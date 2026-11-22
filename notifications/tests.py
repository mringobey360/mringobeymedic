from django.test import TestCase

from accounts.models import ManagerAssistant, User
from pharmacies.models import Pharmacy
from .models import Notification
from .utils import management_recipients
from .views import _safe_notification_link


class NotificationDestinationTests(TestCase):
    def setUp(self):
        self.branch = Pharmacy.objects.create(name='Main', is_active=True)
        self.owner = User.objects.create_user(
            username='owner', password='x', role=User.Role.ADMIN, phone_number='+255700000101')
        self.owner.managed_pharmacies.add(self.branch)
        self.assistant = User.objects.create_user(
            username='asst', password='x', role=User.Role.ASSISTANT, phone_number='+255700000102')
        ManagerAssistant.objects.create(
            manager=self.owner, assistant=self.assistant, pharmacy=self.branch, created_by=self.owner)
        self.employee = User.objects.create_user(
            username='emp', password='x', role=User.Role.EMPLOYEE, phone_number='+255700000103',
            pharmacy=self.branch)
        self.display = User.objects.create_user(
            username='disp', password='x', role=User.Role.BRANCH_DISPLAY, phone_number='+255700000104',
            pharmacy=self.branch)

    def _note(self, recipient, link):
        return Notification.objects.create(
            recipient=recipient, pharmacy=self.branch, category=Notification.Category.SYSTEM,
            title='Late meal request', link_url=link)

    def test_owner_and_assistant_open_meal_issues(self):
        for user in (self.owner, self.assistant):
            url = _safe_notification_link(self._note(user, '/accounts/meal-issues/'), user)
            self.assertTrue(url.startswith('/accounts/meal-issues/'), url)

    def test_employee_is_not_sent_to_manager_only_page(self):
        for link in ('/accounts/meal-issues/', '/accounts/attendance/review/'):
            url = _safe_notification_link(self._note(self.employee, link), self.employee)
            self.assertFalse(url.startswith(link), url)

    def test_display_account_lands_on_kiosk(self):
        url = _safe_notification_link(self._note(self.display, '/accounts/meal-issues/'), self.display)
        self.assertTrue(url.startswith('/accounts/attendance/qr/display/'), url)

    def test_external_links_are_never_followed(self):
        url = _safe_notification_link(self._note(self.owner, 'https://evil.example/x'), self.owner)
        self.assertTrue(url.startswith('/'))
        self.assertNotIn('evil', url)

    def test_attendance_and_meal_alerts_reach_owner_and_assistant(self):
        ids = set(management_recipients(self.branch).values_list('pk', flat=True))
        self.assertEqual(ids, {self.owner.pk, self.assistant.pk})


class AnnouncementLinkSafetyTests(__import__('django.test', fromlist=['SimpleTestCase']).SimpleTestCase):
    """V54.71: only http(s) URLs and in-app paths may be attached to an announcement."""

    def test_allows_https_http_and_in_app_paths(self):
        from .links import clean_action_url
        self.assertEqual(clean_action_url('https://example.com/a?b=1'), 'https://example.com/a?b=1')
        self.assertEqual(clean_action_url('http://example.com'), 'http://example.com')
        self.assertEqual(clean_action_url('/sales/history/'), '/sales/history/')

    def test_rejects_dangerous_or_malformed_links(self):
        from .links import clean_action_url
        for bad in ['javascript:alert(1)', 'data:text/html,x', '//evil.com', 'mailto:a@b.c',
                    'https://user:pw@evil.com', 'https://a.com/x y', '']:
            self.assertEqual(clean_action_url(bad), '', bad)
