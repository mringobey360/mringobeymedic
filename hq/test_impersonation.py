"""V50.44 platform-owner workspace access. Run: python manage.py test hq.test_impersonation"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from pharmacies.models import Pharmacy
from .impersonation import SESSION_KEY
from .models import ImpersonationSession


class WorkspaceAccessTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.root = User.objects.create_superuser(username='root', password='x', email='r@x.com')
        self.owner = User.objects.create_user(username='owner1', password='x', role=User.Role.ADMIN)
        self.other = User.objects.create_user(username='owner2', password='x', role=User.Role.ADMIN)
        self.branch = Pharmacy.objects.create(name='Kondoa Branch')
        self.owner.managed_pharmacies.add(self.branch)
        self.client.force_login(self.root)

    def _enter(self, mode='VIEW', reason='SUPPORT'):
        return self.client.post(reverse('hq:workspace_enter'), {'pharmacy': self.branch.pk, 'mode': mode, 'reason': reason})

    def test_picker_lists_branch_for_superadmin(self):
        r = self.client.get(reverse('hq:workspace_picker'))
        self.assertContains(r, 'Kondoa Branch')
        self.assertContains(r, 'My workspace')

    def test_superadmin_login_lands_on_picker(self):
        from accounts.views import _post_login_url
        self.assertEqual(_post_login_url(self.root), reverse('hq:workspace_picker'))

    def test_owner_cannot_use_picker_or_enter(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('hq:workspace_picker')).status_code, 403)
        self.assertEqual(self._enter().status_code, 403)
        self.assertFalse(ImpersonationSession.objects.exists())

    def test_enter_creates_audit_and_swaps_user(self):
        r = self._enter()
        self.assertEqual(r.status_code, 302)
        log = ImpersonationSession.objects.get()
        self.assertEqual((log.admin_id, log.target_id, log.pharmacy_name, log.mode), (self.root.pk, self.owner.pk, 'Kondoa Branch', 'VIEW'))
        r = self.client.get(reverse('dashboard:home'))
        self.assertEqual(r.wsgi_request.user.pk, self.owner.pk)
        self.assertEqual(r.wsgi_request.real_user.pk, self.root.pk)
        self.assertEqual(r.wsgi_request.pharmacy.pk, self.branch.pk)

    def test_branch_without_owner_is_refused(self):
        empty = Pharmacy.objects.create(name='Orphan')
        r = self.client.post(reverse('hq:workspace_enter'), {'pharmacy': empty.pk}, follow=True)
        self.assertFalse(ImpersonationSession.objects.exists())

    def test_view_only_blocks_changes(self):
        self._enter('VIEW')
        r = self.client.post(reverse('pharmacies:switch', args=[self.branch.pk]))
        self.assertEqual(r.status_code, 403)

    def test_full_mode_allows_changes_but_not_credentials(self):
        self._enter('FULL')
        r = self.client.post(reverse('pharmacies:switch', args=[self.branch.pk]))
        self.assertNotEqual(r.status_code, 403)
        r = self.client.post(reverse('accounts:change_password'), {})
        self.assertEqual(r.status_code, 403)

    def test_exit_returns_to_own_account_and_closes_log(self):
        self._enter()
        r = self.client.post(reverse('hq:workspace_exit'))
        self.assertEqual(r.status_code, 302)
        log = ImpersonationSession.objects.get()
        self.assertIsNotNone(log.ended_at)
        self.assertEqual(log.end_cause, 'EXIT')
        self.assertEqual(self.client.get(reverse('hq:dashboard')).status_code, 200)

    def test_session_expires(self):
        self._enter()
        ImpersonationSession.objects.update(started_at=timezone.now() - timedelta(minutes=ImpersonationSession.DURATION_MINUTES + 1))
        r = self.client.get(reverse('dashboard:home'))
        self.assertFalse(r.wsgi_request.impersonating)
        log = ImpersonationSession.objects.get()
        self.assertEqual(log.end_cause, 'EXPIRED')
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_deactivated_target_ends_session(self):
        self._enter()
        self.owner.is_active = False
        self.owner.save()
        r = self.client.get(reverse('dashboard:home'))
        self.assertFalse(r.wsgi_request.impersonating)

    def test_superuser_target_is_never_selected(self):
        from .impersonation import pick_owner_for_branch
        self.root.managed_pharmacies.add(self.branch)
        self.assertEqual(pick_owner_for_branch(self.branch).pk, self.owner.pk)
