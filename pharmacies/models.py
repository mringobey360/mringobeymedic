import datetime
from datetime import timedelta

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.utils import timezone
from hq.models import SignupRequest, Subscription


class Pharmacy(models.Model):
    """
    A single pharmacy branch/location. Every product, batch, sale, debt, and
    employee belongs to exactly one Pharmacy — this is what keeps each
    branch's stock, sales, and reports fully independent from the others.
    A Shop Owner/Supervisor can manage more than one Pharmacy and switch
    between them; a Shop Employee is assigned to exactly one.
    """
    name = models.CharField(max_length=150, unique=True)
    business_type = models.CharField(
        max_length=15, choices=SignupRequest.BusinessType.choices, default=SignupRequest.BusinessType.PHARMACY,
        help_text="Kind of business this branch runs. Existing branches are Pharmacy.",
    )
    location = models.CharField(max_length=255, blank=True, help_text="Physical address or area, e.g. 'Kariakoo, Dar es Salaam'.")
    phone_number = models.CharField(max_length=20, blank=True, help_text="Branch contact number (optional).")
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True, help_text="Branch latitude used for attendance geofencing.")
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True, help_text="Branch longitude used for attendance geofencing.")
    geofence_radius_m = models.PositiveIntegerField(default=150, help_text="Maximum distance in metres allowed for automatic attendance verification.")
    presence_check_interval_minutes = models.PositiveIntegerField(default=15, help_text="Minimum interval between automatic employee presence checks while the workday page is active.")
    attendance_lunch_rescan_required = models.BooleanField(
        default=False,
        help_text="If enabled, employees must complete one additional attendance scan during the configured lunch window."
    )
    attendance_lunch_rescan_start = models.TimeField(
        default='12:00',
        help_text="Start of the optional lunch attendance verification window."
    )
    attendance_lunch_rescan_end = models.TimeField(
        default='14:00',
        help_text="End of the optional lunch attendance verification window."
    )
    attendance_outside_grace_minutes = models.PositiveIntegerField(
        default=5,
        help_text="Minutes an employee may remain outside the geofence before the manager is alerted."
    )
    attendance_qr_requires_location = models.BooleanField(
        default=True,
        help_text="Employees must be physically inside the workplace radius (device GPS) when they scan the branch QR to check in, claim a meal or check out. Outside = no scan, no meal."
    )
    class AttendanceSMSMode(models.TextChoices):
        OFF = 'OFF', 'Off - no attendance SMS'
        EXCEPTIONS = 'EXCEPTIONS', 'Exceptions only (recommended) - late, early leave, approval requests'
        ALL = 'ALL', 'Every check-in and check-out'

    attendance_sms_mode = models.CharField(
        max_length=12, choices=AttendanceSMSMode.choices, default=AttendanceSMSMode.OFF,
        help_text="Choose how the branch owner is told about employee attendance by SMS. 'Exceptions only' keeps SMS cost low and only alerts you when something needs attention.",
    )
    attendance_sms_work_start = models.TimeField(
        default=datetime.time(8, 0),
        help_text="Official time employees should start work. Used to decide if a check-in is late.",
    )
    attendance_sms_work_end = models.TimeField(
        default=datetime.time(17, 0),
        help_text="Official closing time. Used to decide if a check-out is early.",
    )
    attendance_sms_grace_minutes = models.PositiveIntegerField(
        default=10,
        help_text="Minutes of tolerance before a check-in counts as late (or a check-out as early).",
    )
    location_accuracy_m = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text="Accuracy reported when the branch GPS point was captured.")
    location_captured_at = models.DateTimeField(null=True, blank=True, help_text="When the branch GPS point was last verified/captured.")
    location_captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='captured_branch_locations', help_text="Manager who last captured the branch GPS point."
    )
    location_source = models.CharField(max_length=12, choices=[('DEVICE','Device GPS'),('MANUAL','Manual')], default='MANUAL')
    email = models.EmailField(blank=True)
    registration_number = models.CharField(max_length=100, blank=True, help_text="Legacy/display registration number. Pharmacy Council FIN is stored separately.")
    pharmacy_council_fin = models.CharField(max_length=7, blank=True, help_text="Pharmacy Council Facility Identification Number (FIN), exactly 7 digits.")
    permit_number = models.CharField(max_length=30, blank=True, help_text="Current Pharmacy Council permit number.")
    permit_expiry_date = models.DateField(null=True, blank=True, help_text="Current Pharmacy Council permit expiry date.")
    logo = models.ImageField(upload_to='pharmacy_logos/', blank=True, null=True, help_text="Your pharmacy logo. If blank, the SmartPOS system logo is used on receipts.")
    receipt_header = models.CharField(max_length=180, blank=True, help_text="Optional line shown under the pharmacy name on receipts.")
    receipt_footer = models.CharField(max_length=255, blank=True, help_text="Optional message shown at the bottom of receipts.")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_sale_number = models.PositiveIntegerField(
        default=0,
        help_text="Internal counter — the last receipt/sale number issued at this branch. Do not edit manually.",
    )

    class Meta:
        verbose_name_plural = "Pharmacies"
        ordering = ['name']

    def __str__(self):
        return self.name


class BranchAddOn(models.Model):
    """
    Billing record for a branch beyond an owner's first (included) branch.

    A new branch is created with the pharmacy record locked (Pharmacy.is_active
    stays False and PharmacyContextMiddleware will never select it as the
    active branch) until a superadmin confirms the off-system mobile-money
    payment — the same manual-confirmation pattern used for the main
    subscription (hq.SignupRequest / hq.Subscription). No auto-renewal or
    proration: the branch is billed a flat monthly amount from the day it's
    activated, matching how the main subscription already works here.

    Cancelling never deletes the Pharmacy or any of its stock/sales/employee
    data — it only stops the branch from being usable going forward.
    """

    class Status(models.TextChoices):
        PENDING_PAYMENT = 'PENDING_PAYMENT', 'Pending Payment'
        ACTIVE = 'ACTIVE', 'Active'
        CANCELLED = 'CANCELLED', 'Cancelled'

    pharmacy = models.OneToOneField(Pharmacy, on_delete=models.CASCADE, related_name='addon')
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='branch_addons',
        help_text="The Shop Owner/Supervisor billed for this additional branch.",
    )
    package_tier = models.CharField(
        max_length=15, choices=SignupRequest.Tier.choices, default=SignupRequest.Tier.STARTER,
        help_text='Core package selected specifically for this branch.',
    )
    billing_plan = models.CharField(
        max_length=10, choices=Subscription.Plan.choices, default=Subscription.Plan.MONTHLY,
        help_text='Billing period selected specifically for this branch.',
    )
    billing_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Price snapshot for the selected branch package and billing period.',
    )
    payment_reference = models.CharField(
        max_length=120, blank=True,
        help_text='Mobile-money transaction reference supplied by the owner before admin verification.',
    )
    monthly_price = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Price at the time this add-on was requested (kept even if the system-wide price later changes).",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_PAYMENT)
    requested_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    next_billing_date = models.DateField(
        null=True, blank=True,
        help_text="Set on activation/renewal. Flat monthly billing — no proration.",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='confirmed_branch_addons',
        help_text="Superadmin who confirmed the mobile-money payment.",
    )

    class Meta:
        ordering = ['-requested_at']

    def save(self, *args, **kwargs):
        if not self.billing_amount or self.billing_amount <= 0:
            self.billing_amount = self.monthly_price
        if not self.monthly_price and self.billing_amount:
            self.monthly_price = self.billing_amount
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.pharmacy.name} add-on — {self.get_package_tier_display()} / {self.get_billing_plan_display()} — {self.get_status_display()}"

    def refresh_price(self, payment_settings=None):
        """Re-price this add-on from the CURRENT package prices (HQ > Payment settings)."""
        if payment_settings is None:
            from hq.models import PaymentSettings
            payment_settings = PaymentSettings.load()
        if self.status == self.Status.CANCELLED:
            return self.billing_amount
        try:
            live = payment_settings.price_for(self.billing_plan, self.package_tier)
            monthly = payment_settings.price_for(Subscription.Plan.MONTHLY, self.package_tier)
        except Exception:
            return self.billing_amount
        if live and live > 0 and (self.billing_amount != live or self.monthly_price != monthly):
            self.billing_amount = live
            self.monthly_price = monthly
            self.save(update_fields=['billing_amount', 'monthly_price'])
        return self.billing_amount

    @property
    def is_overdue(self):
        return (
            self.status == self.Status.ACTIVE
            and self.next_billing_date is not None
            and self.next_billing_date < timezone.localdate()
        )

    RENEWAL_WINDOW_DAYS = 10

    @property
    def renewal_due(self):
        """True when an ACTIVE add-on is overdue or due within the renewal window."""
        if self.status != self.Status.ACTIVE:
            return False
        if self.next_billing_date is None:
            return True
        return self.next_billing_date <= timezone.localdate() + timedelta(days=self.RENEWAL_WINDOW_DAYS)

    def activate(self, confirmed_by):
        self.status = self.Status.ACTIVE
        self.activated_at = timezone.now()
        self.next_billing_date = timezone.localdate() + (timedelta(days=365) if self.billing_plan == Subscription.Plan.YEARLY else timedelta(days=30))
        self.confirmed_by = confirmed_by
        self.save(update_fields=['status', 'activated_at', 'next_billing_date', 'confirmed_by'])
        self.pharmacy.is_active = True
        self.pharmacy.save(update_fields=['is_active'])
        BranchBillingEvent.log(self.pharmacy, 'activated', due_date=self.next_billing_date, detail='Confirmed by platform admin')

    # ------------------------------------------------------------------
    # Online (Pesapal) payment paths. Both lock the row and re-check its
    # state inside the lock, so an IPN callback, a browser poll and an HQ
    # manual confirmation racing each other can never apply a payment twice.
    # ------------------------------------------------------------------
    def activate_from_payment(self, reference=''):
        """Unlock a newly added branch after a confirmed online payment.
        Returns True if it was activated now, False if it was not in PENDING_PAYMENT."""
        with transaction.atomic():
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            if locked.status != self.Status.PENDING_PAYMENT:
                self.status = locked.status
                return False
            locked.status = self.Status.ACTIVE
            locked.activated_at = timezone.now()
            locked.next_billing_date = timezone.localdate() + (timedelta(days=365) if locked.billing_plan == Subscription.Plan.YEARLY else timedelta(days=30))
            locked.confirmed_by = None
            if reference:
                locked.payment_reference = reference[:120]
            locked.save(update_fields=['status', 'activated_at', 'next_billing_date', 'confirmed_by', 'payment_reference'])
            type(locked.pharmacy).objects.filter(pk=locked.pharmacy_id).update(is_active=True)
        self.refresh_from_db()
        BranchBillingEvent.log(self.pharmacy, 'activated', due_date=self.next_billing_date, detail='Online payment')
        return True

    def renew_from_payment(self, reference=''):
        """Extend an ACTIVE branch by one billing month after a confirmed online payment.
        The new month starts from the current due date (so paying early never wastes days),
        or from today if the branch is already overdue. Returns False if the branch is not ACTIVE."""
        with transaction.atomic():
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            if locked.status != self.Status.ACTIVE:
                self.status = locked.status
                return False
            today = timezone.localdate()
            base = max(locked.next_billing_date, today) if locked.next_billing_date else today
            locked.next_billing_date = base + (timedelta(days=365) if locked.billing_plan == Subscription.Plan.YEARLY else timedelta(days=30))
            if reference:
                locked.payment_reference = reference[:120]
            locked.save(update_fields=['next_billing_date', 'payment_reference'])
            type(locked.pharmacy).objects.filter(pk=locked.pharmacy_id).update(is_active=True)
        self.refresh_from_db()
        BranchBillingEvent.log(self.pharmacy, 'renewed', due_date=self.next_billing_date, detail='Online payment')
        return True

    def cancel(self):
        """Stop the branch from being usable. Never deletes pharmacy data."""
        self.status = self.Status.CANCELLED
        self.cancelled_at = timezone.now()
        self.save(update_fields=['status', 'cancelled_at'])
        self.pharmacy.is_active = False
        self.pharmacy.save(update_fields=['is_active'])



class BranchBillingEvent(models.Model):
    """Append-only audit trail of a branch's billing milestones and renewals
    (reminders sent, grace/read-only/locked transitions, renewals, activations)."""

    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name='billing_events')
    kind = models.CharField(max_length=30, help_text="e.g. due-10, due-3, due-1, due-0, grace, grace-last, readonly, locked, renewed, activated")
    stage = models.CharField(max_length=12, blank=True)
    due_date = models.DateField(null=True, blank=True)
    detail = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['pharmacy', '-created_at'], name='pharm_billing_ev_idx')]

    def __str__(self):
        return f"{self.pharmacy.name} · {self.kind} · {self.created_at:%Y-%m-%d}"

    @classmethod
    def log(cls, pharmacy, kind, *, stage='', due_date=None, detail=''):
        """Never raises: an audit failure must not break a payment or a page."""
        try:
            return cls.objects.create(pharmacy=pharmacy, kind=kind, stage=stage, due_date=due_date, detail=detail[:255])
        except Exception:
            return None


class ReceiptSettings(models.Model):
    """Per-branch receipt design chosen by the manager (colour, logo, layout...).

    A branch with no row here simply gets the standard SmartPOS receipt (see
    core.receipt_pdf.DEFAULT_STYLE), so existing branches keep working unchanged.
    Branch name, address, phone, FIN and permit always come from the Pharmacy
    record itself; this model only controls how they are presented.
    """
    class FontFamily(models.TextChoices):
        HELVETICA = 'HELVETICA', 'Modern sans-serif'
        TIMES = 'TIMES', 'Classic serif'
        COURIER = 'COURIER', 'Typewriter'

    class Paper(models.IntegerChoices):
        MM80 = 80, '80 mm (standard)'
        MM58 = 58, '58 mm (compact)'

    class HeaderLayout(models.TextChoices):
        LEFT = 'LEFT', 'Logo on the left'
        CENTER = 'CENTER', 'Centered'

    class PanelStyle(models.TextChoices):
        TINT = 'TINT', 'Soft brand tint'
        GREY = 'GREY', 'Light grey'
        OUTLINE = 'OUTLINE', 'Outline only'

    class TitleStyle(models.TextChoices):
        LINES = 'LINES', 'Lines above & below'
        BAND = 'BAND', 'Solid colour bar'

    class LogoSize(models.TextChoices):
        SMALL = 'SMALL', 'Small'
        MEDIUM = 'MEDIUM', 'Medium'
        LARGE = 'LARGE', 'Large'

    pharmacy = models.OneToOneField(Pharmacy, on_delete=models.CASCADE, related_name='receipt_settings')

    primary_color = models.CharField(
        max_length=7, default='#2F8F3B',
        validators=[RegexValidator(r'^#[0-9A-Fa-f]{6}$', 'Enter a hex colour such as #2F8F3B.')],
        help_text='Main receipt colour: branch name, title, lines and panels.',
    )
    text_color = models.CharField(
        max_length=7, default='#172033',
        validators=[RegexValidator(r'^#[0-9A-Fa-f]{6}$', 'Enter a hex colour such as #172033.')],
        help_text='Colour of the normal receipt text (items, details, amounts).',
    )
    background_color = models.CharField(
        max_length=7, default='#FFFFFF',
        validators=[RegexValidator(r'^#[0-9A-Fa-f]{6}$', 'Enter a hex colour such as #FFFFFF.')],
        help_text='Paper colour of the receipt (light colours only).',
    )
    panel_style = models.CharField(max_length=7, choices=PanelStyle.choices, default=PanelStyle.TINT,
                                   help_text='Look of the details box and the total box.')
    title_style = models.CharField(max_length=5, choices=TitleStyle.choices, default=TitleStyle.LINES,
                                   help_text='Look of the receipt title.')
    font_family = models.CharField(max_length=10, choices=FontFamily.choices, default=FontFamily.HELVETICA)
    paper_width_mm = models.PositiveSmallIntegerField(choices=Paper.choices, default=Paper.MM80)
    header_layout = models.CharField(max_length=6, choices=HeaderLayout.choices, default=HeaderLayout.LEFT)
    logo_size = models.CharField(max_length=6, choices=LogoSize.choices, default=LogoSize.MEDIUM)
    show_logo = models.BooleanField(default=True)

    title_text = models.CharField(max_length=40, blank=True, default='OFFICIAL SALES RECEIPT',
                                  help_text='Heading shown above the sale details.')
    return_policy = models.CharField(max_length=255, blank=True,
                                     help_text='Optional terms, e.g. returns / exchange policy.')
    watermark_text = models.CharField(max_length=30, blank=True,
                                      help_text='Faint diagonal text. Blank = the branch name.')

    show_address = models.BooleanField(default=True)
    show_contacts = models.BooleanField(default=True)
    show_registration = models.BooleanField(default=True)
    show_customer = models.BooleanField(default=True)
    show_served_by = models.BooleanField(default=True)
    show_batch_details = models.BooleanField(default=True)
    show_barcode = models.BooleanField(default=True)
    show_watermark = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+',
    )

    # Fields copied by "apply to all my branches" and read by the PDF renderer.
    STYLE_FIELDS = (
        'primary_color', 'text_color', 'background_color', 'panel_style', 'title_style', 'font_family', 'paper_width_mm', 'header_layout', 'logo_size', 'show_logo',
        'title_text', 'return_policy', 'watermark_text', 'show_address', 'show_contacts',
        'show_registration', 'show_customer', 'show_served_by', 'show_batch_details',
        'show_barcode', 'show_watermark',
    )

    class Meta:
        verbose_name = 'Receipt settings'
        verbose_name_plural = 'Receipt settings'

    def __str__(self):
        return f'Receipt design - {self.pharmacy}'
