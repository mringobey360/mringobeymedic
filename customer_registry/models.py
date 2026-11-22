from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models


class CustomerContact(models.Model):
    pharmacy = models.ForeignKey(
        'pharmacies.Pharmacy',
        on_delete=models.CASCADE,
        related_name='customer_contacts',
    )
    name = models.CharField(max_length=150)
    phone = models.CharField(
        max_length=20,
        validators=[RegexValidator(
            r'^\+[0-9]{9,14}$',
            'Use international format, e.g. +255712345678.'
        )],
    )
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='registered_customer_contacts',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['pharmacy', 'phone'],
                name='uniq_customer_phone_per_branch',
            )
        ]
        indexes = [
            models.Index(fields=['pharmacy', 'is_active']),
            models.Index(fields=['pharmacy', 'name']),
            models.Index(fields=['pharmacy', 'phone']),
        ]

    def __str__(self):
        return f"{self.name} · {self.phone}"


class CustomerCampaign(models.Model):
    class Status(models.TextChoices):
        QUEUED = 'QUEUED', 'Queued'
        SENDING = 'SENDING', 'Sending'
        COMPLETED = 'COMPLETED', 'Completed'
        PARTIAL = 'PARTIAL', 'Partially sent'
        FAILED = 'FAILED', 'Failed'

    title = models.CharField(max_length=120)
    message = models.TextField(max_length=1000)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    total_recipients = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    total_segments = models.PositiveIntegerField(default=0, help_text='Total marketing SMS credits reserved/used by this campaign.')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='customer_campaigns_created',
    )
    pharmacy = models.ForeignKey(
        'pharmacies.Pharmacy',
        on_delete=models.CASCADE,
        related_name='customer_campaigns',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['pharmacy', '-created_at']),
        ]

    def __str__(self):
        return self.title


class CustomerCampaignRecipient(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SENDING = 'SENDING', 'Sending'
        SENT = 'SENT', 'Sent'
        FAILED = 'FAILED', 'Failed'

    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20)
    message = models.TextField()
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    error = models.TextField(blank=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    attempt_count = models.PositiveIntegerField(default=0)
    segments = models.PositiveSmallIntegerField(default=1, help_text='Billable SMS segments for this personalised message.')
    credits_reserved = models.PositiveSmallIntegerField(default=0, help_text='Marketing credits currently reserved for this recipient.')
    last_attempt_at = models.DateTimeField(blank=True, null=True)
    campaign = models.ForeignKey(
        CustomerCampaign,
        on_delete=models.CASCADE,
        related_name='recipients',
    )
    customer = models.ForeignKey(
        CustomerContact,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='campaign_recipients',
    )

    class Meta:
        indexes = [
            models.Index(fields=['campaign', 'status']),
        ]

    def __str__(self):
        return f"{self.name} · {self.phone} · {self.status}"


class CustomerDataAccessLog(models.Model):
    """Immutable-by-convention trail of who exported / bulk-handled customer phone numbers."""

    class Action(models.TextChoices):
        EXPORT = 'EXPORT', 'Exported customer list'
        EXPORT_DENIED = 'EXPORT_DENIED', 'Export refused (not a manager)'
        BULK_IMPORT = 'BULK_IMPORT', 'Bulk customer import'
        BULK_DELETE = 'BULK_DELETE', 'Bulk customer delete'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='customer_data_access_logs')
    username = models.CharField(max_length=150, blank=True)
    pharmacy = models.ForeignKey('pharmacies.Pharmacy', null=True, blank=True, on_delete=models.SET_NULL, related_name='customer_data_access_logs')
    action = models.CharField(max_length=20, choices=Action.choices)
    record_count = models.PositiveIntegerField(default=0)
    ip_address = models.CharField(max_length=45, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['pharmacy', '-created_at'], name='customer_re_pharmac_7a1c0e_idx'),
            models.Index(fields=['action', '-created_at'], name='customer_re_action_3b9d52_idx'),
        ]

    def __str__(self):
        return f'{self.created_at:%Y-%m-%d %H:%M} {self.username} {self.action} ({self.record_count})'
