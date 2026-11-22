from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, skipUnlessDBFeature
from django.utils import timezone

from pharmacies.models import Pharmacy
from .models import BranchStockTransfer, BranchStockTransferItem, Product, ProductBatch, StockMovement
from .transfer_service import TransferError, execute_bulk_transfer, parse_lines

KEY = 'k' * 24


class BulkTransferServiceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.src = Pharmacy.objects.create(name='Src Branch')
        self.dst = Pharmacy.objects.create(name='Dst Branch')
        self.other = Pharmacy.objects.create(name='Foreign Branch')
        self.user = User.objects.create_user(username='mgr', password='x', role=User.Role.ADMIN)
        self.user.managed_pharmacies.set([self.src, self.dst])
        self.allowed = {self.src.pk, self.dst.pk}
        self.p1 = Product.objects.create(pharmacy=self.src, name='Paracetamol', unit='TABLET', created_by=self.user)
        self.p2 = Product.objects.create(pharmacy=self.src, name='Abitol', unit='TABLET', created_by=self.user)
        self.b1 = self._batch(self.p1, 'B1', 100)
        self.b2 = self._batch(self.p2, 'B2', 50)

    def _batch(self, product, number, qty, expiry=None):
        return ProductBatch.objects.create(
            product=product, batch_number=number, initial_quantity=qty, quantity=qty,
            purchase_price='10.00', selling_price='15.00', expiry_date=expiry, received_by=self.user,
        )

    def _run(self, lines, key=KEY, **kw):
        return execute_bulk_transfer(user=self.user, source=kw.pop('source', self.src), destination=kw.pop('destination', self.dst),
                                     lines=lines, idempotency_key=key, allowed_branch_ids=kw.pop('allowed', self.allowed), **kw)

    def test_many_items_one_reference(self):
        result = self._run([(self.b1.pk, 20), (self.b2.pk, 10)], note='restock')
        t = result.transfer
        self.assertEqual((t.item_count, t.quantity), (2, 30))
        self.assertEqual(BranchStockTransferItem.objects.filter(transfer=t).count(), 2)
        self.b1.refresh_from_db(); self.b2.refresh_from_db()
        self.assertEqual((self.b1.quantity, self.b2.quantity), (80, 40))
        self.assertEqual(StockMovement.objects.filter(note__startswith=t.reference).count(), 4)
        item = t.items.get(source_batch=self.b1)
        self.assertEqual(item.destination_batch.quantity, 20)
        self.assertEqual(str(item.destination_batch.purchase_price), '10.00')
        self.assertEqual(item.source_quantity_after, 80)

    def test_all_or_nothing(self):
        with self.assertRaises(TransferError) as ctx:
            self._run([(self.b1.pk, 20), (self.b2.pk, 51)])
        self.assertIn(self.b2.pk, ctx.exception.line_errors)
        self.b1.refresh_from_db(); self.b2.refresh_from_db()
        self.assertEqual((self.b1.quantity, self.b2.quantity), (100, 50))
        self.assertEqual(BranchStockTransfer.objects.count(), 0)
        self.assertEqual(Product.objects.filter(pharmacy=self.dst).count(), 0)

    def test_expired_batch_rejected(self):
        old = self._batch(self.p1, 'OLD', 5, expiry=timezone.localdate() - timedelta(days=1))
        with self.assertRaises(TransferError):
            self._run([(old.pk, 1)])

    def test_same_branch_and_foreign_branch_rejected(self):
        with self.assertRaises(TransferError):
            self._run([(self.b1.pk, 1)], destination=self.src)
        with self.assertRaises(TransferError):
            self._run([(self.b1.pk, 1)], destination=self.other)

    def test_batch_from_other_branch_rejected(self):
        foreign_product = Product.objects.create(pharmacy=self.other, name='X', unit='TABLET', created_by=self.user)
        fb = self._batch(foreign_product, 'FB', 9)
        with self.assertRaises(TransferError):
            self._run([(fb.pk, 1)])

    def test_idempotent_retry_does_not_double_transfer(self):
        first = self._run([(self.b1.pk, 20)])
        again = self._run([(self.b1.pk, 20)])
        self.assertTrue(again.replayed)
        self.assertEqual(first.transfer.pk, again.transfer.pk)
        self.b1.refresh_from_db()
        self.assertEqual(self.b1.quantity, 80)

    def test_key_cannot_be_reused_for_a_different_destination(self):
        self._run([(self.b1.pk, 5)])
        self.user.managed_pharmacies.add(self.other)
        with self.assertRaises(TransferError):
            self._run([(self.b1.pk, 5)], destination=self.other, allowed=self.allowed | {self.other.pk})

    def test_parse_lines_rejects_duplicates_empty_and_garbage(self):
        with self.assertRaises(TransferError):
            parse_lines(['1', '1'], ['2', '3'])
        with self.assertRaises(TransferError):
            parse_lines([], [])
        with self.assertRaises(TransferError):
            parse_lines(['a'], ['1'])
        self.assertEqual(parse_lines(['1', '2'], ['3', '4']), [(1, 3), (2, 4)])

    def test_zero_and_negative_quantity_rejected(self):
        for qty in (0, -3):
            with self.assertRaises(TransferError):
                self._run([(self.b1.pk, qty)], key=f'z{qty}'.ljust(20, 'z').replace('-', 'n'))

    # ---- V54.87: destination product matching (regression for generic "Transfer haijakamilika" error) ----
    def test_same_name_different_unit_is_a_clear_validation_error(self):
        Product.objects.create(pharmacy=self.dst, name='Paracetamol', unit='BOX', created_by=self.user)
        with self.assertRaises(TransferError) as ctx:
            self._run([(self.b1.pk, 5)])
        self.assertIn(self.b1.pk, ctx.exception.line_errors)
        self.assertIn('different unit', ctx.exception.message)
        self.b1.refresh_from_db()
        self.assertEqual(self.b1.quantity, 100)                      # nothing moved
        self.assertEqual(BranchStockTransfer.objects.count(), 0)     # header rolled back
        self.assertEqual(Product.objects.filter(pharmacy=self.dst, name__iexact='paracetamol').count(), 1)

    def test_same_name_different_case_reuses_destination_product(self):
        existing = Product.objects.create(pharmacy=self.dst, name='PARACETAMOL', unit='TABLET', created_by=self.user)
        result = self._run([(self.b1.pk, 5)])
        item = result.transfer.items.get()
        self.assertEqual(item.destination_batch.product_id, existing.pk)
        self.assertEqual(Product.objects.filter(pharmacy=self.dst).count(), 1)

    def test_inactive_destination_product_is_reactivated(self):
        existing = Product.objects.create(pharmacy=self.dst, name='Paracetamol', unit='TABLET', created_by=self.user, is_active=False)
        self._run([(self.b1.pk, 5)])
        existing.refresh_from_db()
        self.assertTrue(existing.is_active)

    def test_missing_destination_product_is_created(self):
        result = self._run([(self.b1.pk, 5)])
        dst_product = result.transfer.items.get().destination_batch.product
        self.assertEqual((dst_product.pharmacy_id, dst_product.name, dst_product.unit), (self.dst.pk, 'Paracetamol', 'TABLET'))

    @skipUnlessDBFeature('has_select_for_update_of')
    def test_row_locking_works_with_nullable_category_join(self):
        """Regression: on PostgreSQL, SELECT ... FOR UPDATE + select_related(nullable FK) raised
        'FOR UPDATE cannot be applied to the nullable side of an outer join'. Runs on PostgreSQL only
        (SQLite ignores row locks, which is why this slipped through). Product has no category here."""
        self.assertIsNone(self.p1.category_id)
        result = self._run([(self.b1.pk, 5)])
        self.assertEqual(result.transfer.quantity, 5)

    # ---- V54.89: PDF transfer note ----
    def test_transfer_pdf_is_generated_for_a_completed_transfer(self):
        from .transfer_pdf import build_transfer_pdf
        # A name with markup characters must not break the export.
        weird = Product.objects.create(pharmacy=self.src, name='Vitamin <B> & Co', unit='TABLET', created_by=self.user)
        b3 = self._batch(weird, 'W1', 30)
        t = self._run([(self.b1.pk, 20), (b3.pk, 10)], note='Weekend <restock> & top-up').transfer
        items = list(t.items.select_related('destination_batch'))
        pdf = build_transfer_pdf(t, items, sum((i.line_cost for i in items), 0))
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 2000)
