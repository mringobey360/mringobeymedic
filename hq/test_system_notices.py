"""System notices (in-app status banners). Run: python manage.py test hq.test_system_notices"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import SystemNotice
from .system_notices import link_icon_for, notices_for_user


class SystemNoticeTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.root = User.objects.create_superuser(username='root', password='x', email='r@x.com')
        self.owner = User.objects.create_user(username='owner1', password='x', role=User.Role.ADMIN)
        self.employee = User.objects.create_user(username='emp1', password='x', role=User.Role.EMPLOYEE)

    def _notice(self, **kw):
        # Helper notices are PUBLISHED by default; pass publish_state='DRAFT' for a draft.
        data = dict(kind='INFO', title='Taarifa ya mfumo', message='Maelezo mafupi kwa wateja wote.', created_by=self.root,
                    publish_state='PUBLISHED', published_at=timezone.now())
        data.update(kw)
        return SystemNotice.objects.create(**data)

    # --- who can manage notices -------------------------------------------------
    def test_owner_cannot_open_notice_pages(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('hq:notice_list')).status_code, 403)
        self.assertEqual(self.client.get(reverse('hq:notice_create')).status_code, 403)

    def test_new_notice_is_saved_as_draft_and_customers_cannot_see_it(self):
        self.client.force_login(self.root)
        r = self.client.post(reverse('hq:notice_create'), {
            'kind': 'INCIDENT', 'title': 'Changamoto ya muda', 'message': 'Tunalifanyia kazi kwa makini.',
            'audience': 'ALL', 'action': 'review',
        })
        notice = SystemNotice.objects.get()
        self.assertRedirects(r, reverse('hq:notice_review', args=[notice.pk]))
        self.assertEqual(notice.status, 'DRAFT')
        self.assertEqual(notice.created_by, self.root)
        self.assertEqual(notices_for_user(self.owner), [])

    def test_save_as_draft_returns_to_list(self):
        self.client.force_login(self.root)
        r = self.client.post(reverse('hq:notice_create'), {
            'kind': 'INFO', 'title': 'Rasimu', 'message': 'Maelezo ya rasimu.', 'audience': 'ALL', 'action': 'draft',
        })
        self.assertRedirects(r, reverse('hq:notice_list'))
        self.assertEqual(SystemNotice.objects.get().status, 'DRAFT')

    def test_review_page_shows_preview_and_hides_publish_from_owner(self):
        notice = self._notice(kind='INCIDENT', title='Changamoto ya muda', publish_state='DRAFT', published_at=None)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('hq:notice_review', args=[notice.pk])).status_code, 403)
        self.client.force_login(self.root)
        r = self.client.get(reverse('hq:notice_review', args=[notice.pk]))
        self.assertContains(r, 'Changamoto ya muda')
        self.assertContains(r, 'spn-danger')
        self.assertContains(r, reverse('hq:notice_publish', args=[notice.pk]))

    def test_publish_makes_draft_live(self):
        notice = self._notice(title='Maboresho', publish_state='DRAFT', published_at=None)
        self.assertEqual(notices_for_user(self.owner), [])
        self.client.force_login(self.root)
        r = self.client.post(reverse('hq:notice_publish', args=[notice.pk]))
        self.assertRedirects(r, reverse('hq:notice_list'))
        notice.refresh_from_db()
        self.assertEqual(notice.status, 'LIVE')
        self.assertEqual(notice.published_by, self.root)
        self.assertIsNotNone(notice.published_at)
        self.assertEqual([n['title'] for n in notices_for_user(self.owner)], ['Maboresho'])

    def test_draft_waiting_for_days_starts_when_it_is_published(self):
        notice = self._notice(publish_state='DRAFT', published_at=None, starts_at=timezone.now() - timedelta(days=2))
        before = timezone.now()
        self.client.force_login(self.root)
        self.client.post(reverse('hq:notice_publish', args=[notice.pk]))
        notice.refresh_from_db()
        self.assertGreaterEqual(notice.starts_at, before)

    def test_unfinished_placeholder_blocks_publishing(self):
        notice = self._notice(kind='MAINTENANCE', message='Matengenezo tarehe __ kuanzia saa __.', publish_state='DRAFT', published_at=None)
        self.client.force_login(self.root)
        self.client.post(reverse('hq:notice_publish', args=[notice.pk]))
        notice.refresh_from_db()
        self.assertEqual(notice.status, 'DRAFT')
        self.assertEqual(notices_for_user(self.owner), [])

    def test_expired_end_time_blocks_publishing(self):
        notice = self._notice(publish_state='DRAFT', published_at=None, ends_at=timezone.now() - timedelta(hours=1),
                              starts_at=timezone.now() - timedelta(hours=5))
        self.client.force_login(self.root)
        self.client.post(reverse('hq:notice_publish', args=[notice.pk]))
        notice.refresh_from_db()
        self.assertEqual(notice.status, 'DRAFT')

    def test_draft_is_never_shown_even_with_master_switch_on(self):
        self._notice(title='Rasimu', publish_state='DRAFT', published_at=None, is_active=True, show_on_login=True)
        self.assertEqual(notices_for_user(self.owner), [])
        self.assertNotContains(self.client.get('/'), 'Rasimu')

    def test_editing_a_live_notice_sends_it_back_to_draft_for_review(self):
        notice = self._notice(title='Taarifa ya zamani')
        self.assertEqual(len(notices_for_user(self.owner)), 1)
        self.client.force_login(self.root)
        r = self.client.post(reverse('hq:notice_edit', args=[notice.pk]), {
            'kind': 'INFO', 'title': 'Taarifa mpya', 'message': 'Maelezo mapya kwa wateja.', 'audience': 'ALL',
            'dismissible': 'on', 'action': 'review',
        })
        self.assertRedirects(r, reverse('hq:notice_review', args=[notice.pk]))
        notice.refresh_from_db()
        self.assertEqual(notice.status, 'DRAFT')
        self.assertEqual(notices_for_user(self.owner), [])

    def test_unpublish_hides_notice_immediately(self):
        notice = self._notice()
        self.assertEqual(len(notices_for_user(self.owner)), 1)   # warms the cache
        self.client.force_login(self.root)
        self.client.post(reverse('hq:notice_unpublish', args=[notice.pk]))
        notice.refresh_from_db()
        self.assertEqual(notice.status, 'DRAFT')
        self.assertEqual(notices_for_user(self.owner), [])

    def test_draft_cannot_be_switched_on_or_marked_resolved(self):
        notice = self._notice(kind='INCIDENT', publish_state='DRAFT', published_at=None)
        self.client.force_login(self.root)
        self.client.post(reverse('hq:notice_toggle', args=[notice.pk]))
        self.client.post(reverse('hq:notice_resolve', args=[notice.pk]))
        notice.refresh_from_db()
        self.assertEqual(notice.status, 'DRAFT')
        self.assertEqual(notice.kind, 'INCIDENT')

    def test_previously_live_notice_stays_visible(self):
        # A notice that was already live before the workflow existed is PUBLISHED and keeps showing.
        self._notice(title='Already live')
        self.assertEqual([n['title'] for n in notices_for_user(self.owner)], ['Already live'])

    def test_bad_link_is_rejected(self):
        self.client.force_login(self.root)
        r = self.client.post(reverse('hq:notice_create'), {
            'kind': 'INFO', 'title': 'X', 'message': 'Y', 'audience': 'ALL', 'is_active': 'on',
            'link_url': 'javascript:alert(1)',
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(SystemNotice.objects.exists())

    def test_end_before_start_is_rejected(self):
        self.client.force_login(self.root)
        now = timezone.localtime(timezone.now())
        r = self.client.post(reverse('hq:notice_create'), {
            'kind': 'INFO', 'title': 'X', 'message': 'Y', 'audience': 'ALL', 'is_active': 'on',
            'starts_at': now.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(SystemNotice.objects.exists())

    # --- what customers see -------------------------------------------------------
    def test_scheduled_expired_and_off_notices_are_hidden(self):
        now = timezone.now()
        self._notice(title='Future', starts_at=now + timedelta(hours=2))
        self._notice(title='Past', starts_at=now - timedelta(hours=5), ends_at=now - timedelta(hours=1))
        self._notice(title='Off', is_active=False)
        live = self._notice(title='Live now')
        self.assertEqual([n['title'] for n in notices_for_user(self.owner)], ['Live now'])
        self.assertEqual(live.status, 'LIVE')

    def test_managers_only_audience(self):
        self._notice(title='Owners only', audience='MANAGERS')
        self.assertEqual(len(notices_for_user(self.owner)), 1)
        self.assertEqual(len(notices_for_user(self.employee)), 0)

    def test_anonymous_sees_only_login_page_notices(self):
        from django.contrib.auth.models import AnonymousUser
        self._notice(title='Inside only')
        self._notice(title='Also on login', show_on_login=True)
        self.assertEqual([n['title'] for n in notices_for_user(AnonymousUser())], ['Also on login'])

    def test_incident_is_listed_before_improvement(self):
        self._notice(kind='IMPROVEMENT', title='Maboresho')
        self._notice(kind='INCIDENT', title='Changamoto')
        self.assertEqual([n['kind'] for n in notices_for_user(self.owner)], ['INCIDENT', 'IMPROVEMENT'])

    def test_switching_off_hides_immediately_despite_cache(self):
        notice = self._notice()
        self.assertEqual(len(notices_for_user(self.owner)), 1)   # warms the cache
        self.client.force_login(self.root)
        self.client.post(reverse('hq:notice_toggle', args=[notice.pk]))
        self.assertEqual(len(notices_for_user(self.owner)), 0)

    def test_landing_page_shows_login_notice_to_visitors(self):
        self._notice(title='Matengenezo leo usiku', show_on_login=True)
        r = self.client.get('/')
        self.assertContains(r, 'Matengenezo leo usiku')

    def test_notice_text_is_escaped(self):
        self._notice(title='<script>alert(1)</script>', message='<b>x</b>', show_on_login=True)
        r = self.client.get('/')
        self.assertNotContains(r, '<script>alert(1)</script>')

    # --- one-click follow-ups -------------------------------------------------------
    def test_mark_resolved_turns_incident_into_green_notice_that_expires(self):
        notice = self._notice(kind='INCIDENT', title='Changamoto', dismissible=False)
        self.client.force_login(self.root)
        self.client.post(reverse('hq:notice_resolve', args=[notice.pk]))
        notice.refresh_from_db()
        self.assertEqual(notice.kind, 'RESOLVED')
        self.assertTrue(notice.dismissible)
        self.assertIsNotNone(notice.ends_at)
        self.assertEqual(notice.tone, 'success')

    def test_delete_removes_notice(self):
        notice = self._notice()
        self.client.force_login(self.root)
        self.client.post(reverse('hq:notice_delete', args=[notice.pk]))
        self.assertFalse(SystemNotice.objects.exists())


class LinkIconTests(TestCase):
    def test_icon_follows_button_text(self):
        self.assertEqual(link_icon_for('Anza Sasa'), 'rocket')
        self.assertEqual(link_icon_for('Soma zaidi'), 'book')
        self.assertEqual(link_icon_for('Nunua sasa'), 'cart')
        self.assertEqual(link_icon_for('Piga simu'), 'phone')
        self.assertEqual(link_icon_for('Angalia'), 'arrow')


class NoticeBellDeliveryTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.root = User.objects.create_superuser(username='root2', password='x', email='r2@x.com')
        self.owner = User.objects.create_user(username='own2', password='x', role=User.Role.ADMIN)
        self.employee = User.objects.create_user(username='emp2', password='x', role=User.Role.EMPLOYEE)

    def _published(self, **kw):
        data = dict(kind='MAINTENANCE', title='Matengenezo', message='Mfumo utafanyiwa matengenezo kesho usiku.',
                    created_by=self.root, publish_state='PUBLISHED', published_at=timezone.now(), is_active=True)
        data.update(kw)
        return SystemNotice.objects.create(**data)

    def test_sends_once_to_customers_not_superuser(self):
        from notifications.models import Notification
        from .notice_delivery import send_notice_notifications
        n = self._published()
        self.assertEqual(send_notice_notifications(n), 2)
        self.assertEqual(send_notice_notifications(n), 0)
        self.assertFalse(Notification.objects.filter(recipient=self.root).exists())
        self.assertEqual(Notification.objects.filter(category='SYSTEM', level='WARNING').count(), 2)

    def test_managers_only_audience(self):
        from .notice_delivery import send_notice_notifications
        n = self._published(audience='MANAGERS')
        self.assertEqual(send_notice_notifications(n), 1)

    def test_scheduled_waits_until_start(self):
        from .notice_delivery import send_due_notice_notifications
        n = self._published(starts_at=timezone.now() + timedelta(hours=2))
        self.assertEqual(send_due_notice_notifications(), 0)
        self.assertEqual(send_due_notice_notifications(now=timezone.now() + timedelta(hours=3)), 2)

    def test_option_off_sends_nothing(self):
        from .notice_delivery import send_notice_notifications
        self.assertEqual(send_notice_notifications(self._published(notify_in_app=False)), 0)
