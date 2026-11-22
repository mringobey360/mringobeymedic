import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone

class LayawayPlan(models.Model):
    class Status(models.TextChoices):
        ACTIVE='ACTIVE','Active'
        PAID_PENDING_HANDOVER='PAID_PENDING_HANDOVER','Paid — pending handover'
        PARTIALLY_HANDED_OVER='PARTIALLY_HANDED_OVER','Partially handed over'
        COMPLETED='COMPLETED','Completed'
        NEEDS_RESOLUTION='NEEDS_RESOLUTION','Needs resolution'
        CANCELLED='CANCELLED','Cancelled'
    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='layaway_plans')
    reference = models.CharField(max_length=24, unique=True, editable=False, db_index=True)
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='layaway_plans')
    quantity = models.PositiveIntegerField(default=1)
    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=24)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    deposit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    due_date = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='layaway_created')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='layaway_resolved')
    resolution_note = models.TextField(blank=True)
    # A manager-approved POS sale may release an unpaid plan's stock reservation while the customer case is reviewed.
    reservation_released = models.BooleanField(default=False, db_index=True)
    # Filled by the handover step: the official Sale (stock, receipt, cash ledger) that closed this plan.
    sale = models.OneToOneField('sales.Sale', on_delete=models.SET_NULL, null=True, blank=True, related_name='layaway_plan')
    handed_over_at = models.DateTimeField(null=True, blank=True)
    handed_over_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='layaway_handed_over')
    class Meta:
        ordering=['-created_at']
        indexes=[models.Index(fields=['pharmacy','status','-created_at'])]
    def save(self,*args,**kwargs):
        if not self.reference:
            self.reference=f"LAY-{timezone.localdate():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
        super().save(*args,**kwargs)
    @property
    def quantity_handed_over(self):
        return self.handovers.aggregate(v=Sum('quantity'))['v'] or 0
    @property
    def quantity_remaining(self):
        return max(0, int(self.quantity or 0) - int(self.quantity_handed_over or 0))
    @property
    def total_amount(self): return (self.unit_price or Decimal('0')) * self.quantity
    @property
    def amount_paid(self): return self.payments.filter(status=LayawayPayment.Status.RECEIVED).aggregate(v=Sum('amount'))['v'] or Decimal('0')
    @property
    def amount_refunded(self): return self.refunds.aggregate(v=Sum('amount'))['v'] or Decimal('0')
    @property
    def balance(self): return max(Decimal('0'), self.total_amount-self.amount_paid)
    @property
    def is_overdue(self): return self.status == self.Status.ACTIVE and self.due_date and self.due_date < timezone.localdate()
    def __str__(self): return f'{self.reference} — {self.customer_name}'

class LayawayHandover(models.Model):
    """Auditable partial/full physical handover. Each handover creates exactly one official Sale."""
    plan = models.ForeignKey(LayawayPlan, on_delete=models.PROTECT, related_name='handovers')
    sale = models.OneToOneField('sales.Sale', on_delete=models.PROTECT, related_name='layaway_handover')
    quantity = models.PositiveIntegerField()
    handed_over_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='layaway_handovers')
    handed_over_at = models.DateTimeField(default=timezone.now, db_index=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['handed_over_at', 'pk']
        constraints = [models.CheckConstraint(condition=models.Q(quantity__gt=0), name='layaway_handover_qty_positive')]

    def __str__(self):
        return f'{self.plan.reference}: {self.quantity} unit(s) — Sale #{self.sale_id}'


class LayawayPayment(models.Model):
    class Method(models.TextChoices):
        CASH='CASH','Cash'
        MOBILE='MOBILE','Mobile money'
        BANK='BANK','Bank transfer'
        OTHER='OTHER','Other'
    class Status(models.TextChoices):
        RECEIVED='RECEIVED','Received'
        REVERSED='REVERSED','Reversed'
    plan=models.ForeignKey(LayawayPlan,on_delete=models.PROTECT,related_name='payments')
    amount=models.DecimalField(max_digits=12,decimal_places=2)
    method=models.CharField(max_length=10,choices=Method.choices,default=Method.CASH)
    transaction_reference=models.CharField(max_length=100,blank=True)
    idempotency_key=models.UUIDField(default=uuid.uuid4,unique=True,editable=False)
    status=models.CharField(max_length=12,choices=Status.choices,default=Status.RECEIVED)
    received_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True)
    received_at=models.DateTimeField(auto_now_add=True)
    note=models.CharField(max_length=255,blank=True)
    class Meta: ordering=['-received_at']
    def __str__(self): return f'{self.plan.reference} — {self.amount}'

class LayawayRefund(models.Model):
    """Money handed back to the customer when a plan is cancelled. A CASH refund leaves the drawer on the day it is paid."""
    Method = LayawayPayment.Method
    plan = models.ForeignKey(LayawayPlan, on_delete=models.PROTECT, related_name='refunds')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=10, choices=LayawayPayment.Method.choices, default=LayawayPayment.Method.CASH)
    note = models.CharField(max_length=255, blank=True)
    refunded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='layaway_refunds')
    refunded_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ['-refunded_at']
    def __str__(self): return f'{self.plan.reference} refund {self.amount}'

class LayawayAudit(models.Model):
    plan=models.ForeignKey(LayawayPlan,on_delete=models.CASCADE,related_name='audit_events')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.SET_NULL,null=True)
    action=models.CharField(max_length=40)
    detail=models.TextField(blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']

class LayawaySMSWallet(models.Model):
    """Branch-scoped Layaway SMS allowance: five free trial SMS, then paid credits."""
    pharmacy = models.OneToOneField('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='layaway_sms_wallet')
    trial_used = models.PositiveSmallIntegerField(default=0)
    paid_credits = models.PositiveIntegerField(default=0)
    paid_used = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def trial_remaining(self):
        return max(5 - self.trial_used, 0)

    @property
    def paid_remaining(self):
        return max(self.paid_credits - self.paid_used, 0)

    @property
    def total_remaining(self):
        return self.trial_remaining + self.paid_remaining

    def __str__(self):
        return f'Layaway SMS — {self.pharmacy.name}: {self.total_remaining} remaining'


class LayawaySMSPackage(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    tagline = models.CharField(max_length=180, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    sms_credits = models.PositiveIntegerField()
    is_featured = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'price']

    def __str__(self):
        return f'{self.name} — TZS {self.price:,.0f} / {self.sms_credits} SMS'


class LayawaySMSPurchase(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending verification'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='layaway_sms_purchases')
    package = models.ForeignKey(LayawaySMSPackage, on_delete=models.PROTECT, related_name='purchases')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='layaway_sms_purchases')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_reference = models.CharField(max_length=150, blank=True)
    customer_note = models.TextField(blank=True)
    admin_note = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_layaway_sms_purchases')
    credits_granted = models.BooleanField(default=False)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f'{self.pharmacy.name} — {self.package.name} — {self.status}'


class LayawaySMSLog(models.Model):
    """Idempotent audit trail for customer SMS events and scheduled reminders."""
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SENT = 'SENT', 'Accepted by provider'
        FAILED = 'FAILED', 'Failed'
        NO_CREDITS = 'NO_CREDITS', 'Insufficient SMS credits'

    class EventType(models.TextChoices):
        CREATED = 'CREATED', 'Plan created'
        PAYMENT = 'PAYMENT', 'Payment received'
        PAID = 'PAID', 'Fully paid'
        HANDOVER_PARTIAL = 'HANDOVER_PARTIAL', 'Partial handover'
        HANDOVER_COMPLETE = 'HANDOVER_COMPLETE', 'Handover complete'
        REMINDER_3_DAYS = 'REMINDER_3_DAYS', 'Due in three days'
        REMINDER_DUE = 'REMINDER_DUE', 'Due today'
        REMINDER_OVERDUE = 'REMINDER_OVERDUE', 'Overdue reminder'

    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='layaway_sms_logs')
    plan = models.ForeignKey(LayawayPlan, on_delete=models.CASCADE, related_name='sms_logs')
    event_key = models.CharField(max_length=100, unique=True)
    event_type = models.CharField(max_length=24, choices=EventType.choices)
    phone = models.CharField(max_length=24)
    message = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    provider_result = models.CharField(max_length=255, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['pharmacy', 'status', '-created_at'])]

    def __str__(self):
        return f'{self.plan.reference} — {self.event_type} — {self.status}'
