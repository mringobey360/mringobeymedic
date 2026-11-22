import uuid

from datetime import date

from django.db import models
from django.core.exceptions import ValidationError


class SMSSettings(models.Model):
    """
    Singleton model holding the Africa's Talking credentials.
    Managed from Django Admin by the system administrator only —
    never hardcoded in source code.
    """
    is_enabled = models.BooleanField(
        default=True,
        help_text="Master switch. Turn off to stop ALL outgoing SMS (saves cost)."
    )
    at_username = models.CharField(
        max_length=100,
        help_text="Your Africa's Talking application username."
    )
    at_api_key = models.CharField(
        max_length=255,
        help_text="Your Africa's Talking API key (kept private, never shown on frontend)."
    )
    sender_id = models.CharField(
        max_length=50, blank=True,
        help_text="Your registered Africa's Talking Sender ID (optional)."
    )

    # Granular toggles so the owner controls cost precisely
    send_employee_credentials = models.BooleanField(default=True, help_text="SMS username/password to new employees.")
    send_otp = models.BooleanField(default=True, help_text="SMS OTP codes for password reset (recommended: keep ON).")
    send_daily_summary = models.BooleanField(default=True, help_text="One SMS per day to the owner with sales/profit summary.")
    send_low_stock_alerts = models.BooleanField(default=True, help_text="SMS when a product hits its reorder level.")
    send_expiry_alerts = models.BooleanField(default=True, help_text="SMS when a product is about to expire.")
    send_debt_reminders = models.BooleanField(default=False, help_text="SMS reminders to customers who owe money.")
    send_receipt_sms = models.BooleanField(default=True, help_text="Send customers an SMS containing a secure sales receipt link when Premium Receipt SMS is active.")
    send_payment_receipts = models.BooleanField(default=True, help_text="Send registration/payment confirmation SMS with a secure subscription receipt link.")
    send_customer_marketing = models.BooleanField(default=True, help_text="Allow paid Customer Marketing SMS campaigns.")
    send_attendance_alerts = models.BooleanField(default=True, help_text="Allow attendance SMS (employee late / check-in / check-out) to branch owners. Each branch still chooses its own mode.")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SMS Settings"
        verbose_name_plural = "SMS Settings"

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # prevent deletion of the singleton

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={
            'at_username': 'sandbox',
            'at_api_key': 'CHANGE-ME',
        })
        return obj

    def __str__(self):
        return "SMS Settings (Africa's Talking)"


class SMSLog(models.Model):
    """Keeps a lightweight audit trail of every SMS attempted, for cost tracking."""
    class Category(models.TextChoices):
        CREDENTIALS = 'CREDENTIALS', 'Employee Credentials'
        OTP = 'OTP', 'Password Reset OTP'
        DAILY_SUMMARY = 'DAILY_SUMMARY', 'Daily Summary'
        LOW_STOCK = 'LOW_STOCK', 'Low Stock Alert'
        EXPIRY = 'EXPIRY', 'Expiry Alert'
        DEBT = 'DEBT', 'Debt Reminder'
        RECEIPT = 'RECEIPT', 'Receipt Download Link'
        PAYMENT_RECEIPT = 'PAYMENT_RECEIPT', 'Subscription Payment Receipt'
        CUSTOMER_MARKETING = 'CUSTOMER_MARKETING', 'Customer Marketing Campaign'
        ANNOUNCEMENT = 'ANNOUNCEMENT', 'Platform Announcement'
        ATTENDANCE = 'ATTENDANCE', 'Attendance Alert'
        OTHER = 'OTHER', 'Other'

    owner = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='sms_logs')
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    recipient = models.CharField(max_length=20)
    message = models.TextField()
    was_sent = models.BooleanField(default=False)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    character_count = models.PositiveIntegerField(default=0, editable=False)
    sms_segments = models.PositiveIntegerField(default=1, editable=False)
    provider_status = models.CharField(max_length=60, blank=True, editable=False)
    provider_message_id = models.CharField(max_length=120, blank=True, editable=False)
    delivery_status = models.CharField(max_length=40, blank=True, editable=False)
    delivery_error = models.TextField(blank=True, editable=False)
    delivery_updated_at = models.DateTimeField(null=True, blank=True, editable=False)

    def __str__(self):
        status = "sent" if self.was_sent else "failed"
        return f"[{self.category}] to {self.recipient} ({status})"


class SMSWallet(models.Model):
    """Billable SMS allowance for one paying pharmacy owner."""
    owner = models.OneToOneField('accounts.User', on_delete=models.CASCADE, related_name='sms_wallet')
    credits_granted = models.PositiveIntegerField(default=500)
    credits_used = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def credits_remaining(self):
        return max(self.credits_granted - self.credits_used, 0)

    def __str__(self):
        return f"SMS Wallet — {self.owner.username} ({self.credits_remaining} remaining)"


class CustomerSMSCredentials(models.Model):
    """Optional customer-owned Africa's Talking credentials, enabled by platform owner."""
    owner = models.OneToOneField('accounts.User', on_delete=models.CASCADE, related_name='custom_sms_credentials')
    access_enabled = models.BooleanField(default=False, help_text='Platform owner must enable this before the customer can open the custom SMS credentials form.')
    at_username = models.CharField(max_length=100, blank=True)
    sender_id = models.CharField(max_length=50, blank=True)
    at_api_key = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def has_complete_credentials(self):
        return bool(self.at_username and self.at_api_key and self.sender_id)

    def __str__(self):
        return f"Custom SMS — {self.owner.username}"


class SMSReceiptPackage(models.Model):
    """Admin-configurable package for Premium receipt-download SMS."""
    code = models.CharField(max_length=30, unique=True)
    custom_owner = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.CASCADE, related_name='custom_smsreceiptpackage_packages')
    is_custom = models.BooleanField(default=False, db_index=True)
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    sms_credits = models.PositiveIntegerField()
    duration_days = models.PositiveIntegerField(default=30)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'price', 'id']

    def __str__(self):
        return f"{self.name} — TZS {self.price:,.0f} / {self.sms_credits:,} SMS"


class SMSReceiptSubscription(models.Model):
    """Paid entitlement dedicated only to customer receipt-link SMS."""
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        EXPIRED = 'EXPIRED', 'Expired'
        CANCELLED = 'CANCELLED', 'Cancelled'

    owner = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='receipt_sms_subscriptions')
    package = models.ForeignKey(SMSReceiptPackage, on_delete=models.PROTECT, related_name='subscriptions')
    sms_credits = models.PositiveIntegerField(default=0)
    sms_used = models.PositiveIntegerField(default=0)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_reference = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def credits_remaining(self):
        return max(self.sms_credits - self.sms_used, 0)

    @property
    def is_current(self):
        from django.utils import timezone
        return self.status == self.Status.ACTIVE and self.credits_remaining > 0   # no expiry date check

    def __str__(self):
        return f"Receipt SMS — {self.owner.username} — {self.credits_remaining:,} SMS"


class SMSReceiptPurchase(models.Model):
    """Payment request for Premium Receipt SMS; activated after admin verification."""
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending Payment Verification'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    owner = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='receipt_sms_purchases')
    package = models.ForeignKey(SMSReceiptPackage, on_delete=models.PROTECT, related_name='purchases')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_reference = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    customer_note = models.TextField(blank=True)
    admin_note = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='reviewed_receipt_sms_purchases')

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"{self.owner.username} — {self.package.name} — {self.get_status_display()}"


class SMSDebtPackage(models.Model):
    """Admin-configurable monthly package for Premium debt-reminder SMS."""
    code = models.CharField(max_length=30, unique=True)
    custom_owner = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.CASCADE, related_name='custom_smsdebtpackage_packages')
    is_custom = models.BooleanField(default=False, db_index=True)
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    sms_credits = models.PositiveIntegerField()
    duration_days = models.PositiveIntegerField(default=30)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'price', 'id']

    def __str__(self):
        return f"{self.name} — TZS {self.price:,.0f} / {self.sms_credits:,} SMS"


class SMSDebtSubscription(models.Model):
    """Paid monthly entitlement dedicated only to Premium debt-reminder SMS."""
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        EXPIRED = 'EXPIRED', 'Expired'
        CANCELLED = 'CANCELLED', 'Cancelled'

    owner = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='debt_sms_subscriptions')
    package = models.ForeignKey(SMSDebtPackage, on_delete=models.PROTECT, related_name='subscriptions')
    sms_credits = models.PositiveIntegerField(default=0)
    sms_used = models.PositiveIntegerField(default=0)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_reference = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def credits_remaining(self):
        return max(self.sms_credits - self.sms_used, 0)

    @property
    def is_current(self):
        from django.utils import timezone
        return self.status == self.Status.ACTIVE and self.credits_remaining > 0   # no expiry date check

    def __str__(self):
        return f"Debt SMS — {self.owner.username} — {self.credits_remaining:,} SMS"


class SMSDebtPurchase(models.Model):
    """In-system payment request. Customer pays the configured Lipa Namba, then submits the reference here; admin confirms it."""
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending Payment Verification'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    owner = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='debt_sms_purchases')
    package = models.ForeignKey(SMSDebtPackage, on_delete=models.PROTECT, related_name='purchases')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_reference = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    customer_note = models.TextField(blank=True)
    admin_note = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='reviewed_debt_sms_purchases')

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"{self.owner.username} — {self.package.name} — {self.get_status_display()}"


class SMSMarketingPackage(models.Model):
    """Paid package dedicated to customer marketing/broadcast SMS."""
    code = models.CharField(max_length=30, unique=True)
    custom_owner = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.CASCADE, related_name='custom_smsmarketingpackage_packages')
    is_custom = models.BooleanField(default=False, db_index=True)
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    sms_credits = models.PositiveIntegerField()
    duration_days = models.PositiveIntegerField(default=30)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'price', 'id']

    def __str__(self):
        return f"{self.name} — TZS {self.price:,.0f} / {self.sms_credits:,} SMS"


# SMS package credits (Debt, Receipt, Marketing, Intelligent Report) never expire: they stay
# usable until the balance reaches zero. `end_date` is a required column, so it is parked on
# this far-future marker instead of being a real deadline. Every SMS code path also ignores
# it (see is_current), so old rows with a past end_date keep working.
NO_EXPIRY_DATE = date(2099, 12, 31)
MARKETING_NO_EXPIRY_DATE = NO_EXPIRY_DATE   # backward-compatible name


class SMSMarketingSubscription(models.Model):
    """Paid entitlement used only by Customer Registry marketing campaigns.

    Credits do not expire monthly: the entitlement stays ACTIVE until all SMS are used."""
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        EXPIRED = 'EXPIRED', 'Expired'
        CANCELLED = 'CANCELLED', 'Cancelled'

    owner = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='marketing_sms_subscriptions')
    package = models.ForeignKey(SMSMarketingPackage, on_delete=models.PROTECT, related_name='subscriptions')
    sms_credits = models.PositiveIntegerField(default=0)
    sms_used = models.PositiveIntegerField(default=0)
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
        return max(self.sms_credits - self.sms_used, 0)

    @property
    def never_expires(self):
        return True

    @property
    def is_current(self):
        # No end_date check: credits last until they are used up.
        return self.status == self.Status.ACTIVE and self.credits_remaining > 0


class SMSMarketingPurchase(models.Model):
    """Customer payment request awaiting platform-owner verification."""
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending Payment Verification'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    owner = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='marketing_sms_purchases')
    package = models.ForeignKey(SMSMarketingPackage, on_delete=models.PROTECT, related_name='purchases')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_reference = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    customer_note = models.TextField(blank=True)
    admin_note = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='reviewed_marketing_sms_purchases')

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"{self.owner.username} — {self.package.name} — {self.get_status_display()}"


class SMSAnnouncement(models.Model):
    """
    A platform-owner SMS campaign (announcement / system update) sent to customers.

    Paid by the platform (platform Africa's Talking credentials) — it never
    consumes any customer's SMS wallet or Premium SMS balance.
    """
    class MessageType(models.TextChoices):
        ANNOUNCEMENT = 'ANNOUNCEMENT', 'General announcement'
        SYSTEM_UPDATE = 'SYSTEM_UPDATE', 'System update'
        MAINTENANCE = 'MAINTENANCE', 'Scheduled maintenance'
        BILLING = 'BILLING', 'Subscription / billing notice'

    class Audience(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Customers with an active subscription'
        ALL = 'ALL', 'All active customer accounts'
        EXPIRING = 'EXPIRING', 'Subscriptions expiring within 7 days'
        EXPIRED = 'EXPIRED', 'Customers with an expired subscription'
        SELECTED = 'SELECTED', 'Selected customers only'
        ASSISTANTS = 'ASSISTANTS', 'Assistant managers only'
        PROSPECTS = 'PROSPECTS', 'Prospects (registered business phone numbers)'

    class Status(models.TextChoices):
        QUEUED = 'QUEUED', 'Queued'
        SENDING = 'SENDING', 'Sending'
        COMPLETED = 'COMPLETED', 'Completed'
        PARTIAL = 'PARTIAL', 'Partially delivered'
        FAILED = 'FAILED', 'Failed'

    title = models.CharField(max_length=120, help_text='Official announcement title shown to customers in SMS and in-app notifications.')
    message_type = models.CharField(max_length=20, choices=MessageType.choices, default=MessageType.ANNOUNCEMENT)
    message = models.TextField(help_text='Message body. The final customer SMS is composed as: title + message + administrator signature. {name} is replaced with the customer first name; {link} (optional) with a trackable link to link_url.')
    link_url = models.URLField(
        blank=True,
        help_text="Optional. Where {link} inside the message should lead (e.g. a payment page). Each customer's SMS gets "
                   "their own short, trackable link that redirects here, so you can see who opened it.",
    )
    audience = models.CharField(max_length=10, choices=Audience.choices, default=Audience.ACTIVE)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)

    total_recipients = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    total_segments = models.PositiveIntegerField(default=0, help_text='SMS segments successfully sent (what the provider bills).')
    also_in_app = models.BooleanField(default=False, help_text='A bell notification was also posted for each customer.')
    include_assistants = models.BooleanField(default=True, help_text="The announcement was also delivered to the customers' active assistant managers.")

    created_by = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='sms_announcements')
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} — {self.get_status_display()}"

    @property
    def pending_count(self):
        return max(self.total_recipients - self.sent_count - self.failed_count - self.skipped_count, 0)

    @property
    def progress_percent(self):
        if not self.total_recipients:
            return 100
        done = self.sent_count + self.failed_count + self.skipped_count
        return min(int(done * 100 / self.total_recipients), 100)

    @property
    def is_running(self):
        return self.status in (self.Status.QUEUED, self.Status.SENDING)


class SMSAnnouncementRecipient(models.Model):
    """One delivery row per customer per campaign (audit trail + resume/retry support)."""
    class DeliveryStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SENT = 'SENT', 'Sent'
        FAILED = 'FAILED', 'Failed'
        SKIPPED = 'SKIPPED', 'Skipped'

    announcement = models.ForeignKey(SMSAnnouncement, on_delete=models.CASCADE, related_name='recipients')
    owner = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='received_sms_announcements')
    prospect = models.ForeignKey('sms_integration.SMSProspect', null=True, blank=True, on_delete=models.SET_NULL, related_name='announcement_rows')
    display_name = models.CharField(max_length=150, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    message = models.TextField(blank=True, help_text='Final personalised text for this customer.')
    segments = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=10, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING, db_index=True)
    provider_status = models.CharField(max_length=40, blank=True)
    error_message = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    access_token = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, db_index=True,
        help_text='Unguessable identifier used for this customer\'s personal {link} redirect — never a sequential ID.',
    )
    opened_at = models.DateTimeField(null=True, blank=True, help_text='When this customer first followed their {link}.')

    class Meta:
        ordering = ['display_name', 'id']

    def __str__(self):
        return f"{self.display_name or self.phone_number} — {self.get_status_display()}"


class SMSProspect(models.Model):
    """
    A business owner who is NOT a SmartPOS customer yet - only a phone number the platform
    owner collected so that official SmartPOS invitations can be sent to them.
    """
    class BusinessType(models.TextChoices):
        RETAIL = 'RETAIL', 'Retail shop'
        WHOLESALE = 'WHOLESALE', 'Wholesale shop'
        HARDWARE = 'HARDWARE', 'Hardware'
        PHARMACY = 'PHARMACY', 'Pharmacy'
        COSMETICS = 'COSMETICS', 'Cosmetics'
        FOOD = 'FOOD', 'Food / Restaurant'
        OTHER = 'OTHER', 'Other'

    class Status(models.TextChoices):
        NEW = 'NEW', 'New'
        CONTACTED = 'CONTACTED', 'Contacted'
        JOINED = 'JOINED', 'Joined'
        OPTED_OUT = 'OPTED_OUT', 'Opted out'

    phone_number = models.CharField(max_length=20, unique=True, help_text='Normalised +255... number (one row per number).')
    name = models.CharField(max_length=150, blank=True)
    business_name = models.CharField(max_length=150, blank=True)
    business_type = models.CharField(max_length=12, choices=BusinessType.choices, default=BusinessType.OTHER)
    region = models.CharField(max_length=60, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW, db_index=True)
    sms_count = models.PositiveIntegerField(default=0, help_text='Invitations successfully sent to this number.')
    last_sms_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey('accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='sms_prospects_added')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.business_name or self.name or 'Prospect'} ({self.phone_number})"

    @property
    def display_name(self):
        return self.name or self.business_name or self.phone_number

    @property
    def can_receive(self):
        return self.status not in (self.Status.OPTED_OUT, self.Status.JOINED)
