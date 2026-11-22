from django.conf import settings
from django.db import models
import uuid
from decimal import Decimal
from django.db import transaction
from django.db.models.functions import Lower
from accounts.phone_utils import normalize_phone_number
from django.utils import timezone

from inventory.models import Product, ProductBatch


class LateEntryReason(models.TextChoices):
    """Why a sale was typed in after the day it really happened."""
    PAPER_NOTEBOOK = 'PAPER_NOTEBOOK', 'Recorded in paper notebook'
    NO_PHONE_OR_DEVICE = 'NO_DEVICE', 'No phone / device available'
    SYSTEM_PROBLEM = 'SYSTEM_PROBLEM', 'System or internet problem'
    FORGOT = 'FORGOT', 'Forgot to record it'
    OTHER = 'OTHER', 'Other (explain in the note)'


class Sale(models.Model):
    class PaymentMethod(models.TextChoices):
        CASH = 'CASH', 'Cash'
        MOBILE_MONEY = 'MOBILE_MONEY', 'Mobile Money'
        DEBT = 'DEBT', 'Debt / Credit'
        # One customer, two tenders: part cash, part mobile money (amounts in cash_paid / mobile_paid).
        SPLIT = 'SPLIT', 'Cash + Mobile Money'

    class Status(models.TextChoices):
        COMPLETED = 'COMPLETED', 'Completed'
        PARTIALLY_RETURNED = 'PARTIALLY_RETURNED', 'Partially returned'
        VOIDED = 'VOIDED', 'Voided'

    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='sales')
    sale_number = models.PositiveIntegerField(
        null=True, blank=True, editable=False,
        help_text="Sequential number scoped to this branch only — branch #1's first sale is always #1, "
                  "regardless of how many sales other branches have made. Assigned automatically.",
    )
    served_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='sales')
    payment_method = models.CharField(max_length=15, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    # Only meaningful when payment_method == SPLIT (cash_paid + mobile_paid == the sale total at checkout).
    cash_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    mobile_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    customer_name = models.CharField(max_length=150, blank=True, help_text="Required for Debt sales.")
    customer_phone = models.CharField(max_length=20, blank=True)
    customer_contact = models.ForeignKey(
        'customer_registry.CustomerContact',
        null=True, blank=True, on_delete=models.SET_NULL,
        related_name='sales',
        help_text='Optional master customer contact. Sale name/phone remain a historical snapshot.'
    )
    receipt_access_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    receipt_sms_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    receipt_sms_sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                             related_name='receipt_sms_sent_sales', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # Business date is the accounting date of the sale. It is normally today's
    # Tanzania business date, but offline POS sales preserve the date on which
    # the cashier actually made the sale instead of silently moving it to the
    # date when the device reconnects.
    business_date = models.DateField(default=timezone.localdate, db_index=True)

    # A sale is never deleted. Mistakes are corrected through an approved
    # return/void (see SaleReturnRequest) so the audit trail always survives.
    # SaleItem.quantity is the NET quantity still counted as sold, which keeps
    # every revenue/profit/best-seller aggregate correct without extra filters.
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED, db_index=True)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)

    # Offline sales support: a sale made while the device had no connection is
    # queued locally (IndexedDB) with a client-generated UUID, then submitted
    # to the server once the connection returns. client_uuid makes re-submission
    # safe (idempotent) if the sync request is retried. needs_review flags a
    # sale the server couldn't fully honor against real stock at sync time
    # (e.g. another device sold the same batch while this one was offline) —
    # the sale is NEVER silently dropped or oversold; it's queued for the
    # owner to resolve manually instead.
    client_uuid = models.CharField(max_length=64, unique=True, null=True, blank=True,
                                    help_text="Client-generated ID for offline sales, used to prevent duplicate sync.")
    synced_offline = models.BooleanField(default=False, help_text="True if this sale was made offline and synced later.")
    # Late entry: a sale that really happened on an EARLIER day (e.g. written in the paper
    # notebook because the phone/system was unavailable) and is typed into the system later.
    # business_date = the day the sale truly happened (drives every report, the cash drawer
    # and cash control); created_at = when it was actually entered (kept as the audit trail).
    is_late_entry = models.BooleanField(default=False, db_index=True,
        help_text="Entered in the system AFTER the day it was really made (paper record).")
    late_entry_reason = models.CharField(max_length=40, blank=True,
        help_text="Why it was not recorded on the day (see LateEntryReason).")
    late_entry_note = models.CharField(max_length=300, blank=True,
        help_text="Free text: notebook page / paper receipt number / explanation.")
    needs_review = models.BooleanField(default=False, help_text="True if stock couldn't fully cover this sale at sync time.")
    review_note = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='sales_reviewed')

    @property
    def late_entry_reason_label(self):
        """Readable reason for a late entry ('' for ordinary sales)."""
        if not self.late_entry_reason:
            return ''
        return dict(LateEntryReason.choices).get(self.late_entry_reason, self.late_entry_reason)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['pharmacy', 'sale_number'], name='unique_sale_number_per_pharmacy'),
        ]
        indexes = [
            # Every dashboard/report/sales-history query filters by pharmacy
            # AND a created_at range, then sorts by created_at. Without this,
            # Postgres has to scan and sort the whole table once it grows
            # past a few tens of thousands of rows.
            models.Index(fields=['pharmacy', '-created_at'], name='sale_pharmacy_created_idx'),
            models.Index(fields=['served_by', '-created_at'], name='sale_served_by_created_idx'),
        ]

    def save(self, *args, **kwargs):
        if self.customer_phone:
            self.customer_phone = normalize_phone_number(self.customer_phone)

        if self._state.adding and self.sale_number is None and self.pharmacy_id:
            # select_for_update locks this branch's counter row for the
            # duration of the transaction, so two simultaneous sales at the
            # same branch can never be handed the same number.
            from pharmacies.models import Pharmacy
            with transaction.atomic():
                pharmacy = Pharmacy.objects.select_for_update().get(pk=self.pharmacy_id)
                pharmacy.last_sale_number += 1
                pharmacy.save(update_fields=['last_sale_number'])
                self.sale_number = pharmacy.last_sale_number
                super().save(*args, **kwargs)
            return
        super().save(*args, **kwargs)

    @property
    def total_amount(self):
        return sum(item.subtotal for item in self.items.all())

    @property
    def total_profit(self):
        return sum(item.profit for item in self.items.all())

    @property
    def payment_summary(self):
        """Human text for receipts / lists, e.g. 'Cash 6,000 + Mobile Money 4,000'."""
        if self.payment_method == self.PaymentMethod.SPLIT:
            return f'Cash {self.cash_paid:,.0f} + Mobile Money {self.mobile_paid:,.0f}'
        if self.payment_method == self.PaymentMethod.DEBT:
            paid = self.paid_at_sale
            if paid > 0:
                return f'Credit: paid {paid:,.0f} now, {self.credit_amount:,.0f} on credit'
        return self.get_payment_method_display()

    @property
    def paid_at_sale(self):
        """Money actually received while making this sale (the rest of a credit sale is a loan)."""
        if self.payment_method == self.PaymentMethod.DEBT:
            try:
                return self.debt.deposit_paid
            except Exception:  # noqa: BLE001 - no Debt row (should not happen) => nothing was paid
                return Decimal('0')
        if self.payment_method == self.PaymentMethod.SPLIT:
            return self.cash_paid + self.mobile_paid
        return self.total_amount

    @property
    def credit_amount(self):
        """Part of this sale that was given on credit (0 for cash / mobile / split sales)."""
        if self.payment_method != self.PaymentMethod.DEBT:
            return Decimal('0')
        try:
            return self.debt.credit_given
        except Exception:  # noqa: BLE001
            return self.total_amount

    @property
    def original_total(self):
        """What the sale totalled before any approved returns."""
        return sum(item.original_subtotal for item in self.items.all())

    @property
    def returned_total(self):
        return self.original_total - self.total_amount

    @property
    def is_voided(self):
        return self.status == self.Status.VOIDED

    def clean(self):
        super().clean()
        if self.customer_phone:
            try:
                self.customer_phone = normalize_phone_number(self.customer_phone)
            except Exception as exc:
                from django.core.exceptions import ValidationError
                raise ValidationError({'customer_phone': str(exc)})

    def __str__(self):
        return f"Sale #{self.pk} - {self.created_at:%Y-%m-%d %H:%M}"


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='sale_items')
    batch = models.ForeignKey(ProductBatch, on_delete=models.PROTECT, null=True, related_name='sale_items',
                               help_text="The specific batch this quantity was drawn from (FEFO).")
    quantity = models.PositiveIntegerField(help_text="NET quantity still counted as sold (original quantity minus approved returns).")
    returned_quantity = models.PositiveIntegerField(default=0, help_text="Total quantity returned/voided through approved return requests.")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, help_text="Final selling price per unit after discount.")
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, help_text="Purchase price at time of sale.")
    original_unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Selling price before discount.")
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Discount percentage granted on this line.")
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Total discount amount granted on this line.")
    vip_discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="VIP loyalty discount total, recorded separately for audit.")

    @property
    def subtotal(self):
        return self.unit_price * self.quantity

    @property
    def original_quantity(self):
        return self.quantity + self.returned_quantity

    @property
    def original_subtotal(self):
        return self.unit_price * self.original_quantity

    @property
    def profit(self):
        return (self.unit_price - self.unit_cost) * self.quantity

    def __str__(self):
        return f"{self.product.name} x{self.quantity}"


class Debt(models.Model):
    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='debts')
    sale = models.OneToOneField(Sale, on_delete=models.CASCADE, related_name='debt')
    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=20, blank=True)
    amount_owed = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_settled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            # debt_list_view filters/orders by (pharmacy, is_settled, -created_at).
            models.Index(fields=['pharmacy', 'is_settled', '-created_at'], name='debt_pharmacy_settled_idx'),
            # customer_debt_lookup_api does customer_name__iexact — a plain
            # index doesn't help a case-insensitive match, so this indexes
            # LOWER(customer_name) directly (Postgres uses it for iexact).
            models.Index(Lower('customer_name'), name='debt_customer_name_lower_idx'),
        ]

    @property
    def balance(self):
        return self.amount_owed - self.amount_paid

    @property
    def deposit_paid(self):
        """Money the customer paid AT THE TILL when the credit sale was made (never more than the sale value)."""
        total = sum((p.amount for p in self.payments.all() if p.kind == DebtPayment.Kind.DEPOSIT), Decimal('0'))
        return min(total, self.amount_owed)

    @property
    def repaid(self):
        """Money received AFTER the sale (instalments / settlements), excluding the deposit paid at the till."""
        return max(self.amount_paid - self.deposit_paid, Decimal('0'))

    @property
    def credit_given(self):
        """The part of the sale that was really lent: sale value minus the deposit paid at the till."""
        return self.amount_owed - self.deposit_paid

    def clean(self):
        super().clean()
        if self.customer_phone:
            try:
                self.customer_phone = normalize_phone_number(self.customer_phone)
            except Exception as exc:
                from django.core.exceptions import ValidationError
                raise ValidationError({'customer_phone': str(exc)})
        if self.amount_paid > self.amount_owed:
            from django.core.exceptions import ValidationError
            raise ValidationError({'amount_paid': 'Amount paid cannot exceed amount owed.'})

    def save(self, *args, **kwargs):
        if self.customer_phone:
            self.customer_phone = normalize_phone_number(self.customer_phone)
        super().save(*args, **kwargs)

    @property
    def receipt_number(self):
        return f"PM-{self.pharmacy_id}-{(self.sale.sale_number or self.sale_id):08d}"

    def __str__(self):
        return f"{self.customer_name} owes {self.balance}"


class DebtPayment(models.Model):
    """One repayment received against a Debt (lets reports show what was collected on a given day)."""
    debt = models.ForeignKey(Debt, on_delete=models.CASCADE, related_name='payments')
    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='debt_payments')
    class Method(models.TextChoices):
        CASH = 'CASH', 'Cash (into the drawer)'
        MOBILE_MONEY = 'MOBILE_MONEY', 'Mobile money / bank (not in the drawer)'
        # Repayments recorded before V54.91 had no method. They are kept OUT of the cash ledger so
        # past closed days and their physical counts are never rewritten.
        LEGACY = 'LEGACY', 'Recorded before payment methods existed'

    class Kind(models.TextChoices):
        # Paid at the till while the credit sale was being made (e.g. bought 30,000, paid 10,000 now).
        DEPOSIT = 'DEPOSIT', 'Paid at sale (deposit)'
        # Any later payment against the remaining balance.
        REPAYMENT = 'REPAYMENT', 'Repayment'

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=15, choices=Method.choices, default=Method.CASH, db_index=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.REPAYMENT, db_index=True)
    note = models.CharField(max_length=200, blank=True, help_text='Optional reference (mobile money code, who brought it...).')
    paid_at = models.DateTimeField(default=timezone.now, db_index=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='recorded_debt_payments')

    class Meta:
        ordering = ['-paid_at']
        indexes = [models.Index(fields=['pharmacy', 'paid_at'], name='debtpay_pharm_paid_idx')]

    def __str__(self):
        return f'{self.debt.customer_name} paid {self.amount}'


class SaleReturnRequest(models.Model):
    """A request to return/void part or all of a sale (wrong item, wrong quantity, wrong customer...).

    Employees can only REQUEST; a Shop Owner/Supervisor approves or rejects. An
    owner acting directly creates an already-approved request, so every stock
    and revenue correction — whoever made it — leaves the same audit record.
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending approval'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    class Disposition(models.TextChoices):
        RESTOCK = 'RESTOCK', 'Return to stock'
        WRITE_OFF = 'WRITE_OFF', 'Write off (damaged / cannot be resold)'

    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='sale_return_requests')
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='return_requests')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    reason = models.TextField()
    is_full_void = models.BooleanField(default=False, help_text="True when the request covers everything still counted in the sale.")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='sale_returns_requested')
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_returns_decided')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    disposition = models.CharField(max_length=12, choices=Disposition.choices, default=Disposition.RESTOCK)
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Value of the returned items at the price the customer was charged.")
    refund_due = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Cash/mobile money to hand back to the customer (already-paid part only).")

    class Meta:
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['pharmacy', 'status', '-requested_at'], name='salereturn_pharm_status_idx'),
        ]

    @property
    def total_value(self):
        return sum(line.value for line in self.lines.all())

    def __str__(self):
        return f"Return request #{self.pk} for sale {self.sale_id} ({self.status})"


class SaleReturnLine(models.Model):
    request = models.ForeignKey(SaleReturnRequest, on_delete=models.CASCADE, related_name='lines')
    sale_item = models.ForeignKey(SaleItem, on_delete=models.CASCADE, related_name='return_lines')
    quantity = models.PositiveIntegerField()

    @property
    def value(self):
        return self.sale_item.unit_price * self.quantity

    def __str__(self):
        return f"{self.sale_item.product.name} x{self.quantity}"
