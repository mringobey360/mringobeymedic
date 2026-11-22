from datetime import timedelta
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from accounts import account_deletion as svc
from accounts.models import User


def _manager(**kw):
    defaults = dict(pk=5, username='boss', role=User.Role.ADMIN, phone_number='+255712345678')
    defaults.update(kw)
    return User(**defaults)


class EligibilityTests(SimpleTestCase):
    def test_manager_with_phone_may_delete(self):
        with mock.patch.object(svc, 'shared_branch_names', return_value=[]):
            self.assertIsNone(svc.deletion_blocker(_manager()))

    def test_superadmin_blocked(self):
        self.assertIn('Platform administrator', svc.deletion_blocker(_manager(is_superuser=True)))

    def test_employee_assistant_and_display_blocked(self):
        for role in (User.Role.EMPLOYEE, User.Role.ASSISTANT, User.Role.BRANCH_DISPLAY):
            self.assertIn('Only the business owner', svc.deletion_blocker(_manager(role=role)))

    def test_phone_required_for_the_code(self):
        self.assertIn('phone number', svc.deletion_blocker(_manager(phone_number=None)))

    def test_platform_delete_does_not_need_a_phone_number(self):
        with mock.patch.object(svc, 'shared_branch_names', return_value=[]):
            self.assertIsNone(svc.deletion_blocker(_manager(phone_number=None), by_platform=True))

    def test_platform_delete_still_protects_superadmins_and_staff(self):
        self.assertIn('Platform administrator', svc.deletion_blocker(_manager(is_superuser=True), by_platform=True))
        self.assertIn('Only the business owner', svc.deletion_blocker(_manager(role=User.Role.EMPLOYEE), by_platform=True))

    def test_branch_shared_with_another_owner_no_longer_blocks(self):
        self.assertIsNone(svc.deletion_blocker(_manager()))


class CodeTests(SimpleTestCase):
    def _row(self, user, code='123456', **kw):
        row = mock.Mock(pk=1, attempts=0, expires_at=timezone.now() + timedelta(minutes=5),
                        code_hash=svc._hash_code(user, code))
        for k, v in kw.items():
            setattr(row, k, v)
        return row

    def _patched(self, row):
        patcher = mock.patch.object(svc, 'AccountDeletionRequest')
        model = patcher.start()
        self.addCleanup(patcher.stop)
        model.objects.filter.return_value.order_by.return_value.first.return_value = row
        model.objects.filter.return_value.update.return_value = 1
        return model

    def test_hash_is_keyed_per_user_and_code(self):
        self.assertEqual(svc._hash_code(_manager(), '111111'), svc._hash_code(_manager(), '111111'))
        self.assertNotEqual(svc._hash_code(_manager(), '111111'), svc._hash_code(_manager(pk=6), '111111'))
        self.assertNotEqual(svc._hash_code(_manager(), '111111'), svc._hash_code(_manager(), '222222'))

    def test_correct_code_accepted(self):
        user = _manager()
        self._patched(self._row(user))
        self.assertTrue(svc.verify_code(user, '123456'))

    def test_wrong_code_rejected(self):
        user = _manager()
        self._patched(self._row(user))
        with self.assertRaises(svc.DeletionError):
            svc.verify_code(user, '000000')

    def test_non_numeric_or_short_code_rejected(self):
        user = _manager()
        self._patched(self._row(user))
        for bad in ('', 'abcdef', '12345', '1234567'):
            with self.assertRaises(svc.DeletionError):
                svc.verify_code(user, bad)

    def test_expired_code_rejected(self):
        user = _manager()
        self._patched(self._row(user, expires_at=timezone.now() - timedelta(seconds=1)))
        with self.assertRaisesMessage(svc.DeletionError, 'expired'):
            svc.verify_code(user, '123456')

    def test_no_code_requested_rejected(self):
        self._patched(None)
        with self.assertRaises(svc.DeletionError):
            svc.verify_code(_manager(), '123456')

    def test_attempt_limit_blocks_even_the_right_code(self):
        user = _manager()
        self._patched(self._row(user, attempts=svc.MAX_ATTEMPTS))
        with self.assertRaisesMessage(svc.DeletionError, 'Too many wrong attempts'):
            svc.verify_code(user, '123456')

    def test_already_used_code_rejected(self):
        user = _manager()
        model = self._patched(self._row(user))
        model.objects.filter.return_value.update.return_value = 0  # someone else consumed it first
        with self.assertRaisesMessage(svc.DeletionError, 'already used'):
            svc.verify_code(user, '123456')


class HelperTests(SimpleTestCase):
    def test_mask_phone(self):
        self.assertEqual(svc.mask_phone('+255712345678'), '+255\u2022\u2022\u2022\u2022678')
        self.assertEqual(svc.mask_phone(''), '')
        self.assertEqual(svc.mask_phone(None), '')

    def test_protect_foreign_keys_are_discovered_automatically(self):
        found = {(m.__name__, n) for m, n in svc._protect_referrers(User)}
        self.assertIn(('Expense', 'submitted_by'), found)
        self.assertIn(('PayrollItem', 'employee'), found)
        from pharmacies.models import Pharmacy
        found_ph = {(m.__name__, n) for m, n in svc._protect_referrers(Pharmacy)}
        self.assertIn(('CashControlReset', 'pharmacy'), found_ph)
        self.assertIn(('ManagerAssistant', 'pharmacy'), found_ph)


class KnownChainSchemaTests(SimpleTestCase):
    """The ordered pre-deletes rely on these fields existing; fail loudly if a model changes."""

    def test_fields_used_by_known_chains_exist(self):
        from expenses.models import CashAuditEvent, CashCount, CashDay, Expense
        from inventory.models import BranchStockTransfer
        from sales.models import SaleItem
        CashCount._meta.get_field('supersedes')
        CashDay._meta.get_field('carried_from')
        CashAuditEvent._meta.get_field('cash_day')
        Expense._meta.get_field('pharmacy')
        BranchStockTransfer._meta.get_field('source_pharmacy')
        BranchStockTransfer._meta.get_field('destination_pharmacy')
        SaleItem._meta.get_field('product')


class PlatformDeletionTests(SimpleTestCase):
    def _admin(self, **kw):
        defaults = dict(pk=1, username='root', role=User.Role.ADMIN, is_superuser=True, phone_number='+255700000001')
        defaults.update(kw)
        u = User(**defaults)
        return u

    def test_code_is_bound_to_the_target_account(self):
        admin, a, b = self._admin(), _manager(pk=5), _manager(pk=6)
        self.assertNotEqual(svc._hash_code(admin, '123456', a), svc._hash_code(admin, '123456', b))
        self.assertNotEqual(svc._hash_code(admin, '123456', a), svc._hash_code(admin, '123456'))

    def test_admin_needs_own_phone(self):
        self.assertIn('no phone number', svc.platform_deletion_blocker(self._admin(phone_number=None), _manager()))

    def test_cannot_delete_superadmin_or_self(self):
        admin = self._admin()
        self.assertIn('protected', svc.platform_deletion_blocker(admin, self._admin(pk=2)))
        self.assertIn('currently in use', svc.platform_deletion_blocker(admin, admin))

    def test_only_superadmin_may_start(self):
        self.assertIn('platform administrator', svc.platform_deletion_blocker(_manager(), _manager(pk=9)).lower())

    def test_owner_deletable_by_admin_without_owner_phone(self):
        with mock.patch.object(svc, 'shared_branch_names', return_value=[]):
            self.assertIsNone(svc.platform_deletion_blocker(self._admin(), _manager(pk=7, phone_number=None)))


class HiddenForeignKeyTests(SimpleTestCase):
    def test_modeled_columns_are_known_and_unmodeled_ones_are_not(self):
        known = svc._modeled_fk_columns()
        self.assertIn(('accounts_user', 'pharmacy_id'), known)
        # the drifted production column is not declared by any model
        self.assertNotIn(('dashboard_dashboardpreference', 'pharmacy_id'), known)
