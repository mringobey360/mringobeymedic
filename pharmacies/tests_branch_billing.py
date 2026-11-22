from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from pharmacies import branch_billing as bb
from pharmacies.billing_notices import milestone_for

CFG = {'window': 10, 'grace': 5, 'readonly': 25}
TODAY = date(2026, 10, 10)


def _addon_branch(pk, due, status='ACTIVE'):
    addon = SimpleNamespace(status=status, next_billing_date=due, Status=SimpleNamespace(ACTIVE='ACTIVE'))
    return SimpleNamespace(pk=pk, name=f'Branch {pk}', addon=addon)


class ClassifyTests(SimpleTestCase):
    def test_stages(self):
        self.assertEqual(bb.classify(40, CFG), bb.OK)
        self.assertEqual(bb.classify(10, CFG), bb.DUE_SOON)
        self.assertEqual(bb.classify(0, CFG), bb.DUE_SOON)      # due date itself still fully usable
        self.assertEqual(bb.classify(-1, CFG), bb.GRACE)
        self.assertEqual(bb.classify(-5, CFG), bb.GRACE)
        self.assertEqual(bb.classify(-6, CFG), bb.READ_ONLY)
        self.assertEqual(bb.classify(-30, CFG), bb.READ_ONLY)
        self.assertEqual(bb.classify(-31, CFG), bb.LOCKED)
        self.assertEqual(bb.classify(None, CFG), bb.DUE_SOON)


@override_settings(BRANCH_BILLING_RENEWAL_WINDOW_DAYS=10, BRANCH_BILLING_GRACE_DAYS=5, BRANCH_BILLING_READONLY_DAYS=25)
class BranchIsolationTests(SimpleTestCase):
    def setUp(self):
        p = mock.patch('pharmacies.branch_billing.reverse', side_effect=lambda n, args=None: f'/x/{(args or [0])[0]}/')
        p.start(); self.addCleanup(p.stop)

    def test_each_branch_judged_only_by_itself(self):
        lapsed = bb.branch_billing_state(_addon_branch(1, TODAY - timedelta(days=40)), TODAY)
        healthy = bb.branch_billing_state(_addon_branch(2, TODAY + timedelta(days=60)), TODAY)
        self.assertTrue(lapsed['locked'])
        self.assertEqual(healthy['stage'], bb.OK)
        self.assertFalse(healthy['alert'])

    def test_grace_keeps_working_with_warning(self):
        s = bb.branch_billing_state(_addon_branch(3, TODAY - timedelta(days=2)), TODAY)
        self.assertTrue(s['grace']); self.assertFalse(s['read_only']); self.assertFalse(s['locked'])
        self.assertEqual(s['grace_days_left'], 3)

    def test_read_only_then_lock_countdown(self):
        s = bb.branch_billing_state(_addon_branch(4, TODAY - timedelta(days=10)), TODAY)
        self.assertTrue(s['read_only']); self.assertEqual(s['lock_in_days'], 5 + 25 + 1 - 10)

    def test_pending_or_cancelled_addon_is_not_staged(self):
        self.assertIsNone(bb.branch_billing_state(_addon_branch(5, TODAY, status='PENDING_PAYMENT'), TODAY))

    def test_primary_branch_uses_owner_subscription(self):
        ph = SimpleNamespace(pk=9, name='Main', addon=None)
        sub_due = TODAY - timedelta(days=3)
        with mock.patch.object(bb, '_primary_due', return_value=(sub_due, False, object())):
            s = bb.branch_billing_state(ph, TODAY)
        self.assertEqual(s['kind'], 'PRIMARY'); self.assertTrue(s['grace'])

    def test_primary_without_subscription_falls_back_to_legacy(self):
        ph = SimpleNamespace(pk=9, name='Main', addon=None)
        with mock.patch.object(bb, '_primary_due', return_value=(None, False, None)):
            self.assertIsNone(bb.branch_billing_state(ph, TODAY))

    def test_lapsed_lock_is_not_forced_so_management_can_view(self):
        # A branch whose paid period simply ran out is locked but NOT forced: the middleware opens it view-only.
        s = bb.branch_billing_state(_addon_branch(1, TODAY - timedelta(days=40)), TODAY)
        self.assertTrue(s['locked']); self.assertFalse(s['forced'])

    def test_deliberate_lock_is_forced_and_never_view_only(self):
        ph = SimpleNamespace(pk=9, name='Main', addon=None)
        with mock.patch.object(bb, '_primary_due', return_value=(TODAY + timedelta(days=20), True, object())):
            s = bb.branch_billing_state(ph, TODAY)
        self.assertTrue(s['locked']); self.assertTrue(s['forced'])

    def test_manually_expired_primary_locks_immediately(self):
        ph = SimpleNamespace(pk=9, name='Main', addon=None)
        with mock.patch.object(bb, '_primary_due', return_value=(TODAY + timedelta(days=20), True, object())):
            self.assertTrue(bb.branch_billing_state(ph, TODAY)['locked'])


@override_settings(BRANCH_BILLING_RENEWAL_WINDOW_DAYS=10, BRANCH_BILLING_GRACE_DAYS=5, BRANCH_BILLING_READONLY_DAYS=25)
class MilestoneTests(SimpleTestCase):
    def setUp(self):
        p = mock.patch('pharmacies.branch_billing.reverse', side_effect=lambda n, args=None: '/x/')
        p.start(); self.addCleanup(p.stop)

    def ms(self, offset):
        return milestone_for(bb.branch_billing_state(_addon_branch(1, TODAY + timedelta(days=offset)), TODAY), CFG)

    def test_milestones(self):
        self.assertEqual(self.ms(30), '')
        self.assertEqual(self.ms(10), 'due-10')
        self.assertEqual(self.ms(4), 'due-10')
        self.assertEqual(self.ms(3), 'due-3')
        self.assertEqual(self.ms(1), 'due-1')
        self.assertEqual(self.ms(0), 'due-0')
        self.assertEqual(self.ms(-1), 'grace')
        self.assertEqual(self.ms(-5), 'grace-last')
        self.assertEqual(self.ms(-6), 'readonly')
        self.assertEqual(self.ms(-31), 'locked')
