import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.core.validators import RegexValidator
from django.utils import timezone
from sms_integration.models import SMSDebtPackage, SMSReceiptPackage

# Free plan (freemium): free for life with fixed limits instead of a 7-day countdown.
# Nothing locks and no data is ever withheld; only NEW sales stop once the monthly allowance
# is used, until the owner upgrades. (The old "trial" names are kept so existing code and
# data keep working: a Subscription with is_trial=True IS a Free Plan account.)
FREE_PLAN_MAX_BRANCHES = 1
FREE_PLAN_MAX_USERS = 2            # owner + 1 employee
FREE_PLAN_MONTHLY_SALES = 100      # recorded sales per calendar month
FREE_PLAN_DEMO_PRODUCTS = 10       # sample products loaded at registration
# Setup Offer (done-for-you stock entry). Included with the first 3 months of Starter.
SETUP_INCLUDED_PRODUCTS = 300      # products entered for the base price, in one day
SETUP_EXTRA_BLOCK = 250            # every started block of this many extra products...
SETUP_EXTRA_FEE = 20000            # ...adds this fee (TSh)
SETUP_MAX_STANDARD = 1000          # above this, a custom quote after a short chat
TRIAL_DAYS = 30                    # new sign-ups: one free month with Starter features; locks when it ends until the owner pays
TRIAL_SMS_CREDITS = 20   # first-time allowance per SMS package type (Debt, Receipt, Marketing)


class PaymentSettings(models.Model):
    """
    Singleton (same pattern as sms_integration.SMSSettings) holding the
    payment instructions shown to new customers on the payment page, and
    the current subscription prices. Editable only from Django Admin by
    the platform owner (superadmin) — never hardcoded in templates.
    """
    lipa_namba = models.CharField(
        max_length=30,
        help_text="Payment number (Pay Number / Till), e.g. 5678900."
    )
    payee_name = models.CharField(
        max_length=150,
        help_text="Name customers will see when they pay (registered system owner name)."
    )
    enable_lipa_namba = models.BooleanField(
        default=True,
        verbose_name='Enable manual Lipa Namba payment',
        help_text='When enabled, customers see the manual Lipa Namba option alongside online/mobile-money checkout. When disabled, the manual option is hidden from the public registration payment page.',
    )
    support_phone = models.CharField(
        max_length=20,
        help_text="Phone number customers can call or text with payment proof."
    )
    owner_notification_phone = models.CharField(
        max_length=20,
        help_text="Your phone number — registration SMS notifications are sent here when a customer reaches the payment step."
    )
    instructions_text = models.TextField(
        blank=True,
        help_text="Additional instructions shown on the payment page (e.g. 'Send the receipt to WhatsApp number...')."
    )
    # Public SaaS pricing. Higher tiers unlock more branches and users.
    starter_monthly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('15000'))
    starter_yearly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('150000'))
    business_monthly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('30000'))
    business_yearly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('300000'))
    professional_monthly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('50000'))
    professional_yearly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('500000'))
    enterprise_monthly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('100000'))
    enterprise_yearly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1000000'))
    # Compatibility aliases for older code/reporting; kept in sync with Starter.
    monthly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('15000'), editable=False)
    yearly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('150000'), editable=False)
    branch_addon_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('10000'),
        help_text="Monthly price for each branch beyond the number included in the customer's plan."
    )
    referral_enabled = models.BooleanField(
        default=True, help_text="Customers can invite others and earn a renewal discount for every invited customer who pays.")
    referral_percent_per_invite = models.PositiveSmallIntegerField(
        default=3, help_text="Renewal discount (%) earned for each invited customer who completes a paid subscription (3% x 5 invites = 15%).")
    referral_max_percent = models.PositiveSmallIntegerField(
        default=15, help_text="Highest total discount (%) that can be applied to a single renewal (15% once 5 invited customers have paid).")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Payment Settings"
        verbose_name_plural = "Payment Settings"

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton, same pattern as SMSSettings
        # Keep legacy aliases aligned with Starter for older reports/offline code.
        self.monthly_price = self.starter_monthly_price
        self.yearly_price = self.starter_yearly_price
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # prevent deletion of the singleton

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={
            'lipa_namba': 'CHANGE-ME',
            'payee_name': 'CHANGE-ME',
            'support_phone': 'CHANGE-ME',
            'owner_notification_phone': 'CHANGE-ME',
            'starter_monthly_price': Decimal('15000'), 'starter_yearly_price': Decimal('150000'),
            'business_monthly_price': Decimal('30000'), 'business_yearly_price': Decimal('300000'),
            'professional_monthly_price': Decimal('50000'), 'professional_yearly_price': Decimal('500000'),
            'enterprise_monthly_price': Decimal('100000'), 'enterprise_yearly_price': Decimal('1000000'),
            'branch_addon_price': Decimal('10000'),
            'monthly_price': Decimal('15000'), 'yearly_price': Decimal('150000'),
        })
        return obj

    def price_for(self, plan, tier=None):
        tier = tier or SignupRequest.Tier.STARTER
        prices = {
            SignupRequest.Tier.STARTER: {
                SignupRequest.Plan.MONTHLY: self.starter_monthly_price,
                SignupRequest.Plan.YEARLY: self.starter_yearly_price,
            },
            SignupRequest.Tier.BUSINESS: {
                SignupRequest.Plan.MONTHLY: self.business_monthly_price,
                SignupRequest.Plan.YEARLY: self.business_yearly_price,
            },
            SignupRequest.Tier.PROFESSIONAL: {
                SignupRequest.Plan.MONTHLY: self.professional_monthly_price,
                SignupRequest.Plan.YEARLY: self.professional_yearly_price,
            },
            SignupRequest.Tier.ENTERPRISE: {
                SignupRequest.Plan.MONTHLY: self.enterprise_monthly_price,
                SignupRequest.Plan.YEARLY: self.enterprise_yearly_price,
            },
        }
        return prices.get(tier, prices[SignupRequest.Tier.STARTER])[plan]

    def __str__(self):
        return "Payment Settings"


class SignupRequest(models.Model):
    """
    A prospective customer's signup — created the instant they submit the
    public form, BEFORE any payment happens. There is no payment gateway:
    payment happens off-system (mobile money to a Lipa Namba), so a
    superadmin (platform owner) manually reviews and confirms it here.
    """

    class Plan(models.TextChoices):
        MONTHLY = 'MONTHLY', 'Monthly billing'
        YEARLY = 'YEARLY', 'Yearly billing'

    class Tier(models.TextChoices):
        STARTER = 'STARTER', 'Starter'
        BUSINESS = 'BUSINESS', 'Business'
        PROFESSIONAL = 'PROFESSIONAL', 'Professional'
        ENTERPRISE = 'ENTERPRISE', 'Enterprise'

    class BusinessType(models.TextChoices):
        PHARMACY = 'PHARMACY', 'Pharmacy / Drug store'
        RETAIL = 'RETAIL', 'Retail shop / Duka'
        WHOLESALE = 'WHOLESALE', 'Wholesale / Distribution'
        SUPERMARKET = 'SUPERMARKET', 'Supermarket / Grocery'
        RESTAURANT = 'RESTAURANT', 'Restaurant / Cafe'
        HARDWARE = 'HARDWARE', 'Hardware / Building materials'
        ELECTRONICS = 'ELECTRONICS', 'Electronics / Phones'
        FASHION = 'FASHION', 'Clothing / Boutique'
        BEAUTY = 'BEAUTY', 'Salon / Beauty'
        AGROVET = 'AGROVET', 'Agro-vet / Farm inputs'
        ONLINE_SELLER = 'ONLINE_SELLER', 'Online seller (no shop needed)'
        REAL_ESTATE = 'REAL_ESTATE', 'Property: rentals, houses, land'
        OTHER = 'OTHER', 'Other business'

    class Status(models.TextChoices):
        PENDING_PAYMENT = 'PENDING_PAYMENT', 'Pending Payment'
        ACTIVE = 'ACTIVE', 'Account Activated'
        DELIVERED = 'DELIVERED', 'System Delivered'
        REJECTED = 'REJECTED', 'Rejected'

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    business_type = models.CharField(
        max_length=15, choices=BusinessType.choices, default=BusinessType.PHARMACY,
        help_text="Kind of business. Existing registrations are Pharmacy; new ones can be any business.",
    )
    business_name = models.CharField(max_length=150, help_text="Business name (pharmacy, shop, restaurant, etc.).")
    owner_first_name = models.CharField(max_length=100)
    owner_last_name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    pharmacy_council_fin = models.CharField(
        max_length=7,
        blank=True,
        validators=[RegexValidator(r'^\d{7}$', 'Enter the Pharmacy Council Facility Identification Number (FIN) as exactly 7 digits, e.g. 0101726.')],
        help_text='Pharmacy Council FIN — required only when the business type is Pharmacy. Official premises records use a 7-digit FIN.',
    )
    permit_number = models.CharField(
        max_length=30,
        blank=True,
        help_text='Required for new registrations; may be blank on imported legacy accounts where the legacy database did not store it.',
    )
    permit_expiry_date = models.DateField(
        null=True, blank=True,
        help_text='Required for new registrations; may be blank on imported legacy accounts where the legacy database did not store it.',
    )

    plan = models.CharField(max_length=10, choices=Plan.choices)
    tier = models.CharField(max_length=15, choices=Tier.choices, default=Tier.STARTER)
    sms_package = models.ForeignKey(SMSDebtPackage, null=True, blank=True, on_delete=models.PROTECT, related_name='signup_requests', help_text='Optional Premium debt-SMS package purchased together with the account.')
    receipt_sms_package = models.ForeignKey(SMSReceiptPackage, null=True, blank=True, on_delete=models.PROTECT, related_name='signup_requests', help_text='Optional Premium receipt-link SMS package purchased together with the account.')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_PAYMENT)
    is_trial = models.BooleanField(
        default=False,
        help_text="Free Plan registration: activated immediately, free for life with limited shops, users and monthly sales. No payment taken.",
    )

    google_sub = models.CharField(
        max_length=64, blank=True, default='',
        help_text="Verified Google account id when the owner registered with 'Continue with Google'. Linked to the owner when the account is opened.",
    )

    notes = models.TextField(blank=True, help_text="Internal notes — e.g. mobile money transaction reference.")

    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='confirmed_signups',
    )

    created_pharmacy = models.ForeignKey(
        'pharmacies.Pharmacy', null=True, blank=True, on_delete=models.SET_NULL,
    )
    created_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='signup_request_source',
    )
    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.business_name} ({self.get_plan_display()}) — {self.get_status_display()}"

    @property
    def tier_limits(self):
        return {
            self.Tier.STARTER: {'branches': 1, 'users': 3, 'label': 'Starter'},
            self.Tier.BUSINESS: {'branches': 2, 'users': 10, 'label': 'Business'},
            self.Tier.PROFESSIONAL: {'branches': 5, 'users': 25, 'label': 'Professional'},
            self.Tier.ENTERPRISE: {'branches': 999, 'users': 1000, 'label': 'Enterprise'},
        }.get(self.tier, {'branches': 1, 'users': 3, 'label': 'Starter'})

    @property
    def is_pharmacy(self):
        return self.business_type == self.BusinessType.PHARMACY

    @property
    def tier_display_name(self):
        return self.get_tier_display()

    @property
    def owner_full_name(self):
        return f"{self.owner_first_name} {self.owner_last_name}".strip()


class SubscriptionPaymentReceipt(models.Model):
    """Secure public receipt for a verified SmartPOS subscription payment."""
    class SMSStatus(models.TextChoices):
        NOT_SENT = 'NOT_SENT', 'Not sent'
        SENT = 'SENT', 'Sent'
        FAILED = 'FAILED', 'Failed'

    subscription = models.OneToOneField('Subscription', on_delete=models.CASCADE, related_name='payment_receipt')
    access_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    receipt_number = models.CharField(max_length=40, unique=True, editable=False)
    sms_status = models.CharField(max_length=12, choices=SMSStatus.choices, default=SMSStatus.NOT_SENT)
    sms_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    sms_attempts = models.PositiveIntegerField(default=0, editable=False)
    sms_last_error = models.TextField(blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.receipt_number} — {self.subscription.owner.get_full_name() or self.subscription.owner.username}"


class Subscription(models.Model):
    """
    Tracks a paying customer's access on THIS shared deployment.
    Customers can choose Monthly or Yearly subscriptions.
    """

    class Plan(models.TextChoices):
        MONTHLY = 'MONTHLY', 'Kila Mwezi'
        YEARLY = 'YEARLY', 'Kila Mwaka'

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        EXPIRED = 'EXPIRED', 'Expired'
        CANCELLED = 'CANCELLED', 'Cancelled'

    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscription')
    signup_request = models.OneToOneField(SignupRequest, on_delete=models.SET_NULL, null=True, blank=True)
    plan = models.CharField(max_length=10, choices=Plan.choices)
    tier = models.CharField(max_length=15, choices=SignupRequest.Tier.choices, default=SignupRequest.Tier.STARTER)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField()
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2)
    payment_reference = models.CharField(max_length=150, blank=True)
    premium_daily_reports_enabled = models.BooleanField(default=False, help_text='Allow Premium Daily Sales Intelligence for this account. Professional/Enterprise tiers include it automatically.')
    is_trial = models.BooleanField(
        default=False, db_index=True,
        help_text='Free Plan account (1 shop, 2 users, limited monthly sales). Never locks; cleared automatically when the customer upgrades.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['end_date']

    def __str__(self):
        return f"{self.owner.get_full_name() or self.owner.username} — {self.get_plan_display()} hadi {self.end_date}"

    @property
    def is_timed_trial(self):
        """One-month free trial (end date is real). Older lifetime Free Plan accounts end in 2099 and keep their limits."""
        from sms_integration.models import NO_EXPIRY_DATE
        return bool(self.is_trial and self.end_date < NO_EXPIRY_DATE)

    @property
    def tier_limits(self):
        if self.is_trial and not self.is_timed_trial:
            # Legacy lifetime Free Plan: 1 shop, 2 users. Upgrading clears is_trial and restores the paid tier limits.
            return {'branches': FREE_PLAN_MAX_BRANCHES, 'users': FREE_PLAN_MAX_USERS, 'assistants': 0}
        return {
            SignupRequest.Tier.STARTER: {'branches': 1, 'users': 3, 'assistants': 1},
            SignupRequest.Tier.BUSINESS: {'branches': 2, 'users': 10, 'assistants': 3},
            SignupRequest.Tier.PROFESSIONAL: {'branches': 5, 'users': 25, 'assistants': 5},
            SignupRequest.Tier.ENTERPRISE: {'branches': 999, 'users': 1000, 'assistants': 10},
        }.get(self.tier, {'branches': 1, 'users': 3, 'assistants': 1})

    @property
    def assistant_limit(self):
        """Maximum active Manager Assistants included in this subscription tier."""
        return self.tier_limits['assistants']

    @property
    def included_branches(self):
        return self.tier_limits['branches']

    @property
    def included_users(self):
        return self.tier_limits['users']

    @property
    def is_expired(self):
        return self.end_date < timezone.localdate()

    @property
    def days_remaining(self):
        return (self.end_date - timezone.localdate()).days

    @property
    def is_free_plan(self):
        return bool(self.is_trial)

    @property
    def is_trial_active(self):
        return bool(self.is_trial and self.status == self.Status.ACTIVE and not self.is_expired)

    @property
    def is_trial_ended(self):
        # The Free Plan never ends on its own; only an admin cancelling it counts.
        return bool(self.is_trial and self.status != self.Status.ACTIVE)


class ImpersonationSession(models.Model):
    """Audit trail of every time the platform owner enters a customer's workspace.

    Created when the owner confirms the selection form, closed on exit / expiry.
    Names are snapshotted so the record stays meaningful if a branch or user is
    later renamed or deleted.
    """
    class Mode(models.TextChoices):
        VIEW = 'VIEW', 'View only'
        FULL = 'FULL', 'Full access'

    class Reason(models.TextChoices):
        SUPPORT = 'SUPPORT', 'Customer support request'
        INSPECTION = 'INSPECTION', 'Routine inspection / review'
        BILLING = 'BILLING', 'Subscription / billing check'
        TROUBLESHOOT = 'TROUBLESHOOT', 'Troubleshooting a problem'
        OTHER = 'OTHER', 'Other'

    DURATION_MINUTES = 60

    admin = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='impersonations_started')
    admin_username = models.CharField(max_length=150)
    target = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='impersonated_sessions')
    target_username = models.CharField(max_length=150)
    pharmacy_id_snapshot = models.PositiveIntegerField(null=True, blank=True)
    pharmacy_name = models.CharField(max_length=150, blank=True)
    mode = models.CharField(max_length=4, choices=Mode.choices, default=Mode.VIEW)
    reason = models.CharField(max_length=14, choices=Reason.choices, default=Reason.SUPPORT)
    note = models.CharField(max_length=300, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_cause = models.CharField(max_length=12, blank=True)  # EXIT / EXPIRED / SWITCHED
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['-started_at']

    @property
    def expires_at(self):
        from datetime import timedelta
        return self.started_at + timedelta(minutes=self.DURATION_MINUTES)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_active(self):
        return self.ended_at is None and not self.is_expired

    def close(self, cause):
        if self.ended_at is None:
            self.ended_at = timezone.now()
            self.end_cause = cause
            self.save(update_fields=['ended_at', 'end_cause'])

    def __str__(self):
        return f'{self.admin_username} -> {self.target_username} ({self.pharmacy_name}) {self.started_at:%Y-%m-%d %H:%M}'


class ReferralCode(models.Model):
    """A customer's personal invite code (one per shop owner), shared as /jiunge/?ref=CODE."""
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='referral_code')
    code = models.CharField(max_length=12, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.code} ({self.owner})"


class Referral(models.Model):
    """One invited signup. The reward is earned only when the invited customer PAYS.

    PENDING -> QUALIFIED (reward available to the referrer) -> USED (applied to a renewal).
    """
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Waiting for payment'
        QUALIFIED = 'QUALIFIED', 'Reward available'
        USED = 'USED', 'Reward used'
        VOID = 'VOID', 'Void'

    referrer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='referrals_made')
    signup = models.OneToOneField(SignupRequest, on_delete=models.CASCADE, related_name='referral')
    code_used = models.CharField(max_length=12)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    reward_percent = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    qualified_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.referrer} -> {self.signup.business_name} ({self.status})"


# ---------------------------------------------------------------------------
# System notices: professional status banners shown to customers inside the app
# (improvements, planned maintenance, incidents, "resolved" follow-ups).
# Managed by the platform owner at /hq/notices/. Cached for a few seconds, so a
# notice is live almost instantly without touching the database on every page.
# ---------------------------------------------------------------------------
NOTICE_CACHE_KEY = 'hq:system_notices:active:v2'


def invalidate_notice_cache():
    try:
        from django.core.cache import cache
        cache.delete(NOTICE_CACHE_KEY)
    except Exception:
        pass


class SystemNotice(models.Model):
    class Kind(models.TextChoices):
        IMPROVEMENT = 'IMPROVEMENT', 'Improvement / Maboresho'
        MAINTENANCE = 'MAINTENANCE', 'Planned maintenance / Matengenezo'
        INCIDENT = 'INCIDENT', 'Issue being fixed / Changamoto'
        RESOLVED = 'RESOLVED', 'Issue resolved / Imetatuliwa'
        INFO = 'INFO', 'General notice / Taarifa'

    class Audience(models.TextChoices):
        ALL = 'ALL', 'Everyone who is signed in'
        MANAGERS = 'MANAGERS', 'Shop owners and assistants only'

    class Workflow(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft (not visible to customers)'
        PUBLISHED = 'PUBLISHED', 'Published'

    # Customer-facing wording (Swahili) and colour family for each kind.
    BADGES = {
        'IMPROVEMENT': 'Maboresho',
        'MAINTENANCE': 'Matengenezo',
        'INCIDENT': 'Changamoto',
        'RESOLVED': 'Imetatuliwa',
        'INFO': 'Taarifa',
    }
    TONES = {
        'IMPROVEMENT': 'brand',
        'MAINTENANCE': 'warning',
        'INCIDENT': 'danger',
        'RESOLVED': 'success',
        'INFO': 'info',
    }
    # Lower number = shown first when several notices are live at once.
    PRIORITY = {'INCIDENT': 0, 'MAINTENANCE': 1, 'RESOLVED': 2, 'IMPROVEMENT': 3, 'INFO': 4}

    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.INFO)
    title = models.CharField(max_length=100)
    message = models.TextField(max_length=500, help_text='Short and clear: what happened, what to expect, what you are doing about it.')
    audience = models.CharField(max_length=10, choices=Audience.choices, default=Audience.ALL)
    link_url = models.CharField(max_length=300, blank=True, help_text='Optional page with more details (https://... or /path).')
    link_label = models.CharField(max_length=40, blank=True, help_text='Button text. Default: Soma zaidi.')
    show_on_login = models.BooleanField(default=False, help_text='Also show on the login page, so customers see it before they sign in.')
    dismissible = models.BooleanField(default=True, help_text='Let customers close the banner (it comes back if you edit the notice).')
    notify_in_app = models.BooleanField(default=True, help_text='Also put this on each customer\'s notification bell (and send a phone push if they have it on). Untick it for small typo fixes.')
    notified_at = models.DateTimeField(null=True, blank=True, help_text='When the bell notifications were sent (so they are never sent twice).')
    is_active = models.BooleanField(default=True, help_text='Master switch. Turn off to hide the notice immediately.')
    # Review workflow: every notice starts as a DRAFT that customers can never see.
    # It only reaches customers after the owner has reviewed the preview and published it.
    publish_state = models.CharField(max_length=10, choices=Workflow.choices, default=Workflow.DRAFT)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    starts_at = models.DateTimeField(default=timezone.now, help_text='Shown from this time.')
    ends_at = models.DateTimeField(null=True, blank=True, help_text='Hidden automatically after this time. Leave empty to keep it until you switch it off.')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['is_active', 'starts_at', 'ends_at'], name='hq_notice_live_idx')]

    def __str__(self):
        return f'{self.get_kind_display()}: {self.title}'

    @property
    def badge(self):
        return self.BADGES.get(self.kind, 'Taarifa')

    @property
    def tone(self):
        return self.TONES.get(self.kind, 'info')

    @property
    def version(self):
        """Changes whenever the notice is edited, so a dismissed banner re-appears."""
        return str(int(self.updated_at.timestamp())) if self.updated_at else '0'

    @property
    def is_published(self):
        return self.publish_state == self.Workflow.PUBLISHED

    @property
    def status(self):
        now = timezone.now()
        if not self.is_published:
            return 'DRAFT'
        if not self.is_active:
            return 'OFF'
        if self.starts_at and self.starts_at > now:
            return 'SCHEDULED'
        if self.ends_at and self.ends_at <= now:
            return 'EXPIRED'
        return 'LIVE'

    # --- review workflow -------------------------------------------------------
    PLACEHOLDER = '__'

    def review_checks(self, now=None):
        """Return (blocking, warnings): plain-English problems found before publishing.

        Blocking problems stop the owner from publishing; warnings are shown but allowed.
        """
        now = now or timezone.now()
        blocking, warnings = [], []
        text = f'{self.title} {self.message}'
        if self.PLACEHOLDER in text:
            blocking.append('The text still contains a blank to fill in ("__"). Replace it with the real date or time.')
        effective_start = max(self.starts_at, now) if self.starts_at else now
        if self.ends_at and self.ends_at <= effective_start:
            blocking.append('The "Hide after" time has already passed, so customers would never see this notice.')
        if len(self.message.strip()) < 20:
            warnings.append('The message is very short. Tell customers what is affected and what to expect.')
        if not self.dismissible and not self.ends_at:
            warnings.append('Customers cannot close this banner and it has no end time. It stays until you switch it off.')
        if self.starts_at and self.starts_at > now:
            warnings.append('It is scheduled for later, so customers will not see it until the start time.')
        if self.kind == self.Kind.INCIDENT and self.dismissible:
            warnings.append('For an ongoing issue, consider turning off "Customers can close the banner" so nobody misses it.')
        return blocking, warnings

    def publish(self, user=None, now=None):
        now = now or timezone.now()
        self.publish_state = self.Workflow.PUBLISHED
        self.is_active = True
        self.published_at = now
        self.published_by = user if getattr(user, 'pk', None) else None
        self.notified_at = None  # a fresh publish may notify again (the owner can untick the option)
        if not self.starts_at or self.starts_at < now:
            # A notice that waited as a draft starts at the moment it is really published.
            self.starts_at = now
        self.save()

    def unpublish(self):
        self.publish_state = self.Workflow.DRAFT
        self.save(update_fields=['publish_state', 'updated_at'])

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        invalidate_notice_cache()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        invalidate_notice_cache()
        return result
