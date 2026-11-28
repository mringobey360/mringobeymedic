from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from . import audit
from .models import InventoryChangeLog


class AuditDiffTests(SimpleTestCase):
    def test_only_changed_fields_are_reported_with_old_and_new_values(self):
        before = {'selling_price': Decimal('1500.00'), 'quantity': 10, 'note': '', 'expiry_date': date(2027, 1, 5)}
        after = {'selling_price': Decimal('1800.00'), 'quantity': 10, 'note': None, 'expiry_date': date(2027, 2, 1)}
        labels = {'selling_price': 'Selling price', 'quantity': 'Quantity', 'note': 'Note', 'expiry_date': 'Expiry'}
        changes = {c['field']: c for c in audit.diff(before, after, labels)}
        self.assertEqual(set(changes), {'selling_price', 'expiry_date'})   # quantity same, note '' vs None is not a change
        self.assertEqual((changes['selling_price']['old'], changes['selling_price']['new']), ('1,500', '1,800'))
        self.assertEqual(changes['expiry_date']['new'], '01 Feb 2027')

    def test_decimal_and_int_with_same_value_are_not_a_change(self):
        self.assertEqual(audit.diff({'quantity': 5}, {'quantity': Decimal('5.00')}, {'quantity': 'Quantity'}), [])

    def test_booleans_read_as_yes_no(self):
        c = audit.diff({'requires_expiry': True}, {'requires_expiry': False}, {'requires_expiry': 'Has expiry date'})[0]
        self.assertEqual((c['old'], c['new']), ('Yes', 'No'))


class AuditRecordsAreAppendOnlyTests(SimpleTestCase):
    def test_existing_record_cannot_be_saved_again(self):
        with self.assertRaises(ValidationError):
            InventoryChangeLog(pk=1, action=InventoryChangeLog.Action.BATCH_EDITED).save()
