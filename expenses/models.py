from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

class ExpenseCategory(models.Model):
    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, null=True, blank=True, related_name='expense_categories')
    name = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        constraints = [models.UniqueConstraint(fields=['pharmacy', 'name'], name='unique_expense_category_per_pharmacy')]

    def __str__(self):
        return self.name

class CashDay(models.Model):
    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        DAY_ENDED = 'DAY_ENDED', 'Day ended — awaiting physical close'
        CLOSED = 'CLOSED', 'Closed'

    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='cash_days')
    date = models.DateField(default=timezone.localdate)
    class OpeningSource(models.TextChoices):
        MANUAL = 'MANUAL', 'Manual opening'
        CARRY_FORWARD = 'CARRY_FORWARD', 'Carried forward from previous closing'
        OVERRIDE = 'OVERRIDE', 'Manager override'

    opening_cash = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    opening_note = models.CharField(max_length=255, blank=True)
    opening_source = models.CharField(max_length=20, choices=OpeningSource.choices, default=OpeningSource.MANUAL)
    carried_from = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='carried_to_days')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    actual_closing_cash = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    physical_counted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='verified_cash_counts')
    physical_counted_at = models.DateTimeField(null=True, blank=True)
    variance_reason = models.TextField(blank=True)
    closing_note = models.CharField(max_length=255, blank=True)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='opened_cash_days')
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='closed_cash_days')
    closed_at = models.DateTimeField(null=True, blank=True)
    # Professional automation audit fields. Automatic closing is explicit so
    # managers can distinguish a system reconciliation from a physical cash count.
    closed_automatically = models.BooleanField(default=False, db_index=True)
    auto_close_reason = models.CharField(max_length=255, blank=True)
    automation_checked_at = models.DateTimeField(null=True, blank=True)
    day_ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    day_ended_automatically = models.BooleanField(default=False, db_index=True)
    # A new business day may start while a previous day is still awaiting
    # physical verification. This flag makes the opening explicitly provisional
    # until the previous verified closing balance is available.
    opening_is_provisional = models.BooleanField(default=False, db_index=True)
    opening_provisional_note = models.CharField(max_length=255, blank=True)
    opening_reconciled_at = models.DateTimeField(null=True, blank=True)
    # Mobile money account (V54.96): a second "till" with its own opening, movements and counted balance.
    # opening_mobile is a manual starting balance (NULL = carried forward from the previous checked day).
    # actual_mobile_balance is the balance the shop's mobile money statement shows. Neither touches drawer cash.
    opening_mobile = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    actual_mobile_balance = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    mobile_checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='mobile_money_checks')
    mobile_checked_at = models.DateTimeField(null=True, blank=True)
    mobile_check_note = models.CharField(max_length=255, blank=True)

    @property
    def day_transfers(self):
        return CashTransfer.objects.filter(pharmacy_id=self.pharmacy_id, transfer_date=self.date).select_related('created_by').order_by('created_at')

    @property
    def mobile_money_totals(self):
        from dashboard.mobile_money import mobile_money_totals
        return mobile_money_totals(self.pharmacy, self.date, self.date)

    @property
    def expected_mobile_received(self):
        """Mobile money taken from customers today (sales + debts - refunds). Not the balance."""
        return self.mobile_money_totals['received']

    @property
    def mobile_opening_balance(self):
        from dashboard.mobile_money import mobile_opening_balance
        return mobile_opening_balance(self.pharmacy, self.date, manual=self.opening_mobile)

    @property
    def expected_mobile_balance(self):
        """Opening + received - mobile-paid expenses + cash put into mobile - mobile cashed out to the drawer."""
        from dashboard.mobile_money import mobile_net
        return self.mobile_opening_balance + mobile_net(self.pharmacy, self.date, self.date)

    @property
    def mobile_money_difference(self):
        if self.actual_mobile_balance is None:
            return None
        from .cash_rules import clean_variance
        return clean_variance(self.actual_mobile_balance - self.expected_mobile_balance)

    @property
    def expected_total_money(self):
        """Cash and mobile money are both real money: expected drawer cash + expected mobile balance."""
        return self.expected_closing_cash + self.expected_mobile_balance

    @property
    def counted_total_money(self):
        """Counted drawer cash + mobile money statement balance. None until both are entered."""
        if self.actual_mobile_balance is None:
            return None
        latest = self.latest_cash_count
        if latest is not None and not self.count_is_outdated and self.status == self.Status.OPEN:
            cash = latest.physical_cash
        else:
            cash = self.actual_closing_cash
        if cash is None:
            return None
        return cash + self.actual_mobile_balance

    @property
    def total_money_difference(self):
        counted = self.counted_total_money
        from .cash_rules import clean_variance
        return None if counted is None else clean_variance(counted - self.expected_total_money)

    @property
    def is_day_ended(self):
        return self.status == self.Status.DAY_ENDED

    @property
    def is_carried_forward(self):
        return self.opening_source == self.OpeningSource.CARRY_FORWARD and self.carried_from_id is not None

    class Meta:
        ordering = ['-date']
        constraints = [models.UniqueConstraint(fields=['pharmacy', 'date'], name='unique_cash_day_per_pharmacy_date')]
        indexes = [models.Index(fields=['pharmacy', '-date'])]

    def clean(self):
        if self.opening_cash < 0:
            raise ValidationError({'opening_cash': 'Opening cash cannot be negative.'})
        if self.actual_closing_cash is not None and self.actual_closing_cash < 0:
            raise ValidationError({'actual_closing_cash': 'Closing cash cannot be negative.'})

    @property
    def cash_sales(self):
        """Cash received on this business date (original totals). See expenses.ledger."""
        from .ledger import CashLedgerService
        return CashLedgerService.totals(self).cash_sales

    @property
    def cash_refunds(self):
        from .ledger import CashLedgerService
        return CashLedgerService.totals(self).cash_refunds

    @property
    def approved_cash_expenses(self):
        from .ledger import CashLedgerService
        t = CashLedgerService.totals(self)
        return t.cash_expenses + t.cash_meals   # expenses + automatic staff-meal payments

    def expected_cash_as_of(self, as_of=None):
        """Expected physical cash at an exact point in time (single ledger rule)."""
        from .ledger import CashLedgerService
        return CashLedgerService.expected_cash(self, as_of)

    def _expected_cutoff(self):
        # Closed days are historical records: their expected value must remain
        # frozen at the closing timestamp even if later-day activity exists.
        if self.status == self.Status.CLOSED and self.closed_at:
            return self.closed_at
        if self.status == self.Status.DAY_ENDED and self.day_ended_at:
            # A historical day is frozen at automatic day-end, but a legacy/current
            # day that was accidentally marked DAY_ENDED must remain live for POS
            # trading until its real physical close. Automatic day-end itself is now
            # guarded against today's date in expenses.services.
            return timezone.now() if self.date >= timezone.localdate() else self.day_ended_at
        return timezone.now()

    @property
    def expected_closing_cash(self):
        return self.expected_cash_as_of(self._expected_cutoff())

    @property
    def expected_breakdown(self):
        """Itemised expected cash (same cutoff and queries as expected_closing_cash)."""
        from .ledger import CashLedgerService
        return CashLedgerService.breakdown(self, self._expected_cutoff())

    @property
    def latest_cash_count(self):
        return self.physical_counts.order_by('-counted_at').first()

    @property
    def count_is_outdated(self):
        count = self.latest_cash_count
        if not count:
            return False
        return self.has_cash_activity_after(count.counted_at)

    def has_cash_activity_after(self, cutoff):
        from .ledger import CashLedgerService
        return CashLedgerService.has_activity_after(self, cutoff)

    @property
    def cash_difference(self):
        if self.actual_closing_cash is None:
            return None
        # Closed days are frozen at closed_at. Never let later transactions
        # rewrite a historical closing variance.
        cutoff = self.closed_at or self.physical_counted_at or timezone.now()
        from .cash_rules import clean_variance
        return clean_variance(self.actual_closing_cash - self.expected_cash_as_of(cutoff))

    @property
    def post_close_cash_activity(self):
        if self.status != self.Status.CLOSED or not self.closed_at:
            return False
        return self.has_cash_activity_after(self.closed_at)

    @property
    def reconciliation_state(self):
        """Single presentation state used by Cash Control.

        CLOSED is not the same as reconciled when cash activity exists after
        the recorded close. Keeping this state explicit prevents the dashboard,
        timeline and reconciled-days table from disagreeing.
        """
        if self.status == self.Status.CLOSED and self.post_close_cash_activity:
            return 'NEEDS_RECONCILIATION'
        if self.status == self.Status.CLOSED:
            return 'RECONCILED'
        if self.status == self.Status.DAY_ENDED:
            return 'DAY_ENDED'
        return 'OPEN'

class CashCount(models.Model):
    """Immutable physical cash count audit record.

    A count is not a close. Managers may count during the day, and the system
    records the expected amount at exactly that moment. A later transaction makes
    the count stale but never changes the historical count record.
    """
    cash_day = models.ForeignKey(CashDay, on_delete=models.CASCADE, related_name='physical_counts')
    counted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cash_count_records')
    counted_at = models.DateTimeField(default=timezone.now, db_index=True)
    physical_cash = models.DecimalField(max_digits=14, decimal_places=2)
    expected_cash = models.DecimalField(max_digits=14, decimal_places=2)
    variance = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.TextField(blank=True)
    confirmed = models.BooleanField(default=True)
    # A correction never overwrites the original count. The new count explicitly
    # points to the record it supersedes so the complete physical-count history
    # remains auditable.
    supersedes = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='corrections')
    correction_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-counted_at']
        indexes = [
            models.Index(fields=['cash_day', '-counted_at']),
        ]

    @property
    def variance_clean(self):
        """Variance rounded to the whole shilling; sub-shilling residue is 0 (matched)."""
        from .cash_rules import clean_variance
        return clean_variance(self.variance)

    def clean(self):
        if self.physical_cash < 0:
            raise ValidationError({'physical_cash': 'Physical cash cannot be negative.'})

    def __str__(self):
        return f'{self.cash_day.date} count — TZS {self.physical_cash:,.0f}'

class CashOpeningAdjustment(models.Model):
    cash_day = models.ForeignKey(CashDay, on_delete=models.CASCADE, related_name='opening_adjustments')
    previous_amount = models.DecimalField(max_digits=14, decimal_places=2)
    new_amount = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.TextField()
    adjusted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cash_opening_adjustments')
    adjusted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-adjusted_at']
        indexes = [models.Index(fields=['cash_day', '-adjusted_at'])]

    def __str__(self):
        return f'{self.cash_day} opening adjusted from {self.previous_amount} to {self.new_amount}'

class Expense(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending approval'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'
        CANCELLED = 'CANCELLED', 'Cancelled'

    class PaymentSource(models.TextChoices):
        CASH_SALES = 'CASH_SALES', 'Sales cash / petty cash'
        MOBILE_MONEY = 'MOBILE_MONEY', 'Mobile money'
        BANK = 'BANK', 'Bank account'
        OTHER = 'OTHER', 'Other'

    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='expenses')
    expense_date = models.DateField(default=timezone.localdate)
    # V54.68: the business day the money actually left the drawer. Set on approval (so an expense
    # that waited as PENDING and is approved later is deducted on the approval day). NULL = legacy
    # row, which keeps using expense_date so history is never rewritten.
    cash_date = models.DateField(null=True, blank=True)
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='expenses')
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_source = models.CharField(max_length=20, choices=PaymentSource.choices, default=PaymentSource.CASH_SALES)
    reference = models.CharField(max_length=100, blank=True)
    receipt = models.FileField(upload_to='expense_receipts/%Y/%m/', blank=True, null=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='submitted_expenses')
    submitted_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='decided_expenses')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    # V97.36 - auto-recorded employee expenses. A sales-cash expense entered by an employee leaves the
    # drawer immediately (status APPROVED, auto_recorded=True) and the boss is notified. The boss may
    # confirm it or REJECT it at any time, however old. A rejection never rewrites the day the money
    # left: it adds a reversal on the day of the rejection (reversed_at), exactly like a meal reversal.
    auto_recorded = models.BooleanField(default=False, db_index=True)
    cash_out_at = models.DateTimeField(null=True, blank=True)   # exact moment the cash left the drawer
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_expenses')
    reviewed_at = models.DateTimeField(null=True, blank=True)    # boss confirmed the auto-recorded expense
    reversed_at = models.DateTimeField(null=True, blank=True, db_index=True)  # rejected after the cash had left: cash returns to the books now
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='reversed_expenses')

    class Meta:
        ordering = ['-expense_date', '-submitted_at']
        indexes = [
            models.Index(fields=['pharmacy', '-expense_date']),
            models.Index(fields=['pharmacy', 'status']),
        ]

    def clean(self):
        if self.amount <= 0:
            raise ValidationError({'amount': 'Expense amount must be greater than zero.'})
        if self.expense_date > timezone.localdate():
            raise ValidationError({'expense_date': 'Expense date cannot be in the future.'})
        if self.submitted_by_id and self.pharmacy_id:
            if self.submitted_by.is_employee_role() and self.submitted_by.pharmacy_id != self.pharmacy_id:
                raise ValidationError('Employee is not assigned to this branch.')

    @property
    def affects_cash(self):
        return self.status == self.Status.APPROVED and self.payment_source == self.PaymentSource.CASH_SALES

    @property
    def awaiting_review(self):
        """Auto-recorded, deducted already, and the boss has not yet confirmed or rejected it."""
        return self.auto_recorded and self.status == self.Status.APPROVED and self.reviewed_at is None

    @property
    def was_reversed(self):
        return self.reversed_at is not None

    def __str__(self):
        return f'{self.title} — TZS {self.amount:,.2f}'


class CashDayReopenAudit(models.Model):
    """Immutable snapshot explaining an authorized reopening of a closed cash day."""
    cash_day = models.ForeignKey(CashDay, on_delete=models.PROTECT, related_name='reopen_audits')
    previous_opening_cash = models.DecimalField(max_digits=14, decimal_places=2)
    previous_closing_cash = models.DecimalField(max_digits=14, decimal_places=2)
    previous_expected_cash = models.DecimalField(max_digits=14, decimal_places=2)
    previous_variance = models.DecimalField(max_digits=14, decimal_places=2)
    previous_closed_at = models.DateTimeField(null=True, blank=True)
    previous_closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cash_day_reopen_actions')
    reason = models.TextField()
    reopened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cash_day_reopened_records')
    reopened_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-reopened_at']

    def __str__(self):
        return f'{self.cash_day.date} reopened at {self.reopened_at:%Y-%m-%d %H:%M}'


class _ImmutableMixin:
    """Rows of this kind are append-only: no update, no delete (audit integrity)."""

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise ValidationError(f'{self.__class__.__name__} records are immutable and cannot be edited.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f'{self.__class__.__name__} records are immutable and cannot be deleted.')


class CashClosingSnapshot(_ImmutableMixin, models.Model):
    """Frozen record of one final close. A reopen never edits it; the next close adds a new row."""
    cash_day = models.ForeignKey(CashDay, on_delete=models.PROTECT, related_name='closing_snapshots')
    revision = models.PositiveIntegerField(default=1)
    opening_cash = models.DecimalField(max_digits=14, decimal_places=2)
    expected_cash = models.DecimalField(max_digits=14, decimal_places=2)
    physical_cash = models.DecimalField(max_digits=14, decimal_places=2)
    variance = models.DecimalField(max_digits=14, decimal_places=2)
    cash_sales = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cash_debt_collected = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cash_refunds = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cash_expenses = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    note = models.TextField(blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cash_closing_snapshots')
    closed_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ['-closed_at']
        constraints = [models.UniqueConstraint(fields=['cash_day', 'revision'], name='unique_snapshot_revision_per_day')]

    def __str__(self):
        return f'{self.cash_day.date} close r{self.revision}'


class CashAuditEvent(_ImmutableMixin, models.Model):
    """Append-only audit trail for every important cash-control action."""

    class Action(models.TextChoices):
        DAY_CREATED = 'DAY_CREATED', 'Cash day created'
        OPENING_ADJUSTED = 'OPENING_ADJUSTED', 'Opening adjusted'
        OPENING_RECONCILED = 'OPENING_RECONCILED', 'Opening reconciled'
        SPOT_CHECK = 'SPOT_CHECK', 'Spot check'
        COUNT_CORRECTED = 'COUNT_CORRECTED', 'Count corrected'
        DAY_ENDED = 'DAY_ENDED', 'Day ended'
        CASH_CLOSED = 'CASH_CLOSED', 'Cash closed'
        POST_CLOSE_DETECTED = 'POST_CLOSE_DETECTED', 'Post-close activity detected'
        REOPENED = 'REOPENED', 'Cash day reopened'

    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.PROTECT, related_name='cash_audit_events')
    cash_day = models.ForeignKey(CashDay, on_delete=models.PROTECT, related_name='audit_events')
    business_date = models.DateField(db_index=True)
    action = models.CharField(max_length=24, choices=Action.choices, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='cash_audit_events')
    old_value = models.CharField(max_length=120, blank=True)
    new_value = models.CharField(max_length=120, blank=True)
    reason = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['pharmacy', '-created_at'], name='expenses_ca_pharmac_idx')]

    def __str__(self):
        return f'{self.business_date} {self.action}'


class CashControlReset(models.Model):
    """Auditable checkpoint when management starts a fresh cash-control cycle.

    Historical sales, expenses, debt collections and prior cash-day evidence are
    intentionally retained; this record never deletes accounting source data.
    """
    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.PROTECT, related_name='cash_control_resets')
    start_date = models.DateField(db_index=True)
    opening_cash = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.TextField()
    reset_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cash_control_resets')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    archived_cash_days = models.PositiveIntegerField(default=0)

    class Method(models.TextChoices):
        START_OF_DAY = 'START_OF_DAY', 'Known opening cash'
        COUNT_NOW = 'COUNT_NOW', 'Physical count now (opening calculated)'

    previous_opening = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    method = models.CharField(max_length=16, choices=Method.choices, default=Method.START_OF_DAY)
    counted_cash = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    movement_at_reset = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    expected_after = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    expected_before = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
    def __str__(self):
        return f'{self.pharmacy_id} cash reset from {self.start_date}'


class MealCashPayment(models.Model):
    """Drawer-cash payment for an EARNED meal (V50.42). At most one ACTIVE row per meal;
    a meal that is reversed and later re-earned gets a new row, so history never changes.

    Created automatically when a MealLedger becomes EARNED (scan funded by sales,
    or manager-verified network-outage meal) and reversed when the meal stops being
    EARNED. It is NOT an Expense, so a meal can never be deducted twice. Cash leaves
    the drawer on the day the payment is recorded (paid_at); a reversal returns cash
    on the day it is reversed (reversed_at). Closed days are never rewritten.
    """
    meal = models.ForeignKey('accounts.MealLedger', on_delete=models.CASCADE, related_name='cash_payments')
    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='meal_cash_payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    meal_date = models.DateField()
    paid_at = models.DateTimeField(default=timezone.now, db_index=True)
    reversed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # Meals that were already EARNED before V50.42: tracked so they are never charged
    # retroactively, but excluded from every cash total.
    is_legacy = models.BooleanField(default=False)
    # V54.28: pre-V50.42 meals are charged to the drawer ONCE, on the settlement day (never on
    # their original, possibly closed, day). Audit stamp of when that settlement happened.
    legacy_settled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_at', '-pk']
        indexes = [models.Index(fields=['pharmacy', 'paid_at'], name='exp_mealpay_ph_paid_idx')]

    @property
    def is_active(self):
        return self.reversed_at is None

    def __str__(self):
        return f'Meal payment {self.amount} for meal {self.meal_id}'


class CashTransfer(models.Model):
    """Money moved between the drawer and the mobile money account (e.g. cash banked at an M-Pesa agent, or
    mobile money cashed out into the drawer). Both sides move by the same amount, so total money never changes."""
    class Direction(models.TextChoices):
        CASH_TO_MOBILE = 'CASH_TO_MOBILE', 'Drawer cash -> Mobile money'
        MOBILE_TO_CASH = 'MOBILE_TO_CASH', 'Mobile money -> Drawer cash'

    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='cash_transfers')
    transfer_date = models.DateField(default=timezone.localdate, db_index=True)
    direction = models.CharField(max_length=20, choices=Direction.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cash_transfers')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['pharmacy', 'transfer_date'], name='expenses_ctransfer_pharm_idx')]

    def clean(self):
        if self.amount is None or self.amount <= 0:
            raise ValidationError({'amount': 'Transfer amount must be greater than zero.'})

    def __str__(self):
        return f'{self.get_direction_display()} {self.amount:,.0f} on {self.transfer_date}'


class EmployeeShiftCount(models.Model):
    """An employee's blind count of the drawer cash they are responsible for (V97.44).

    The employee counts FIRST, without seeing the system figure. On submit the system figure is computed
    server-side and frozen on this row with the difference, so nothing can be adjusted to fit afterwards.
    A zero difference is accepted automatically; any other difference waits for the manager's decision.
    """
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Awaiting manager'
        ACCEPTED = 'ACCEPTED', 'Accepted'
        REJECTED = 'REJECTED', 'Recount requested'

    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='shift_counts')
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='shift_counts')
    date = models.DateField(db_index=True)
    counted_cash = models.DecimalField(max_digits=14, decimal_places=2)
    # Frozen statement at the moment of the count.
    opening_cash = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    cash_sales = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cash_refunds = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    meal_earned = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    expenses_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    system_amount = models.DecimalField(max_digits=14, decimal_places=2)
    difference = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    reason = models.TextField(blank=True)
    reason_at = models.DateTimeField(null=True, blank=True)
    counted_at = models.DateTimeField(auto_now_add=True)
    auto_accepted = models.BooleanField(default=False)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='decided_shift_counts')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-date', '-counted_at']
        indexes = [models.Index(fields=['pharmacy', 'date'], name='shiftcount_pharm_date_idx')]
        constraints = [models.UniqueConstraint(
            fields=['employee', 'date'], condition=models.Q(status__in=['PENDING', 'ACCEPTED']),
            name='one_live_shift_count_per_employee_day')]

    def __str__(self):
        return f'{self.employee_id} {self.date} counted {self.counted_cash} ({self.status})'

    @property
    def is_short(self):
        return self.difference < 0

    @property
    def is_over(self):
        return self.difference > 0
