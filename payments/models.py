import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from core.encrypted_fields import EncryptedTextField


class PaymentGatewaySettings(models.Model):
    class Gateway(models.TextChoices):
        PESAPAL = 'PESAPAL', 'PesaPal'
        ABLINER = 'ABLINER', 'ABLiner'

    is_enabled = models.BooleanField(default=True)
    active_gateway = models.CharField(max_length=20, choices=Gateway.choices, default=Gateway.PESAPAL)
    abliner_enabled = models.BooleanField(default=False)
    abliner_api_key = EncryptedTextField(blank=True)
    abliner_webhook_secret = EncryptedTextField(blank=True)
    abliner_base_url = models.URLField(default='https://abliner.net', blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Payment Gateway Settings'
        verbose_name_plural = 'Payment Gateway Settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        self.abliner_base_url = (self.abliner_base_url or 'https://abliner.net').rstrip('/')
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return f'Payment Gateway — {self.get_active_gateway_display()}'

    @property
    def abliner_configured(self):
        return bool(self.abliner_api_key and self.abliner_webhook_secret)


class PaymentEvent(models.Model):
    payment = models.ForeignKey('PesapalTransaction', on_delete=models.CASCADE, related_name='events')
    gateway = models.CharField(max_length=20)
    event_type = models.CharField(max_length=80)
    provider_event_id = models.CharField(max_length=180, blank=True)
    provider_status = models.CharField(max_length=80, blank=True)
    message = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    success = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['gateway', 'provider_event_id'], name='uniq_payment_provider_event', condition=~models.Q(provider_event_id='')),
        ]
        indexes = [models.Index(fields=['gateway', 'event_type']), models.Index(fields=['provider_event_id'])]

    def __str__(self):
        return f'{self.gateway} — {self.event_type} — {self.payment.merchant_reference}'


class PesapalSettings(models.Model):
    """
    Singleton (same pattern as hq.PaymentSettings / sms_integration.SMSSettings)
    holding the Pesapal API 3.0 credentials and configuration. Editable ONLY
    from Django Admin by the platform owner (superadmin) — nothing here is
    ever hardcoded in source code.

    Get your Consumer Key / Consumer Secret from:
    Pesapal Business Dashboard -> Account Settings -> Developer Settings -> API settings.
    """

    class Environment(models.TextChoices):
        SANDBOX = 'SANDBOX', 'Sandbox (testing only — no real money moves)'
        LIVE = 'LIVE', 'Live (real customer payments)'

    is_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Turn ON to let customers pay instantly online with Pesapal "
            "(cards, M-Pesa, Tigo Pesa, Airtel Money, etc). When OFF, only "
            "the manual mobile-money (Lipa Namba) flow is shown, exactly as before."
        ),
    )
    environment = models.CharField(
        max_length=10, choices=Environment.choices, default=Environment.SANDBOX,
        help_text="Use Sandbox to test end-to-end before going Live. Switch to Live only once a test payment has worked.",
    )
    consumer_key = EncryptedTextField(
        blank=True,
        help_text="Consumer Key from Pesapal Developer Settings > API settings.",
    )
    consumer_secret = EncryptedTextField(
        blank=True,
        help_text="Consumer Secret from Pesapal Developer Settings > API settings. Keep this confidential.",
    )
    ipn_id = models.CharField(
        max_length=100, blank=True, editable=False,
        help_text="Auto-registered with Pesapal the first time it's needed. Do not edit manually — "
                   "use the 'Re-register IPN with Pesapal' admin action instead if it ever needs refreshing.",
    )
    ipn_url = models.CharField(
        max_length=300, blank=True, editable=False,
        help_text="The IPN URL that ipn_id was registered for. If the site address changes, the IPN is re-registered automatically.",
    )
    site_base_url = models.URLField(
        blank=True,
        help_text=(
            "Your site's public HTTPS address, e.g. https://yourapp.up.railway.app "
            "(no trailing slash). Used to build the IPN and callback URLs Pesapal calls back on. "
            "Leave blank to auto-detect from the request, but setting it explicitly is more reliable "
            "behind Railway's proxy."
        ),
    )

    cached_token = EncryptedTextField(blank=True, editable=False)
    cached_token_expires_at = models.DateTimeField(null=True, blank=True, editable=False)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pesapal Settings"
        verbose_name_plural = "Pesapal Settings"

    _CONFIG_FIELDS = ('environment', 'consumer_key', 'consumer_secret', 'site_base_url')

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        if self.site_base_url:
            self.site_base_url = self.site_base_url.strip().rstrip('/')

        # A cached token and a registered IPN id belong to ONE environment + key pair +
        # site address. If any of those change (Sandbox -> Live, new keys, new domain)
        # the old token / ipn_id would be rejected by Pesapal, so drop them and let the
        # next payment fetch fresh ones. Token-only saves (update_fields without a config
        # field) skip this check.
        update_fields = kwargs.get('update_fields')
        touches_config = update_fields is None or bool(set(update_fields) & set(self._CONFIG_FIELDS))
        if touches_config:
            previous = type(self).objects.filter(pk=1).values(*self._CONFIG_FIELDS).first()
            current = {name: getattr(self, name) for name in self._CONFIG_FIELDS}
            if previous is not None and previous != current:
                self.ipn_id = ''
                self.ipn_url = ''
                self.cached_token = ''
                self.cached_token_expires_at = None
                if update_fields is not None:
                    kwargs['update_fields'] = list(set(update_fields) | {
                        'ipn_id', 'ipn_url', 'cached_token', 'cached_token_expires_at',
                    })
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # prevent deletion of the singleton

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def base_api_url(self):
        return (
            'https://pay.pesapal.com/v3'
            if self.environment == self.Environment.LIVE
            else 'https://cybqa.pesapal.com/pesapalv3'
        )

    @property
    def is_configured(self):
        return bool(self.consumer_key and self.consumer_secret)

    def __str__(self):
        return f"Pesapal Settings ({self.get_environment_display()})"


class PesapalTransaction(models.Model):
    """
    One record per online payment attempt, whatever it's for. This is the
    single place the platform owner can see every Pesapal payment across
    the whole system — new subscriptions, renewals, branch add-ons, and
    SMS package purchases — instead of scattered, purpose-specific fields.

    `target` (via content_type/object_id) points at the specific record the
    payment is for (a SignupRequest, a Subscription, a BranchAddOn, an
    SMSDebtPurchase, or an SMSReceiptPurchase), so once payment completes we
    know exactly what to activate.
    """

    class Purpose(models.TextChoices):
        SUBSCRIPTION = 'SUBSCRIPTION', 'New Subscription'
        SUBSCRIPTION_RENEWAL = 'SUBSCRIPTION_RENEWAL', 'Subscription Renewal'
        BRANCH_ADDON = 'BRANCH_ADDON', 'Branch Add-on'
        BRANCH_ADDON_RENEWAL = 'BRANCH_ADDON_RENEWAL', 'Branch Add-on Renewal'
        SMS_DEBT_PACKAGE = 'SMS_DEBT_PACKAGE', 'Premium Debt SMS Package'
        SMS_RECEIPT_PACKAGE = 'SMS_RECEIPT_PACKAGE', 'Premium Receipt SMS Package'
        SMS_MARKETING_PACKAGE = 'SMS_MARKETING_PACKAGE', 'Customer Marketing SMS Package'
        DAILY_REPORT_PACKAGE = 'DAILY_REPORT_PACKAGE', 'Intelligent Report SMS Package'
        LAYAWAY_SMS_PACKAGE = 'LAYAWAY_SMS_PACKAGE', 'Layaway SMS Package'
        MARKETPLACE_ORDER = 'MARKETPLACE_ORDER', 'Marketplace Order'
        MARKETPLACE_ONLINE_SELLING = 'MARKETPLACE_ONLINE_SELLING', 'Marketplace Online Selling Subscription'
        MARKETPLACE_ORDER_SMS = 'MARKETPLACE_ORDER_SMS', 'Marketplace Seller Order SMS Add-on'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'                 # created, not yet submitted to Pesapal
        SUBMITTED = 'SUBMITTED', 'Awaiting Payment'     # order submitted, customer redirected to pay
        COMPLETED = 'COMPLETED', 'Completed'            # Pesapal confirmed payment, and our system activated it
        FAILED = 'FAILED', 'Failed'
        CANCELLED = 'CANCELLED', 'Cancelled'
        INVALID = 'INVALID', 'Invalid'
        REVERSED = 'REVERSED', 'Reversed'

    merchant_reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    gateway = models.CharField(max_length=20, default='PESAPAL', db_index=True, help_text='Gateway used for this transaction. Existing records remain pinned to their original provider.')
    order_tracking_id = models.CharField(max_length=100, blank=True, db_index=True)

    purpose = models.CharField(max_length=30, choices=Purpose.choices)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    target = GenericForeignKey('content_type', 'object_id')

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='pesapal_transactions',
        help_text="The logged-in owner this payment belongs to, if any (blank for pre-account signups).",
    )
    payer_name = EncryptedTextField(blank=True)
    payer_email = EncryptedTextField(blank=True)
    payer_phone = EncryptedTextField(blank=True, help_text='Stored AES-256-GCM encrypted.')

    description = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=6, default='TZS')

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    redirect_url = models.URLField(blank=True, help_text="The Pesapal checkout page the customer was sent to.")
    confirmation_code = models.CharField(max_length=100, blank=True)
    payment_method = models.CharField(max_length=60, blank=True, help_text="e.g. M-Pesa, Visa, Airtel Money.")
    status_description = models.CharField(max_length=255, blank=True)
    provider_status_code = models.IntegerField(null=True, blank=True, editable=False)
    payment_account = EncryptedTextField(blank=True, editable=False, help_text='Masked payment account returned by Pesapal (stored encrypted).')
    provider_created_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_checked_at = models.DateTimeField(null=True, blank=True, editable=False)
    ipn_count = models.PositiveIntegerField(default=0, editable=False)
    raw_response = EncryptedTextField(blank=True, help_text="Latest raw gateway response (redacted, stored encrypted) — kept for support/debugging.")
    error_message = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the linked subscription/branch/SMS record was actually activated in our system.",
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
            models.Index(fields=['gateway', 'status', 'created_at']),
        ]

    def __str__(self):
        return f"{self.get_purpose_display()} — TZS {self.amount:,.0f} — {self.get_status_display()}"

    @property
    def is_final(self):
        return self.status in (
            self.Status.COMPLETED, self.Status.FAILED,
            self.Status.CANCELLED, self.Status.INVALID, self.Status.REVERSED,
        )

    @property
    def is_processed(self):
        return self.processed_at is not None
