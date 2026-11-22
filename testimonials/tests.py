"""Run: python manage.py test testimonials

Covers the rules that matter: nothing shows without admin authorisation, reviews are Pending
until a superadmin approves them, and only approved reviews reach the public landing page.
"""
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts import device_pin
from .models import (TestimonialAuditLog, TestimonialInvitation, TestimonialReview,
                     TestimonialSettings)

User = get_user_model()


class BaseCase(TestCase):
    def setUp(self):
        cache.clear()
        p = mock.patch.object(device_pin, 'needs_pin_setup', return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.root = User.objects.create_superuser(username='root', password='x', email='r@x.com')
        self.owner = User.objects.create_user(username='owner1', password='x', role=User.Role.ADMIN,
                                              first_name='Asha')
        self.other = User.objects.create_user(username='owner2', password='x', role=User.Role.ADMIN)
        self.emp = User.objects.create_user(username='emp1', password='x', role=User.Role.EMPLOYEE)
        self.cfg = TestimonialSettings.load()

    def popup(self, user):
        self.client.force_login(user)
        return self.client.get(reverse('testimonials:popup'), HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()

    def submit(self, user, **over):
        self.client.force_login(user)
        body = {'rating': 5, 'comment': 'Stock and cash control are much clearer now.', 'consent': True,
                'display_name': 'Asha M.', 'business_name': 'Asha Pharmacy'}
        body.update(over)
        return self.client.post(reverse('testimonials:submit'), data=json.dumps(body),
                                content_type='application/json')

    def authorize(self, *users):
        self.client.force_login(self.root)
        return self.client.post(reverse('testimonials:admin_invite'), {'customers': [u.pk for u in users]})


class PopupGatingTests(BaseCase):
    def test_no_invitation_no_popup(self):
        self.cfg.enabled = True
        self.cfg.save()
        self.assertFalse(self.popup(self.owner)['show'])

    def test_authorised_but_feature_disabled_shows_nothing(self):
        self.authorize(self.owner)
        self.assertFalse(self.popup(self.owner)['show'])

    def test_only_chosen_customer_sees_popup(self):
        self.cfg.enabled = True
        self.cfg.save()
        self.authorize(self.owner)
        self.assertTrue(self.popup(self.owner)['show'])
        self.assertFalse(self.popup(self.other)['show'])

    def test_superadmin_never_sees_customer_popup(self):
        self.cfg.enabled = True
        self.cfg.save()
        self.assertFalse(self.popup(self.root)['show'])

    def test_later_hides_popup_until_snooze_ends(self):
        self.cfg.enabled = True
        self.cfg.save()
        self.authorize(self.owner)
        self.client.force_login(self.owner)
        self.client.post(reverse('testimonials:later'))
        self.assertFalse(self.popup(self.owner)['show'])


class AudienceTests(BaseCase):
    def test_employee_can_be_chosen_and_sees_popup(self):
        self.cfg.enabled = True
        self.cfg.save()
        self.authorize(self.emp)
        self.assertTrue(self.popup(self.emp)['show'])
        self.assertFalse(self.popup(self.owner)['show'])

    def test_authorise_all_by_role_filter(self):
        self.client.force_login(self.root)
        self.client.post(reverse('testimonials:admin_invite'), {'scope': 'filter', 'role': 'EMPLOYEE'})
        self.assertEqual(TestimonialInvitation.objects.filter(customer=self.emp).count(), 1)
        self.assertEqual(TestimonialInvitation.objects.filter(customer=self.owner).count(), 0)

    def test_authorise_everyone(self):
        self.client.force_login(self.root)
        self.client.post(reverse('testimonials:admin_invite'), {'scope': 'filter'})
        self.assertEqual(TestimonialInvitation.objects.count(), 3)  # owner, other owner, employee; never superuser

    def test_employee_review_records_role(self):
        self.cfg.enabled = True
        self.cfg.save()
        self.authorize(self.emp)
        self.submit(self.emp)
        self.assertEqual(TestimonialReview.objects.get().reviewer_role, 'Shop Employee')


class SubmissionTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.cfg.enabled = True
        self.cfg.save()
        self.authorize(self.owner)

    def test_submit_creates_pending_review_and_closes_invitation(self):
        r = self.submit(self.owner)
        self.assertEqual(r.status_code, 200)
        review = TestimonialReview.objects.get()
        self.assertEqual(review.status, TestimonialReview.Status.PENDING)
        self.assertEqual(review.invitation.status, TestimonialInvitation.Status.ANSWERED)
        self.assertFalse(self.popup(self.owner)['show'])

    def test_double_submit_creates_one_review(self):
        self.submit(self.owner)
        r = self.submit(self.owner)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(TestimonialReview.objects.count(), 1)

    def test_uninvited_customer_cannot_submit(self):
        r = self.submit(self.other)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(TestimonialReview.objects.count(), 0)

    def test_rating_and_comment_validation(self):
        self.assertEqual(self.submit(self.owner, rating=9).status_code, 400)
        self.assertEqual(self.submit(self.owner, rating=0).status_code, 400)
        self.assertEqual(self.submit(self.owner, comment='short').status_code, 400)
        self.assertEqual(TestimonialReview.objects.count(), 0)

    def test_name_hidden_without_consent(self):
        self.submit(self.owner, consent=False)
        review = TestimonialReview.objects.get()
        self.assertEqual(review.public_name, 'Verified SmartPOS customer')
        self.assertEqual(review.public_business, '')

    def test_decline_closes_invitation(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('testimonials:decline'))
        self.assertEqual(TestimonialInvitation.objects.get().status, TestimonialInvitation.Status.DECLINED)
        self.assertEqual(self.submit(self.owner).status_code, 409)


class AdminPermissionTests(BaseCase):
    def test_customer_cannot_open_admin_or_moderate(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('testimonials:admin')).status_code, 403)
        self.assertEqual(self.client.post(reverse('testimonials:admin_invite'),
                                          {'customers': [self.owner.pk]}).status_code, 403)
        self.assertEqual(TestimonialInvitation.objects.count(), 0)

    def test_superadmin_can_open_every_tab(self):
        self.client.force_login(self.root)
        for tab in ('reviews', 'customers', 'invitations', 'audit'):
            self.assertEqual(self.client.get(reverse('testimonials:admin'), {'tab': tab}).status_code, 200)

    def test_one_open_invitation_per_customer(self):
        self.authorize(self.owner)
        self.authorize(self.owner)
        self.assertEqual(TestimonialInvitation.objects.filter(customer=self.owner).count(), 1)

    def test_revoke_hides_popup(self):
        self.cfg.enabled = True
        self.cfg.save()
        self.authorize(self.owner)
        inv = TestimonialInvitation.objects.get()
        self.client.force_login(self.root)
        self.client.post(reverse('testimonials:admin_revoke', args=[inv.pk]))
        self.assertFalse(self.popup(self.owner)['show'])


class ModerationAndLandingTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.cfg.enabled = True
        self.cfg.save()
        self.authorize(self.owner)
        self.submit(self.owner, comment='Unique praise text for the landing page.')
        self.review = TestimonialReview.objects.get()

    def landing(self):
        self.client.logout()
        return self.client.get(reverse('landing')).content.decode()

    def test_pending_review_not_public(self):
        self.assertNotIn('Unique praise text', self.landing())

    def test_approve_publishes_and_unpublish_hides(self):
        self.client.force_login(self.root)
        self.client.post(reverse('testimonials:admin_moderate', args=[self.review.pk, 'approve']))
        self.assertIn('Unique praise text', self.landing())
        self.client.force_login(self.root)
        self.client.post(reverse('testimonials:admin_moderate', args=[self.review.pk, 'unpublish']))
        self.assertNotIn('Unique praise text', self.landing())

    def test_rejected_review_not_public_and_audited(self):
        self.client.force_login(self.root)
        self.client.post(reverse('testimonials:admin_moderate', args=[self.review.pk, 'reject']))
        self.assertNotIn('Unique praise text', self.landing())
        self.assertTrue(TestimonialAuditLog.objects.filter(action='REVIEW_REJECT', actor=self.root).exists())

    def test_customer_cannot_self_approve(self):
        self.client.force_login(self.owner)
        r = self.client.post(reverse('testimonials:admin_moderate', args=[self.review.pk, 'approve']))
        self.assertEqual(r.status_code, 403)
        self.review.refresh_from_db()
        self.assertEqual(self.review.status, TestimonialReview.Status.PENDING)
