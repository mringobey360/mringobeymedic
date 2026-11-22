import uuid
from datetime import time, timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class DailyReportPackage(models.Model):
    """Admin-configurable package for Premium Intelligent Report SMS delivery."""
    code = models.CharField(max_length=30, unique=True)
    custom_owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name='custom_daily_report_packages')
    is_custom = models.BooleanField(default=False, db_index=True)
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    report_sms_credits = models.PositiveIntegerField(help_text='Number of Intelligent Report SMS deliveries included.')
    duration_days = models.PositiveIntegerField(default=30)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'price', 'id']

    def __str__(self):
        return f'{self.name} — TZS {self.price:,.0f} / {self.report_sms_credits:,} report SMS'


class DailyReportSubscription(models.Model):
    """Paid entitlement dedicated to Intelligent Daily Report SMS."""
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        EXPIRED = 'EXPIRED', 'Expired'
        CANCELLED = 'CANCELLED', 'Cancelled'

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='daily_report_subscriptions')
    package = models.ForeignKey(DailyReportPackage, on_delete=models.PROTECT, related_name='subscriptions')
    report_sms_credits = models.PositiveIntegerField(default=0)
    report_sms_used = models.PositiveIntegerField(default=0)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_reference = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['owner', 'status', 'end_date'])]

    @property
    def credits_remaining(self):
        return max(self.report_sms_credits - self.report_sms_used, 0)

    @property
    def is_current(self):
        return self.status == self.Status.ACTIVE and self.credits_remaining > 0   # credits never expire

    def __str__(self):
        return f'Intelligent Report SMS — {self.owner.username} — {self.credits_remaining:,} remaining'


class DailyReportPurchase(models.Model):
    """Customer payment request awaiting platform-owner verification."""
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending Payment Verification'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='daily_report_purchases')
    package = models.ForeignKey(DailyReportPackage, on_delete=models.PROTECT, related_name='purchases')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_reference = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    customer_note = models.TextField(blank=True)
    admin_note = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='reviewed_daily_report_purchases')

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f'{self.owner.username} — {self.package.name} — {self.get_status_display()}'


class DailyReportSettings(models.Model):
    """Owner-level preferences for the Premium Daily Sales Intelligence service."""
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='daily_report_settings')
    enabled = models.BooleanField(default=True)
    delivery_time = models.TimeField(default=time(4, 0), help_text='Africa/Dar_es_Salaam local time.')
    phone_number = models.CharField(max_length=20, blank=True, help_text='Optional override. Leave blank to use the manager account phone number.')
    include_pdf_link = models.BooleanField(default=True)
    include_salary_accrual = models.BooleanField(default=False, help_text='Deduct a daily share of monthly salaries (monthly salary / days in month) when calculating net profit.')
    report_retention_days = models.PositiveSmallIntegerField(default=30)
    last_sent_for_date = models.DateField(null=True, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Daily Sales Intelligence Setting'
        verbose_name_plural = 'Daily Sales Intelligence Settings'

    def __str__(self):
        return f'{self.owner} — {self.delivery_time:%H:%M}'

    @property
    def effective_phone(self):
        return self.phone_number or self.owner.phone_number


class DailySalesReport(models.Model):
    """Immutable-ish daily snapshot and secure public access token for one owner."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='daily_sales_reports')
    report_date = models.DateField()
    access_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    expires_at = models.DateTimeField()
    generated_at = models.DateTimeField(auto_now_add=True)
    sms_sent_at = models.DateTimeField(null=True, blank=True)
    sms_recipient = models.CharField(max_length=20, blank=True)
    sms_message = models.TextField(blank=True)
    sms_error = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict)

    class Meta:
        ordering = ['-report_date', '-generated_at']
        constraints = [models.UniqueConstraint(fields=['owner', 'report_date'], name='unique_daily_sales_report_owner_date')]
        indexes = [models.Index(fields=['owner', '-report_date']), models.Index(fields=['access_token'])]

    def __str__(self):
        return f'{self.owner} — {self.report_date}'

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def days_until_expiry(self):
        return max((self.expires_at - timezone.now()).days, 0)


class DailyReportDeliveryLog(models.Model):
    class Status(models.TextChoices):
        SENT = 'SENT', 'Sent'
        FAILED = 'FAILED', 'Failed'
        PROCESSING = 'PROCESSING', 'Processing'
        SKIPPED = 'SKIPPED', 'Skipped'

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='daily_report_delivery_logs')
    report = models.ForeignKey(DailySalesReport, on_delete=models.SET_NULL, null=True, blank=True, related_name='delivery_logs')
    report_date = models.DateField()
    scheduled_time = models.TimeField()
    recipient = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    message = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-attempted_at']
        constraints = [models.UniqueConstraint(fields=['owner', 'report_date'], name='unique_daily_report_delivery_owner_date')]
        indexes = [models.Index(fields=['report_date', 'status'])]

    def __str__(self):
        return f'{self.owner} — {self.report_date} — {self.status}'
