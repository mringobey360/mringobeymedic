"""
Activation logic shared between two callers:

1. The superadmin manually confirming a mobile-money payment
   (hq.views.activate_subscription_view / renew_subscription_view).
2. Pesapal online payment completing automatically
   (payments.services, once Pesapal confirms COMPLETED).

Keeping this in one place means a customer gets exactly the same outcome —
same notifications, same SMS, same receipt — no matter which payment path
they used.
"""
import logging
import secrets
import string
from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from accounts.phone_utils import normalize_phone_number
from notifications.models import Notification
from notifications.utils import notify_many
from pharmacies.models import Pharmacy
from sms_integration.models import (
    CustomerSMSCredentials, NO_EXPIRY_DATE, SMSDebtPackage, SMSDebtSubscription, SMSLog,
    SMSMarketingPackage, SMSMarketingSubscription, SMSReceiptPackage, SMSReceiptSubscription, SMSWallet,
)
from sms_integration.utils import send_sms

from .models import (
    FREE_PLAN_MAX_USERS, FREE_PLAN_MONTHLY_SALES, PaymentSettings, SignupRequest, Subscription, SubscriptionPaymentReceipt, TRIAL_DAYS, TRIAL_SMS_CREDITS,
)

logger = logging.getLogger('hq.services')


def _generate_temp_password(length=12):
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))


def _unique_username(base):
    base = ''.join(ch for ch in base.lower().replace(' ', '.') if ch.isalnum() or ch == '.') or 'mteja'
    username = base
    n = 1
    while User.objects.filter(username__iexact=username).exists():
        n += 1
        username = f"{base}{n}"
    return username


def _unique_pharmacy_name(base_name, phone_number):
    if not Pharmacy.objects.filter(name__iexact=base_name).exists():
        return base_name
    return f"{base_name} ({phone_number[-4:]})"


def _send_subscription_payment_sms(phone_number, message):
    try:
        return send_sms(
            normalize_phone_number(phone_number), message,
            category=SMSLog.Category.PAYMENT_RECEIPT, owner=None, consume_wallet=False,
        )
    except Exception:
        return False


def _qualify_referral(signup, amount):
    try:
        from . import referrals
        referrals.qualify(signup, amount_paid=amount)
    except Exception:
        logger.exception('Referral qualification failed for signup #%s', getattr(signup, 'pk', None))


def activate_signup_from_payment(signup: SignupRequest, *, reference_note='', confirmed_by=None, amount_paid=None):
    """
    Creates the Pharmacy + Shop Owner account, opens the Subscription, and
    SMSes login details — identical outcome whether triggered by an admin
    confirming a manual payment or by an automatic Pesapal completion.

    * The signup row is locked and re-checked, so a manual HQ confirmation and an
      online payment arriving together can never create two accounts.
    * All database work is ONE transaction: if any step fails nothing is left behind
      (no orphan pharmacy / user without a subscription).
    * SMS and notifications go out only after the transaction has committed.
    * `amount_paid` is the amount actually received (online); manual flow derives it
      from the current price list.
    The passed-in `signup` instance is refreshed so callers can read created_pharmacy etc.
    """
    with transaction.atomic():
        locked = SignupRequest.objects.select_for_update().get(pk=signup.pk)
        if locked.status != SignupRequest.Status.PENDING_PAYMENT:
            signup.refresh_from_db()
            return getattr(signup, 'subscription', None)

        payment_settings = PaymentSettings.load()
        base_amount = payment_settings.price_for(locked.plan, locked.tier)
        sms_amount = locked.sms_package.price if locked.sms_package_id else 0
        receipt_sms_amount = locked.receipt_sms_package.price if locked.receipt_sms_package_id else 0
        amount = amount_paid if amount_paid is not None else base_amount + sms_amount + receipt_sms_amount

        phone_number = normalize_phone_number(locked.phone_number)
        username = _unique_username(f"{locked.owner_first_name}{locked.owner_last_name}")
        pharmacy_name = _unique_pharmacy_name(locked.business_name, phone_number)
        temp_password = _generate_temp_password()

        pharmacy = Pharmacy.objects.create(
            name=pharmacy_name, location=locked.location, phone_number=phone_number,
            business_type=locked.business_type,
            registration_number=locked.pharmacy_council_fin,
            pharmacy_council_fin=locked.pharmacy_council_fin,
            permit_number=locked.permit_number,
            permit_expiry_date=locked.permit_expiry_date,
        )
        owner = User.objects.create_user(
            username=username, password=temp_password,
            first_name=locked.owner_first_name, last_name=locked.owner_last_name,
            phone_number=phone_number, role=User.Role.ADMIN,
            is_staff=False, is_superuser=False, must_change_password=True,
        )
        owner.managed_pharmacies.add(pharmacy)
        _link_google_identity(locked, owner)

        today = timezone.localdate()
        end_date = today + (timedelta(days=365) if locked.plan == SignupRequest.Plan.YEARLY else timedelta(days=30))
        subscription = Subscription.objects.create(
            owner=owner, signup_request=locked, plan=locked.plan, tier=locked.tier, status=Subscription.Status.ACTIVE,
            start_date=today, end_date=end_date, amount_paid=amount, payment_reference=reference_note,
        )
        # Referral programme: the invited customer has now paid, so the inviter earns the reward.
        _signup_for_referral, _paid_amount = locked, amount
        transaction.on_commit(lambda: _qualify_referral(_signup_for_referral, _paid_amount))
        payment_receipt = SubscriptionPaymentReceipt.objects.create(
            subscription=subscription,
            receipt_number=f"PM-SUB-{subscription.pk:08d}",
        )
        SMSWallet.objects.get_or_create(owner=owner, defaults={'credits_granted': 500, 'credits_used': 0})
        CustomerSMSCredentials.objects.get_or_create(owner=owner)
        if locked.sms_package_id:
            package = locked.sms_package
            SMSDebtSubscription.objects.create(
                owner=owner, package=package, sms_credits=package.sms_credits, sms_used=0,
                start_date=today, end_date=NO_EXPIRY_DATE,
                status=SMSDebtSubscription.Status.ACTIVE, amount_paid=package.price,
                payment_reference=reference_note,
            )
        if locked.receipt_sms_package_id:
            package = locked.receipt_sms_package
            SMSReceiptSubscription.objects.create(
                owner=owner, package=package, sms_credits=package.sms_credits, sms_used=0,
                start_date=today, end_date=NO_EXPIRY_DATE,
                status=SMSReceiptSubscription.Status.ACTIVE, amount_paid=package.price,
                payment_reference=reference_note,
            )

        locked.status = SignupRequest.Status.ACTIVE
        locked.confirmed_at = timezone.now()
        locked.confirmed_by = confirmed_by
        locked.created_pharmacy = pharmacy
        locked.created_owner = owner
        if reference_note:
            locked.notes = (locked.notes + "\n" if locked.notes else "") + reference_note
        locked.save()

        sms_phone = locked.phone_number
        receipt_token = payment_receipt.access_token
        receipt_pk = payment_receipt.pk

    def _after_commit():
        try:
            notify_many(
                [owner],
                'Account verified and activated',
                f"Your SmartPOS account for '{pharmacy_name}' has been approved and activated. "
                'Your login details were sent by SMS. Please change your temporary password after login.',
                category=Notification.Category.SYSTEM,
                level=Notification.Level.SUCCESS,
                link_url=reverse('dashboard:home'),
                pharmacy=pharmacy,
            )
        except Exception:
            logger.exception("Could not create the activation notification for signup #%s", signup.pk)

        receipt = SubscriptionPaymentReceipt.objects.filter(pk=receipt_pk).first()
        sms_sent = False
        try:
            receipt_url_path = reverse('hq:public_subscription_receipt', kwargs={'token': receipt_token})
            from payments.models import PesapalSettings
            site_base = PesapalSettings.load().site_base_url
            receipt_url = f"{site_base.rstrip('/')}{receipt_url_path}" if site_base else receipt_url_path
            activation_sms = (
                "SmartPOS - Payment received. Your account is ACTIVE.\n"
                f"Username: {username}\n"
                f"Password: {temp_password}\n"
                f"Valid until: {end_date:%d %b %Y}\n"
                f"Receipt: {receipt_url}"
            )
            sms_sent = _send_subscription_payment_sms(sms_phone, activation_sms)
        except Exception:
            logger.exception("Activation SMS failed for signup #%s", signup.pk)
        if receipt is not None:
            receipt.sms_attempts += 1
            receipt.sms_status = (SubscriptionPaymentReceipt.SMSStatus.SENT if sms_sent else SubscriptionPaymentReceipt.SMSStatus.FAILED)
            receipt.sms_sent_at = timezone.now() if sms_sent else None
            receipt.sms_last_error = '' if sms_sent else 'Initial payment receipt SMS could not be delivered.'
            receipt.save(update_fields=['sms_attempts', 'sms_status', 'sms_sent_at', 'sms_last_error'])

    transaction.on_commit(_after_commit)
    signup.refresh_from_db()
    return subscription



def _link_google_identity(signup, owner):
    """Registered with 'Continue with Google': link the verified Google id so the owner signs in with one tap."""
    sub = (getattr(signup, 'google_sub', '') or '').strip()
    if not sub or User.objects.filter(google_sub=sub).exclude(pk=owner.pk).exists():
        return
    owner.google_sub = sub
    owner.must_change_password = False   # they sign in with Google; no SMS password to replace
    if not owner.email and signup.email:
        owner.email = signup.email
    owner.save(update_fields=['google_sub', 'email', 'must_change_password'])


TRIAL_PACKAGE_CODE = 'TRIAL10'


def _trial_package(model):
    """Hidden, free 'Trial' package row for one SMS package type.

    SMS subscriptions need a package (FK). The trial row is inactive so it never appears in the
    Packages Center or on the registration page, and it is created on first use (no data migration).
    """
    package, _ = model.objects.get_or_create(
        code=TRIAL_PACKAGE_CODE,
        defaults={
            'name': f'Free Trial ({TRIAL_SMS_CREDITS} SMS)', 'price': 0, 'sms_credits': TRIAL_SMS_CREDITS,
            'duration_days': TRIAL_DAYS, 'is_active': False, 'sort_order': 0,
        },
    )
    return package


def activate_trial_signup(signup: SignupRequest):
    """Open a TRIAL_DAYS (30-day) free trial straight away: same Pharmacy + Shop Owner + Subscription
    set-up as a paid activation with full Starter features and no sales cap, no payment taken, TRIAL_SMS_CREDITS SMS in each SMS package (Debt, Receipt, Marketing) and 10 sample
    products matching the chosen business type so the customer can sell immediately.

    * One transaction: nothing is left half-created if a step fails.
    * Row-locked, so a double submit can never create two trial accounts.
    * Login details are SMSed after commit (best-effort, like paid activation).
    """
    with transaction.atomic():
        locked = SignupRequest.objects.select_for_update().get(pk=signup.pk)
        if locked.status != SignupRequest.Status.PENDING_PAYMENT or not locked.is_trial:
            signup.refresh_from_db()
            return getattr(signup, 'subscription', None)

        phone_number = normalize_phone_number(locked.phone_number)
        username = _unique_username(f"{locked.owner_first_name}{locked.owner_last_name}")
        pharmacy_name = _unique_pharmacy_name(locked.business_name, phone_number)
        temp_password = _generate_temp_password()

        pharmacy = Pharmacy.objects.create(
            name=pharmacy_name, location=locked.location, phone_number=phone_number,
            business_type=locked.business_type,
            registration_number=locked.pharmacy_council_fin,
            pharmacy_council_fin=locked.pharmacy_council_fin,
            permit_number=locked.permit_number,
            permit_expiry_date=locked.permit_expiry_date,
        )
        owner = User.objects.create_user(
            username=username, password=temp_password,
            first_name=locked.owner_first_name, last_name=locked.owner_last_name,
            phone_number=phone_number, role=User.Role.ADMIN,
            is_staff=False, is_superuser=False, must_change_password=True,
        )
        owner.managed_pharmacies.add(pharmacy)
        _link_google_identity(locked, owner)

        today = timezone.localdate()
        end_date = today + timedelta(days=TRIAL_DAYS)   # one free month; locks afterwards until the owner pays
        # Free Plan tier is fixed here, never taken from the form.
        subscription = Subscription.objects.create(
            owner=owner, signup_request=locked, plan=SignupRequest.Plan.MONTHLY, tier=SignupRequest.Tier.STARTER,
            status=Subscription.Status.ACTIVE, start_date=today, end_date=end_date, amount_paid=0,
            payment_reference='FREE TRIAL', is_trial=True,
        )
        SMSWallet.objects.get_or_create(owner=owner, defaults={'credits_granted': 500, 'credits_used': 0})
        CustomerSMSCredentials.objects.get_or_create(owner=owner)
        for sub_model, pkg_model in (
            (SMSDebtSubscription, SMSDebtPackage),
            (SMSReceiptSubscription, SMSReceiptPackage),
            (SMSMarketingSubscription, SMSMarketingPackage),
        ):
            sub_model.objects.create(
                owner=owner, package=_trial_package(pkg_model), sms_credits=TRIAL_SMS_CREDITS, sms_used=0,
                start_date=today, end_date=NO_EXPIRY_DATE, status=sub_model.Status.ACTIVE,
                amount_paid=0, payment_reference='FREE TRIAL',
            )
        # Sample products for the chosen business type: the customer never opens an empty system.
        try:
            from inventory.demo_data import load_demo_products
            # Listing accounts (online seller / property) have no POS stock: no POS sample products.
            if locked.business_type not in ('ONLINE_SELLER', 'REAL_ESTATE'):
                with transaction.atomic():   # savepoint: a sample-data problem must never block registration
                    load_demo_products(pharmacy, locked.business_type, user=owner)
        except Exception:
            logger.exception("Sample data could not be loaded for signup #%s", signup.pk)

        locked.status = SignupRequest.Status.ACTIVE
        locked.confirmed_at = timezone.now()
        locked.created_pharmacy = pharmacy
        locked.created_owner = owner
        locked.notes = (locked.notes + "\n" if locked.notes else "") + f"[FREE TRIAL {TRIAL_DAYS}d] started {today:%Y-%m-%d}"
        locked.save()
        sms_phone = locked.phone_number
        trial_end = end_date

    def _after_commit():
        try:
            notify_many(
                [owner], 'Welcome to SmartPOS',
                f"'{pharmacy_name}' has a free {TRIAL_DAYS}-day trial until {trial_end:%d %b %Y}, with {TRIAL_SMS_CREDITS} free SMS in each SMS package. "
                '10 sample products are ready so you can sell right now. Pay any time from Subscription & Packages to keep going.',
                category=Notification.Category.SYSTEM, level=Notification.Level.SUCCESS,
                link_url=reverse('dashboard:home'), pharmacy=pharmacy,
            )
        except Exception:
            logger.exception("Could not create the trial notification for signup #%s", signup.pk)
        try:
            _send_subscription_payment_sms(
                sms_phone,
                f"SmartPOS - Your {TRIAL_DAYS}-day free trial is ACTIVE until {trial_end:%d %b %Y}.\n"
                f"Username: {username}\n"
                f"Password: {temp_password}\n"
                "10 sample products are ready. Try your first sale now.",
            )
        except Exception:
            logger.exception("Trial activation SMS failed for signup #%s", signup.pk)

    transaction.on_commit(_after_commit)
    signup.refresh_from_db()
    return subscription


def renew_subscription_from_payment(subscription: Subscription, *, plan=None, tier=None, reference_note='', amount_paid=None):
    """Extends an existing subscription. Reused by admin renewal and Pesapal-paid renewal.

    * Renewing before expiry stacks the new period after the current end date (no days lost);
      renewing after expiry starts from today (a full period, same as a brand-new signup).
    * `amount_paid` is what was actually received (online); otherwise the current price list.
    * Row-locked, so concurrent confirmations cannot extend twice.
    """
    plan = plan or subscription.plan
    if plan not in (Subscription.Plan.MONTHLY, Subscription.Plan.YEARLY):
        plan = subscription.plan

    tier = tier or getattr(subscription, 'tier', SignupRequest.Tier.STARTER)
    if tier not in SignupRequest.Tier.values:
        tier = getattr(subscription, 'tier', SignupRequest.Tier.STARTER)

    from . import referrals
    full_price = PaymentSettings.load().price_for(plan, tier)
    if amount_paid is not None:
        amount = amount_paid
    else:
        # Manual confirmation: the owner's referral discount (if any) is applied automatically.
        amount, _ = referrals.discounted_price(subscription.owner, full_price)
    discount_given = referrals.percent_given(full_price, amount)
    duration = timedelta(days=365) if plan == Subscription.Plan.YEARLY else timedelta(days=30)

    with transaction.atomic():
        locked = Subscription.objects.select_for_update().get(pk=subscription.pk)
        today = timezone.localdate()
        # A trial is an upgrade, not a renewal: the paid period starts today (remaining free
        # days are not stacked on top) and the account stops being a trial.
        was_trial = bool(locked.is_trial)
        base_date = today if was_trial else max(locked.end_date, today)

        locked.plan = plan
        locked.tier = tier
        locked.status = Subscription.Status.ACTIVE
        locked.start_date = today
        locked.end_date = base_date + duration
        locked.amount_paid = (locked.amount_paid or 0) + amount
        locked.is_trial = False
        if reference_note:
            locked.payment_reference = reference_note
        locked.save(update_fields=['plan', 'tier', 'status', 'start_date', 'end_date', 'amount_paid', 'payment_reference', 'is_trial'])
        referrals.consume(locked.owner, discount_given)  # mark the used rewards, same transaction
        if was_trial and locked.signup_request_id and amount and amount > 0:
            _trial_signup, _trial_amount = locked.signup_request, amount
            transaction.on_commit(lambda: _qualify_referral(_trial_signup, _trial_amount))

    subscription.refresh_from_db()
    owner = subscription.owner
    end_date = subscription.end_date
    tier_label = subscription.get_tier_display()
    upgraded_from_trial = was_trial

    def _after_commit():
        try:
            notify_many(
                [owner], 'Account upgraded' if upgraded_from_trial else 'Subscription renewed',
                (f"Your SmartPOS account is upgraded to {tier_label} until {end_date:%d %b %Y}. Everything you recorded during the trial is kept."
                 if upgraded_from_trial else f"Your SmartPOS subscription has been renewed until {end_date:%d %b %Y}."),
                category=Notification.Category.SYSTEM, level=Notification.Level.SUCCESS,
                link_url=reverse('dashboard:home'),
            )
        except Exception:
            logger.exception("Could not create the renewal notification for subscription #%s", subscription.pk)
        try:
            send_sms(
                owner.phone_number,
                f"SmartPOS - Payment received.\n{tier_label} plan renewed until {end_date:%d %b %Y}.\nYou can log in now.",
                category=SMSLog.Category.PAYMENT_RECEIPT, owner=None, consume_wallet=False,
            )
        except Exception:
            logger.warning("Renewal SMS failed for subscription #%s", subscription.pk, exc_info=True)

    transaction.on_commit(_after_commit)
    return subscription
