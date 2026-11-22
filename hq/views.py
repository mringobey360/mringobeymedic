import os
import shutil
import subprocess
import sys
import tempfile
import random
import string
from datetime import timedelta
from functools import wraps

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum, Count, Prefetch
from django.http import Http404, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import User
from accounts.phone_utils import normalize_phone_number
from pharmacies.models import BranchAddOn, Pharmacy
from sms_integration.models import SMSAnnouncement, SMSLog, SMSWallet, CustomerSMSCredentials, SMSDebtPackage, SMSDebtSubscription, SMSDebtPurchase, SMSReceiptPackage, SMSReceiptSubscription, SMSReceiptPurchase, SMSMarketingPackage, SMSMarketingSubscription, SMSMarketingPurchase
from daily_reports.models import DailyReportPackage, DailyReportSubscription, DailyReportPurchase
from sms_integration.utils import send_sms

from .forms import SignupForm, PaymentSettingsForm, SMSDebtPackageForm, SMSReceiptPackageForm, SMSMarketingPackageForm
from sms_integration.services import visible_purchases
from .models import SignupRequest, Subscription, SubscriptionPaymentReceipt, PaymentSettings
from notifications.utils import notify_many
from notifications.models import Notification


def superadmin_required(view_func):
    """
    Only a true platform superadmin (Django is_superuser=True) may access
    this view — deliberately a DIFFERENT flag from the pharmacy Admin
    ('Shop Owner/Supervisor') role, which never carries is_superuser.
    Create this account with: python manage.py createsuperuser
    """
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied("This page is available to platform superadmins only.")
        return view_func(request, *args, **kwargs)
    return _wrapped


def _generate_temp_password(length=12):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))


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


# ============================================================== Customer feedback ===

def _safe_customer_sms(signup, message):
    """Send customer status feedback without ever breaking the admin action."""
    try:
        send_sms(
            normalize_phone_number(signup.phone_number),
            message,
            category=SMSLog.Category.OTHER,
        )
        return True
    except Exception:
        # The database/status change is the source of truth. SMS delivery is best-effort.
        return False


def _send_subscription_payment_sms(phone_number, message):
    """Platform-paid transactional SMS for subscription/payment receipts."""
    try:
        return send_sms(
            normalize_phone_number(phone_number), message,
            category=SMSLog.Category.PAYMENT_RECEIPT, owner=None, consume_wallet=False,
        )
    except Exception:
        return False


# ============================================================== Public ===

def _start_free_trial(request, signup, payment_settings):
    """Free-trial registration: no payment step. The account is opened immediately."""
    from .services import activate_trial_signup
    from .models import TRIAL_DAYS
    try:
        activate_trial_signup(signup)
    except Exception:
        # Never leave a half-registered trial behind: drop the pending row so the visitor can retry.
        import logging
        logging.getLogger('hq.views').exception("Trial activation failed for signup #%s", signup.pk)
        SignupRequest.objects.filter(pk=signup.pk, status=SignupRequest.Status.PENDING_PAYMENT).delete()
        messages.error(request, 'We could not start your free account just now. Please try again in a moment.')
        return redirect('hq:signup')

    superadmins = User.objects.filter(is_superuser=True, is_active=True)
    try:
        notify_many(
            superadmins, "New Free Plan signup",
            f"{signup.owner_full_name} ({signup.phone_number}) joined the Free Plan with "
            f"{signup.business_name} ({signup.get_business_type_display()}).",
            category=Notification.Category.SYSTEM, level=Notification.Level.INFO,
            link_url=reverse('hq:signup_detail', kwargs={'pk': signup.pk}),
        )
    except Exception:
        pass
    try:
        send_sms(
            payment_settings.owner_notification_phone,
            f"New FREE PLAN: {signup.business_name} ({signup.owner_full_name}, {signup.phone_number}).",
            category=SMSLog.Category.OTHER,
        )
    except Exception:
        pass
    if signup.google_sub and signup.created_owner_id:
        # Registered with Google: open the workspace straight away, no password to type.
        from accounts.google_views import start_session
        from accounts import google_auth
        pending = request.session.pop('google_signup', None) or {}
        google_auth.sync_profile_photo(signup.created_owner, {'picture': pending.get('picture', '')})
        return start_session(request, signup.created_owner, lambda: redirect('dashboard:home'))
    return redirect('hq:signup_status', public_id=signup.public_id)


def signup_view(request):
    """Public signup form — no login required. Anyone can reach this page."""
    from accounts import google_auth
    from accounts.google_views import pending_google_signup
    payment_settings = PaymentSettings.load()
    google_identity = pending_google_signup(request)
    initial = {'referral_code': (request.GET.get('ref') or '')[:12].upper()}
    if google_identity:
        initial.update({'owner_first_name': google_identity['first_name'], 'owner_last_name': google_identity['last_name'],
                        'email': google_identity['email']})
    form = SignupForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        signup = form.save()
        if google_identity:
            # Email comes from Google (verified), never from the typed field.
            signup.google_sub, signup.email = google_identity['sub'], google_identity['email']
            signup.save(update_fields=['google_sub', 'email'])

        if signup.is_trial:
            return _start_free_trial(request, signup, payment_settings)

        # Notify the platform owner immediately, before the customer even
        # sees the payment page. Use BOTH the in-app notification bell and
        # the configured SMS number. SMS is best-effort: a temporary SMS
        # provider failure must never make a successful registration fail.
        notification_message = (
            f"{signup.owner_full_name} ({signup.phone_number}) registered "
            f"{signup.business_name} ({signup.get_business_type_display()}) for the {signup.get_tier_display()} / {signup.get_plan_display()} plan "
            f"and is awaiting payment instructions."
        )
        superadmins = User.objects.filter(is_superuser=True, is_active=True)
        notify_many(
            superadmins,
            "New customer registration",
            notification_message,
            category=Notification.Category.SYSTEM,
            level=Notification.Level.INFO,
            link_url=reverse('hq:signup_detail', kwargs={'pk': signup.pk}),
        )

        try:
            send_sms(
                payment_settings.owner_notification_phone,
                f"New customer: {signup.business_name} ({signup.owner_full_name}, {signup.phone_number}) "
                f"has registered for the {signup.get_tier_display()} / {signup.get_plan_display()} plan and is awaiting payment instructions.",
                category=SMSLog.Category.OTHER,
            )
        except Exception:
            # Registration itself has already succeeded. Do not expose SMS
            # provider errors to the public signup page.
            pass

        # Give the customer an immediate confirmation and a private link straight
        # back to their payment page, so they can pay even if they close the tab.
        pay_url = request.build_absolute_uri(
            reverse('hq:payment_instructions', kwargs={'public_id': signup.public_id})
        )
        total_amount = _signup_total(signup, payment_settings)
        # One concise ASCII/GSM-friendly message: registration confirmation +
        # exact payment amount + private payment link.
        registration_sms = (
            "SmartPOS - Registration received.\n"
            f"Plan: {signup.get_tier_display()} / {signup.get_plan_display()}\n"
            f"Amount: TZS {total_amount:,.0f}\n"
            f"Pay here: {pay_url}"
        )
        _safe_customer_sms(signup, registration_sms)

        return redirect('hq:payment_instructions', public_id=signup.public_id)

    tier_defs = [
        (SignupRequest.Tier.STARTER, 'Starter', '1 branch • 3 users • Core POS, stock, sales, debts and reports', True),
        (SignupRequest.Tier.BUSINESS, 'Business', '2 branches • 10 users • Advanced reports and branch management', False),
        (SignupRequest.Tier.PROFESSIONAL, 'Professional', '5 branches • 25 users • Advanced multi-branch controls and analytics', False),
        (SignupRequest.Tier.ENTERPRISE, 'Enterprise', 'Large teams and multi-branch operations • Custom setup available', False),
    ]
    tier_cards = []
    for value, label, features, popular in tier_defs:
        monthly = payment_settings.price_for(SignupRequest.Plan.MONTHLY, value)
        yearly = payment_settings.price_for(SignupRequest.Plan.YEARLY, value)
        savings = (monthly * 12) - yearly
        tier_cards.append({
            'value': value, 'label': label, 'features': features,
            'monthly': monthly, 'yearly': yearly, 'savings': savings,
            'popular': popular,
        })
    from .models import FREE_PLAN_MAX_USERS, FREE_PLAN_MONTHLY_SALES, TRIAL_DAYS, TRIAL_SMS_CREDITS
    from .free_plan import starter_price as _free_starter_price
    return render(request, 'hq/signup.html', {
        'form': form, 'tier_cards': tier_cards,
        'trial_days': TRIAL_DAYS, 'trial_sms': TRIAL_SMS_CREDITS,
        'free_users': FREE_PLAN_MAX_USERS, 'free_sales': FREE_PLAN_MONTHLY_SALES,
        'starter_price': f'{_free_starter_price():,}',
        'trial_selected': form.is_trial or (not form.is_bound and request.GET.get('plan') != 'paid'),
        'google_identity': google_identity,
        **google_auth.template_context(request),
    })


@require_http_methods(["GET"])
def signup_phone_check_api(request):
    raw = request.GET.get('phone', '')
    try:
        phone = normalize_phone_number(raw)
    except Exception as exc:
        return JsonResponse({'valid': False, 'exists': False, 'normalized': '', 'message': str(exc)}, status=200)
    user_exists = User.objects.filter(phone_number=phone, is_active=True).exists()
    signup_exists = SignupRequest.objects.filter(phone_number=phone).exclude(status=SignupRequest.Status.REJECTED).exists()
    return JsonResponse({
        'valid': True, 'exists': user_exists or signup_exists, 'normalized': phone,
        'message': ('This phone number is already registered or has a pending registration.' if (user_exists or signup_exists) else 'Phone number is available.')
    })


def _signup_total(signup, payment_settings):
    """Core subscription price. Legacy signups that still carry SMS add-ons keep paying for them."""
    total = payment_settings.price_for(signup.plan, signup.tier)
    if signup.sms_package_id:
        total += signup.sms_package.price
    if signup.receipt_sms_package_id:
        total += signup.receipt_sms_package.price
    return total


def payment_instructions_view(request, public_id):
    """
    Public — the payment page every new customer lands on right after
    registering. Reachable only by the exact UUID link (not enumerable), which
    is also sent by SMS so the customer can always come back and pay.
    """
    signup = get_object_or_404(SignupRequest, public_id=public_id)
    payment_settings = PaymentSettings.load()
    base_amount = payment_settings.price_for(signup.plan, signup.tier)
    sms_amount = signup.sms_package.price if signup.sms_package_id else 0
    receipt_sms_amount = signup.receipt_sms_package.price if signup.receipt_sms_package_id else 0
    amount = base_amount + sms_amount + receipt_sms_amount
    # Online checkout is offered whenever ANY gateway (Pesapal or ABLiner) is usable.
    from payments.services import pesapal_usable
    from payments import abliner_client
    online_ready = bool(pesapal_usable() or abliner_client.is_usable())
    return render(request, 'hq/payment_instructions.html', {
        'signup': signup, 'payment_settings': payment_settings, 'amount': amount, 'base_amount': base_amount,
        'sms_amount': sms_amount, 'receipt_sms_amount': receipt_sms_amount,
        'online_ready': online_ready,
        'payment_url': request.build_absolute_uri(request.path),
    })


def signup_status_view(request, public_id):
    """Public status page for a customer using their unguessable UUID link."""
    signup = get_object_or_404(SignupRequest, public_id=public_id)
    payment_settings = PaymentSettings.load()
    trial_subscription = None
    if signup.is_trial and signup.created_owner_id:
        trial_subscription = Subscription.objects.filter(owner_id=signup.created_owner_id).first()
    return render(request, 'hq/signup_status.html', {
        'signup': signup, 'payment_settings': payment_settings, 'trial_subscription': trial_subscription,
    })


# =========================================================== Superadmin ==

@superadmin_required
def dashboard_view(request):
    """Platform owner overview: what needs action, key numbers, and quick navigation."""
    today = timezone.localdate()
    signups = SignupRequest.objects.all()
    customers_qs = User.objects.filter(role=User.Role.ADMIN, is_superuser=False)
    total_collected = Subscription.objects.aggregate(total=Sum('amount_paid'))['total'] or 0
    active_subs = Subscription.objects.filter(status=Subscription.Status.ACTIVE, end_date__gte=today)

    stats = {
        'pending_count': signups.filter(status=SignupRequest.Status.PENDING_PAYMENT).count(),
        'active_subscriptions': active_subs.count(),
        'total_collected': total_collected,
        'expiring_soon': active_subs.filter(end_date__lte=today + timedelta(days=7)).count(),
        'expired_count': Subscription.objects.filter(Q(status=Subscription.Status.EXPIRED) | Q(end_date__lt=today)).count(),
        'customer_count': customers_qs.count(),
        'branch_count': Pharmacy.objects.count(),
        'active_branch_count': Pharmacy.objects.filter(is_active=True).count(),
        'pending_branch_requests': BranchAddOn.objects.filter(status=BranchAddOn.Status.PENDING_PAYMENT).count(),
        'pending_debt_sms': visible_purchases(SMSDebtPurchase.objects.filter(status=SMSDebtPurchase.Status.PENDING)).count(),
        'pending_receipt_sms': visible_purchases(SMSReceiptPurchase.objects.filter(status=SMSReceiptPurchase.Status.PENDING)).count(),
    }
    attention = [
        {'label': 'Registration payments to verify', 'count': stats['pending_count'],
         'url': reverse('hq:signup_list') + '?status=PENDING_PAYMENT', 'tone': 'amber'},
        {'label': 'Branch add-on requests', 'count': stats['pending_branch_requests'],
         'url': reverse('hq:branch_addon_list'), 'tone': 'amber'},
        {'label': 'Debt SMS payments to approve', 'count': stats['pending_debt_sms'],
         'url': reverse('hq:debt_sms_purchases'), 'tone': 'amber'},
        {'label': 'Receipt SMS payments to approve', 'count': stats['pending_receipt_sms'],
         'url': reverse('hq:receipt_sms_purchases'), 'tone': 'amber'},
        {'label': 'Subscriptions expiring in 7 days', 'count': stats['expiring_soon'],
         'url': reverse('hq:subscription_list'), 'tone': 'red'},
    ]
    attention = [item for item in attention if item['count']]

    expiring_subscriptions = list(
        active_subs.filter(end_date__lte=today + timedelta(days=14)).select_related('owner').order_by('end_date')[:6]
    )
    customers = (
        customers_qs
        .prefetch_related('managed_pharmacies__addon')
        .annotate(
            branch_count=Count('managed_pharmacies', distinct=True),
            active_branch_count=Count('managed_pharmacies', filter=Q(managed_pharmacies__is_active=True), distinct=True),
        )
        .order_by('-created_at')[:8]
    )
    return render(request, 'hq/dashboard.html', {
        'stats': stats, 'attention': attention,
        'recent_signups': signups[:8], 'customers': customers,
        'expiring_subscriptions': expiring_subscriptions,
        'recent_announcements': SMSAnnouncement.objects.all()[:4],
    })


@superadmin_required
def signup_list_view(request):
    signups = SignupRequest.objects.all()
    status_filter = request.GET.get('status', '')
    q = request.GET.get('q', '').strip()
    if status_filter:
        signups = signups.filter(status=status_filter)
    if q:
        signups = signups.filter(
            Q(business_name__icontains=q) | Q(owner_first_name__icontains=q) |
            Q(owner_last_name__icontains=q) | Q(phone_number__icontains=q)
        )
    paginator = Paginator(signups, 20)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'hq/signup_list.html', {
        'page_obj': page, 'status_choices': SignupRequest.Status.choices,
        'current_status': status_filter, 'q': q,
    })


@superadmin_required
def customer_list_view(request):
    """Platform owner portfolio: one row per customer owner, with all branches."""
    customers = (
        User.objects.filter(role=User.Role.ADMIN, is_superuser=False)
        .prefetch_related('managed_pharmacies__addon')
        .annotate(
            branch_count=Count('managed_pharmacies', distinct=True),
            active_branch_count=Count(
                'managed_pharmacies',
                filter=Q(managed_pharmacies__is_active=True),
                distinct=True,
            ),
        )
        .order_by('first_name', 'last_name', 'username')
    )
    q = request.GET.get('q', '').strip()
    if q:
        customers = customers.filter(
            Q(username__icontains=q) | Q(first_name__icontains=q) |
            Q(last_name__icontains=q) | Q(phone_number__icontains=q) |
            Q(managed_pharmacies__name__icontains=q)
        ).distinct()
    paginator = Paginator(customers, 20)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'hq/customer_list.html', {'page_obj': page, 'q': q})


@superadmin_required
def customer_detail_view(request, pk):
    """Detailed customer portfolio showing every branch and its billing/access state."""
    owner = get_object_or_404(
        User.objects.prefetch_related('managed_pharmacies__addon', 'managed_pharmacies__employees'),
        pk=pk, role=User.Role.ADMIN, is_superuser=False,
    )
    branches = list(owner.managed_pharmacies.all())
    active_branches = sum(1 for b in branches if b.is_active)
    pending_branches = sum(1 for b in branches if hasattr(b, 'addon') and b.addon.status == BranchAddOn.Status.PENDING_PAYMENT)
    return render(request, 'hq/customer_detail.html', {
        'owner': owner, 'branches': branches,
        'branch_count': len(branches), 'active_branch_count': active_branches,
        'pending_branch_count': pending_branches,
        'subscription': getattr(owner, 'subscription', None),
        'sms_wallet': getattr(owner, 'sms_wallet', None),
    })


@superadmin_required
def signup_detail_view(request, pk):
    signup = get_object_or_404(SignupRequest, pk=pk)
    return render(request, 'hq/signup_detail.html', {'signup': signup})


@superadmin_required
@require_http_methods(["POST"])
def activate_subscription_view(request, pk):
    """
    Confirms a manually-verified mobile-money payment for a MONTHLY or
    YEARLY signup. The actual account creation is shared with the
    automatic Pesapal online-payment path — see hq.services.activate_signup_from_payment
    — so the outcome (Pharmacy, Shop Owner account, Subscription, SMS) is
    identical no matter which payment method the customer used.
    """
    signup = get_object_or_404(SignupRequest, pk=pk)
    if signup.status != SignupRequest.Status.PENDING_PAYMENT:
        messages.warning(request, "This request has already been processed.")
        return redirect('hq:signup_detail', pk=pk)
    reference_note = request.POST.get('reference_note', '').strip()

    from .services import activate_signup_from_payment
    activate_signup_from_payment(signup, reference_note=reference_note, confirmed_by=request.user)

    pharmacy_name = signup.created_pharmacy.name if signup.created_pharmacy_id else signup.business_name
    messages.success(request, f"Account for '{pharmacy_name}' is active. Login details and payment receipt link were sent by SMS.")
    return redirect('hq:signup_detail', pk=pk)


def _to_packages_center(request, service=None):
    """Every subscription/SMS package surface now lives in ONE place: Packages & Services."""
    url = reverse('hq:packages_center')
    if service:
        url += '?service=' + service
    return redirect(url)


@login_required
def my_subscription_view(request):
    return _to_packages_center(request)

@login_required
@require_http_methods(['POST'])
def resend_subscription_receipt_sms_view(request):
    if not request.user.is_management_user() or request.user.is_superuser:
        raise PermissionDenied('Only the business owner can resend this receipt SMS.')
    subscription = get_object_or_404(Subscription.objects.select_related('owner'), owner=request.user.business_owner())
    receipt = getattr(subscription, 'payment_receipt', None)
    if receipt is None:
        messages.error(request, 'No payment receipt is available for this subscription.')
        return redirect('accounts:my_subscription')
    if receipt.sms_status == SubscriptionPaymentReceipt.SMSStatus.SENT:
        messages.info(request, 'The payment receipt SMS has already been delivered.')
        return redirect('accounts:my_subscription')
    if not request.user.phone_number:
        messages.error(request, 'No phone number is saved on your account.')
        return redirect('accounts:my_subscription')
    receipt_url = request.build_absolute_uri(reverse('hq:public_subscription_receipt', kwargs={'token': receipt.access_token}))
    pharmacy = request.user.accessible_management_pharmacies().filter(is_active=True).order_by('id').first()
    pharmacy_name = pharmacy.name if pharmacy else 'SmartPOS'
    import unicodedata
    pharmacy_name = unicodedata.normalize('NFKD', pharmacy_name).encode('ascii', 'ignore').decode('ascii')
    pharmacy_name = ' '.join(pharmacy_name.split())[:24]
    message = (f"SmartPOS - Payment receipt\n{pharmacy_name}\n"
               f"Amount: TZS {subscription.amount_paid:,.0f}\n"
               f"Valid until: {subscription.end_date:%d %b %Y}\n"
               f"Receipt: {receipt_url}")
    sent = _send_subscription_payment_sms(request.user.phone_number, message)
    receipt.sms_attempts += 1
    receipt.sms_status = SubscriptionPaymentReceipt.SMSStatus.SENT if sent else SubscriptionPaymentReceipt.SMSStatus.FAILED
    if sent:
        receipt.sms_sent_at = timezone.now()
        receipt.sms_last_error = ''
    else:
        receipt.sms_last_error = 'Receipt SMS resend failed. Please check SMS configuration/network.'
    receipt.save(update_fields=['sms_attempts', 'sms_status', 'sms_sent_at', 'sms_last_error'])
    if sent:
        messages.success(request, 'Payment receipt SMS resent successfully.')
    else:
        messages.error(request, 'Payment receipt SMS could not be sent. Please try again later.')
    return redirect('accounts:my_subscription')


def public_subscription_receipt_view(request, token):
    receipt = get_object_or_404(SubscriptionPaymentReceipt.objects.select_related('subscription__owner', 'subscription__signup_request'), access_token=token)
    return render(request, 'hq/public_subscription_receipt.html', {'receipt': receipt, 'subscription': receipt.subscription})


@superadmin_required
@require_http_methods(["POST"])
def reject_signup_view(request, pk):
    signup = get_object_or_404(SignupRequest, pk=pk)
    signup.status = SignupRequest.Status.REJECTED
    reason = request.POST.get('reason', '').strip()
    if reason:
        signup.notes = (signup.notes + "\n" if signup.notes else "") + reason
    signup.confirmed_by = request.user
    signup.confirmed_at = timezone.now()
    signup.save()
    reason_text = f" Reason: {reason}" if reason else ""
    _safe_customer_sms(
        signup,
        f"Hello {signup.owner_first_name}, your SmartPOS registration for {signup.business_name} was not approved at this time.{reason_text} "
        f"For assistance, contact {PaymentSettings.load().support_phone}. "
        f"Track status: {request.build_absolute_uri(reverse('hq:signup_status', kwargs={'public_id': signup.public_id}))}",
    )
    messages.info(request, "Request rejected.")
    return redirect('hq:signup_detail', pk=pk)


@superadmin_required
def branch_addon_list_view(request):
    """Pending/all additional-branch payment requests, for manual mobile-money confirmation."""
    addons = BranchAddOn.objects.select_related('pharmacy', 'owner').all()
    status_filter = request.GET.get('status', BranchAddOn.Status.PENDING_PAYMENT)
    if status_filter:
        addons = addons.filter(status=status_filter)
    paginator = Paginator(addons, 20)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'hq/branch_addon_list.html', {
        'page_obj': page, 'status_choices': BranchAddOn.Status.choices, 'current_status': status_filter,
    })


@superadmin_required
@require_http_methods(["POST"])
def confirm_branch_addon_view(request, pk):
    """Confirms off-system mobile-money payment for an additional branch and activates it."""
    addon = get_object_or_404(BranchAddOn, pk=pk)
    if addon.status != BranchAddOn.Status.PENDING_PAYMENT:
        messages.warning(request, "This branch add-on has already been processed.")
        return redirect('hq:branch_addon_list')

    addon.activate(confirmed_by=request.user)
    notify_many(
        [addon.owner],
        'Branch activated',
        f"Your branch '{addon.pharmacy.name}' has been approved and activated. It is now ready to use.",
        category=Notification.Category.SYSTEM,
        level=Notification.Level.SUCCESS,
        link_url=reverse('pharmacies:list'),
    )
    try:
        send_sms(
            addon.owner.phone_number,
            f"SmartPOS - Branch approved\n'{addon.pharmacy.name}' is now active.\nOpen it from My Pharmacy Branches.",
            category=SMSLog.Category.OTHER,
            owner=addon.owner,
        )
    except Exception:
        pass
    messages.success(request, f"Branch '{addon.pharmacy.name}' is now active for {addon.owner.get_full_name() or addon.owner.username}.")
    return redirect('hq:branch_addon_list')


@superadmin_required
@require_http_methods(["POST"])
def reject_branch_addon_view(request, pk):
    """Declines an additional-branch payment request; the branch stays locked."""
    addon = get_object_or_404(BranchAddOn, pk=pk)
    if addon.status != BranchAddOn.Status.PENDING_PAYMENT:
        messages.warning(request, "This branch add-on has already been processed.")
        return redirect('hq:branch_addon_list')

    addon.status = BranchAddOn.Status.CANCELLED
    addon.cancelled_at = timezone.now()
    addon.confirmed_by = request.user
    addon.save(update_fields=['status', 'cancelled_at', 'confirmed_by'])
    notify_many(
        [addon.owner],
        'Branch request update',
        f"Your branch request for '{addon.pharmacy.name}' was not approved. Please contact support for details.",
        category=Notification.Category.SYSTEM,
        level=Notification.Level.WARNING,
        link_url=reverse('pharmacies:list'),
    )
    try:
        send_sms(
            addon.owner.phone_number,
            f"SmartPOS - Branch request\n'{addon.pharmacy.name}' was not approved.\nPlease contact support for details.",
            category=SMSLog.Category.OTHER,
            owner=addon.owner,
        )
    except Exception:
        pass
    messages.info(request, f"Branch add-on for '{addon.pharmacy.name}' was rejected.")
    return redirect('hq:branch_addon_list')


@superadmin_required
@require_http_methods(["POST"])
def renew_subscription_view(request, pk):
    """Renew an existing monthly/yearly customer without creating a new account.

    Shares the actual extension logic with the automatic Pesapal-paid
    renewal path — see hq.services.renew_subscription_from_payment.
    """
    subscription = get_object_or_404(Subscription.objects.select_related('owner'), pk=pk)
    plan = request.POST.get('plan', subscription.plan)
    tier = request.POST.get('tier', getattr(subscription, 'tier', SignupRequest.Tier.STARTER))
    if plan not in (Subscription.Plan.MONTHLY, Subscription.Plan.YEARLY) or tier not in SignupRequest.Tier.values:
        messages.error(request, "Invalid renewal plan selected.")
        return redirect('hq:subscription_list')

    from .services import renew_subscription_from_payment
    renew_subscription_from_payment(subscription, plan=plan, tier=tier, reference_note=request.POST.get('reference_note', '').strip())

    messages.success(
        request,
        f"Subscription renewed for {subscription.owner.get_full_name() or subscription.owner.username} "
        f"until {subscription.end_date.strftime('%d %b %Y')}.",
    )
    return redirect('hq:subscription_list')


@login_required
def intelligent_reports_subscription_view(request):
    return _to_packages_center(request, 'report')

@superadmin_required
def intelligent_report_purchase_list_view(request):
    purchases = visible_purchases(DailyReportPurchase.objects.select_related('owner','package').all())
    status = request.GET.get('status', '')
    if status in dict(DailyReportPurchase.Status.choices): purchases = purchases.filter(status=status)
    page_obj = Paginator(purchases, 20).get_page(request.GET.get('page'))
    return render(request, 'hq/intelligent_report_purchases.html', {'page_obj': page_obj, 'status_choices': DailyReportPurchase.Status.choices, 'current_status': status})

@superadmin_required
@require_http_methods(['POST'])
def approve_intelligent_report_purchase_view(request, pk):
    purchase = get_object_or_404(DailyReportPurchase.objects.select_related('owner','package'), pk=pk)
    if purchase.status != DailyReportPurchase.Status.PENDING:
        messages.warning(request, 'This Intelligent Report purchase has already been processed.')
        return redirect('hq:intelligent_report_purchases')
    from daily_reports.services import approve_daily_report_purchase
    approve_daily_report_purchase(purchase, reviewed_by=request.user, admin_note=request.POST.get('admin_note','').strip())
    messages.success(request, f'Intelligent Reports activated for {purchase.owner.username}: +{purchase.package.report_sms_credits:,} report SMS.')
    return redirect('hq:intelligent_report_purchases')

@superadmin_required
@require_http_methods(['POST'])
def reject_intelligent_report_purchase_view(request, pk):
    purchase = get_object_or_404(DailyReportPurchase, pk=pk)
    if purchase.status != DailyReportPurchase.Status.PENDING:
        messages.warning(request, 'This Intelligent Report purchase has already been processed.')
        return redirect('hq:intelligent_report_purchases')
    purchase.status = DailyReportPurchase.Status.REJECTED; purchase.reviewed_at = timezone.now(); purchase.reviewed_by = request.user; purchase.admin_note = request.POST.get('admin_note','').strip(); purchase.save(update_fields=['status','reviewed_at','reviewed_by','admin_note'])
    notify_many([purchase.owner], 'Intelligent Reports payment update', f'Your {purchase.package.name} payment request was rejected. {purchase.admin_note}'.strip(), category=Notification.Category.SYSTEM, level=Notification.Level.WARNING, link_url=reverse('hq:intelligent_reports_subscription'))
    messages.info(request, 'Intelligent Report payment request rejected.')
    return redirect('hq:intelligent_report_purchases')

def subscription_list_view(request):
    subscriptions = Subscription.objects.select_related('owner').all()
    page_obj = Paginator(subscriptions, 20).get_page(request.GET.get('page'))
    return render(request, 'hq/subscription_list.html', {'page_obj': page_obj})



@superadmin_required
def staff_list_view(request):
    """Platform-wide staff/customer account control for the superadmin."""
    users = User.objects.all().order_by('-date_joined')
    q = request.GET.get('q', '').strip()
    role = request.GET.get('role', '').strip()
    status = request.GET.get('status', '').strip()
    if q:
        users = users.filter(
            Q(username__icontains=q) | Q(first_name__icontains=q) |
            Q(last_name__icontains=q) | Q(phone_number__icontains=q) | Q(email__icontains=q)
        )
    if role in (User.Role.ADMIN, User.Role.EMPLOYEE):
        users = users.filter(role=role)
    if status == 'active':
        users = users.filter(is_active=True)
    elif status == 'inactive':
        users = users.filter(is_active=False)
    paginator = Paginator(users, 30)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'hq/staff_list.html', {
        'page_obj': page, 'q': q, 'role': role, 'status': status,
        'role_choices': User.Role.choices,
    })


@superadmin_required
@require_http_methods(["POST"])
def staff_toggle_active_view(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.is_superuser:
        messages.error(request, 'Platform superadmin accounts must be managed from Django System Admin.')
        return redirect('hq:staff_list')
    user.is_active = not user.is_active
    user.save(update_fields=['is_active'])
    messages.success(request, f"{user.get_full_name() or user.username} is now {'active' if user.is_active else 'inactive'}.")
    return redirect('hq:staff_list')


@superadmin_required
@require_http_methods(["POST"])
def staff_photo_update_view(request, pk):
    """System Owner can set, replace or remove any user's profile photo."""
    user = get_object_or_404(User, pk=pk)
    # Other superadmin accounts stay protected; the owner may edit their own photo.
    if user.is_superuser and user.pk != request.user.pk:
        messages.error(request, 'Platform superadmin accounts must be managed from Django System Admin.')
        return redirect('hq:staff_list')
    name = user.get_full_name() or user.username
    if request.POST.get('remove_photo') == '1':
        if user.profile_photo:
            user.profile_photo.delete(save=False)
            user.profile_photo = None
            user.save(update_fields=['profile_photo'])
        messages.success(request, f"Profile photo removed for {name}.")
        return redirect(request.META.get('HTTP_REFERER') or 'hq:staff_list')
    photo = request.FILES.get('profile_photo')
    if not photo:
        messages.error(request, 'Please choose a photo first.')
        return redirect('hq:staff_list')
    content_type = getattr(photo, 'content_type', '') or ''
    if not content_type.startswith('image/'):
        messages.error(request, 'The selected file is not an image (use JPG, PNG or WebP).')
        return redirect('hq:staff_list')
    if photo.size > 10 * 1024 * 1024:
        messages.error(request, 'This photo is larger than 10 MB. Please choose a smaller one.')
        return redirect('hq:staff_list')
    old = user.profile_photo.name if user.profile_photo else ''
    user.profile_photo = photo  # core.image_signals compresses it on save
    try:
        user.save(update_fields=['profile_photo'])
    except Exception:
        messages.error(request, 'The photo could not be saved. Please try a different image.')
        return redirect('hq:staff_list')
    if old and old != user.profile_photo.name:
        try:
            user.profile_photo.storage.delete(old)
        except Exception:
            pass
    messages.success(request, f"Profile photo updated for {name}.")
    return redirect(request.META.get('HTTP_REFERER') or 'hq:staff_list')


@superadmin_required
@require_http_methods(["POST"])
def staff_delete_view(request, pk):
    """Permanently remove a non-superadmin account after explicit confirmation."""
    user = get_object_or_404(User, pk=pk)
    if user.is_superuser:
        messages.error(request, 'Platform superadmin accounts are protected and cannot be deleted here.')
        return redirect('hq:staff_list')
    if user.pk == request.user.pk:
        messages.error(request, 'You cannot delete the account currently in use.')
        return redirect('hq:staff_list')
    if request.POST.get('confirm_delete') != 'DELETE':
        messages.error(request, 'Account deletion was not confirmed. No changes were made.')
        return redirect('hq:staff_list')

    # Permanent deletion is never done in one click: an SMS code goes to the administrator's own
    # phone first (see hq/account_delete_views.py). The erasure happens only after it is entered.
    from .account_delete_views import start_deletion
    return start_deletion(request, user, request.POST.get('next', ''))


@superadmin_required
def sms_package_list_view(request):
    packages = SMSDebtPackage.objects.all().order_by('sort_order', 'price', 'id')
    return render(request, 'hq/sms_package_list.html', {'packages': packages})


@superadmin_required
@require_http_methods(["GET", "POST"])
def sms_package_create_view(request):
    form = SMSDebtPackageForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'SMS package created successfully.')
        return redirect('hq:sms_package_list')
    return render(request, 'hq/sms_package_form.html', {'form': form, 'title': 'Add SMS Package'})


@superadmin_required
@require_http_methods(["GET", "POST"])
def sms_package_edit_view(request, pk):
    package = get_object_or_404(SMSDebtPackage, pk=pk)
    form = SMSDebtPackageForm(request.POST or None, instance=package)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f"SMS package '{package.name}' updated successfully.")
        return redirect('hq:sms_package_list')
    return render(request, 'hq/sms_package_form.html', {'form': form, 'title': f'Edit SMS Package — {package.name}'})


@superadmin_required
@require_http_methods(["POST"])
def sms_package_toggle_view(request, pk):
    package = get_object_or_404(SMSDebtPackage, pk=pk)
    package.is_active = not package.is_active
    package.save(update_fields=['is_active'])
    messages.success(request, f"SMS package '{package.name}' is now {'active' if package.is_active else 'inactive'}.")
    return redirect('hq:sms_package_list')


@superadmin_required
def receipt_sms_package_list_view(request):
    packages = SMSReceiptPackage.objects.all().order_by('sort_order', 'price', 'id')
    return render(request, 'hq/receipt_sms_package_list.html', {'packages': packages})

@superadmin_required
@require_http_methods(["GET", "POST"])
def receipt_sms_package_create_view(request):
    form = SMSReceiptPackageForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Receipt SMS package created successfully.')
        return redirect('hq:receipt_sms_package_list')
    return render(request, 'hq/receipt_sms_package_form.html', {'form': form, 'title': 'Add Receipt SMS Package'})

@superadmin_required
@require_http_methods(["GET", "POST"])
def receipt_sms_package_edit_view(request, pk):
    package = get_object_or_404(SMSReceiptPackage, pk=pk)
    form = SMSReceiptPackageForm(request.POST or None, instance=package)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f"Receipt SMS package '{package.name}' updated successfully.")
        return redirect('hq:receipt_sms_package_list')
    return render(request, 'hq/receipt_sms_package_form.html', {'form': form, 'title': f'Edit Receipt SMS Package — {package.name}'})

@superadmin_required
@require_http_methods(["POST"])
def receipt_sms_package_toggle_view(request, pk):
    package = get_object_or_404(SMSReceiptPackage, pk=pk)
    package.is_active = not package.is_active
    package.save(update_fields=['is_active'])
    messages.success(request, f"Receipt SMS package '{package.name}' is now {'active' if package.is_active else 'inactive'}.")
    return redirect('hq:receipt_sms_package_list')

@superadmin_required
def marketing_sms_package_list_view(request):
    packages = SMSMarketingPackage.objects.all().order_by('sort_order', 'price', 'id')
    return render(request, 'hq/marketing_sms_package_list.html', {'packages': packages})

@superadmin_required
@require_http_methods(["GET", "POST"])
def marketing_sms_package_create_view(request):
    form = SMSMarketingPackageForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Customer Marketing SMS package created successfully.')
        return redirect('hq:marketing_sms_package_list')
    return render(request, 'hq/marketing_sms_package_form.html', {'form': form, 'title': 'Add Customer Marketing SMS Package'})

@superadmin_required
@require_http_methods(["GET", "POST"])
def marketing_sms_package_edit_view(request, pk):
    package = get_object_or_404(SMSMarketingPackage, pk=pk)
    form = SMSMarketingPackageForm(request.POST or None, instance=package)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f"Marketing SMS package '{package.name}' updated successfully.")
        return redirect('hq:marketing_sms_package_list')
    return render(request, 'hq/marketing_sms_package_form.html', {'form': form, 'title': f'Edit Marketing SMS Package — {package.name}'})

@superadmin_required
@require_http_methods(["POST"])
def marketing_sms_package_toggle_view(request, pk):
    package = get_object_or_404(SMSMarketingPackage, pk=pk)
    package.is_active = not package.is_active
    package.save(update_fields=['is_active', 'updated_at'])
    messages.success(request, f"Marketing SMS package '{package.name}' is now {'active' if package.is_active else 'inactive'}.")
    return redirect('hq:marketing_sms_package_list')

@login_required
def marketing_sms_access_view(request):
    return _to_packages_center(request, 'marketing')

@superadmin_required
def marketing_sms_purchase_list_view(request):
    purchases = visible_purchases(SMSMarketingPurchase.objects.select_related('owner', 'package').all())
    status = request.GET.get('status', '')
    if status in dict(SMSMarketingPurchase.Status.choices):
        purchases = purchases.filter(status=status)
    page_obj = Paginator(purchases, 20).get_page(request.GET.get('page'))
    return render(request, 'hq/marketing_sms_purchases.html', {'page_obj': page_obj, 'status_choices': SMSMarketingPurchase.Status.choices, 'current_status': status})

@superadmin_required
@require_http_methods(["POST"])
def approve_marketing_sms_purchase_view(request, pk):
    purchase = get_object_or_404(SMSMarketingPurchase.objects.select_related('owner', 'package'), pk=pk)
    if purchase.status != SMSMarketingPurchase.Status.PENDING:
        messages.warning(request, 'This Marketing SMS purchase has already been processed.')
        return redirect('hq:marketing_sms_purchases')
    from sms_integration.services import approve_marketing_sms_purchase
    approve_marketing_sms_purchase(purchase, reviewed_by=request.user)
    messages.success(request, f'Customer Marketing SMS activated for {purchase.owner.username}: +{purchase.package.sms_credits:,} SMS.')
    return redirect('hq:marketing_sms_purchases')

@superadmin_required
@require_http_methods(["POST"])
def reject_marketing_sms_purchase_view(request, pk):
    purchase = get_object_or_404(SMSMarketingPurchase, pk=pk)
    if purchase.status != SMSMarketingPurchase.Status.PENDING:
        messages.warning(request, 'This Marketing SMS purchase has already been processed.')
        return redirect('hq:marketing_sms_purchases')
    purchase.status = SMSMarketingPurchase.Status.REJECTED
    purchase.reviewed_at = timezone.now()
    purchase.reviewed_by = request.user
    purchase.admin_note = request.POST.get('admin_note', '').strip()
    purchase.save(update_fields=['status', 'reviewed_at', 'reviewed_by', 'admin_note'])
    notify_many([purchase.owner], 'Customer Marketing SMS payment update', f'Your {purchase.package.name} Marketing SMS payment was rejected. {purchase.admin_note}'.strip(), category=Notification.Category.SYSTEM, level=Notification.Level.WARNING, link_url=reverse('hq:marketing_sms'))
    messages.info(request, 'Marketing SMS purchase rejected.')
    return redirect('hq:marketing_sms_purchases')

@login_required
def receipt_sms_access_view(request):
    return _to_packages_center(request, 'receipt')

@superadmin_required
def receipt_sms_purchase_list_view(request):
    purchases = visible_purchases(SMSReceiptPurchase.objects.select_related('owner', 'package').all())
    status = request.GET.get('status', '')
    if status in dict(SMSReceiptPurchase.Status.choices):
        purchases = purchases.filter(status=status)
    page_obj = Paginator(purchases, 20).get_page(request.GET.get('page'))
    return render(request, 'hq/receipt_sms_purchases.html', {'page_obj': page_obj, 'status_choices': SMSReceiptPurchase.Status.choices, 'current_status': status})

@superadmin_required
@require_http_methods(["POST"])
def approve_receipt_sms_purchase_view(request, pk):
    purchase = get_object_or_404(SMSReceiptPurchase.objects.select_related('owner', 'package'), pk=pk)
    if purchase.status != SMSReceiptPurchase.Status.PENDING:
        messages.warning(request, 'This Receipt SMS purchase has already been processed.')
        return redirect('hq:receipt_sms_purchases')
    admin_note = request.POST.get('admin_note', '').strip()
    if admin_note:
        purchase.admin_note = admin_note
        purchase.save(update_fields=['admin_note'])

    from sms_integration.services import approve_receipt_sms_purchase
    approve_receipt_sms_purchase(purchase, reviewed_by=request.user)

    messages.success(request, f'Premium Receipt SMS activated for {purchase.owner.username}: +{purchase.package.sms_credits:,} SMS.')
    return redirect('hq:receipt_sms_purchases')

@superadmin_required
@require_http_methods(["POST"])
def reject_receipt_sms_purchase_view(request, pk):
    purchase = get_object_or_404(SMSReceiptPurchase, pk=pk)
    if purchase.status != SMSReceiptPurchase.Status.PENDING:
        messages.warning(request, 'This Receipt SMS purchase has already been processed.')
        return redirect('hq:receipt_sms_purchases')
    purchase.status = SMSReceiptPurchase.Status.REJECTED
    purchase.reviewed_at = timezone.now()
    purchase.reviewed_by = request.user
    purchase.admin_note = request.POST.get('admin_note', '').strip()
    purchase.save(update_fields=['status', 'reviewed_at', 'reviewed_by', 'admin_note'])
    notify_many([purchase.owner], 'Premium Receipt SMS payment update', f'Your {purchase.package.name} Premium Receipt SMS payment request was rejected. {purchase.admin_note}'.strip(), category=Notification.Category.SYSTEM, level=Notification.Level.WARNING, link_url=reverse('sms_integration:receipt_sms'))
    messages.info(request, 'Receipt SMS purchase rejected.')
    return redirect('hq:receipt_sms_purchases')

@login_required
def referrals_view(request):
    """Owner page: personal invite code/link, invited customers and the renewal discount earned."""
    from django.core.exceptions import PermissionDenied
    from . import referrals
    if request.user.is_superuser or not request.user.is_management_user():
        raise PermissionDenied('Invites are available to business owners.')
    owner = request.user.business_owner()
    ps = PaymentSettings.load()
    code = referrals.get_or_create_code(owner) if ps.referral_enabled else None
    invited = list(owner.referrals_made.select_related('signup'))
    return render(request, 'hq/referrals.html', {
        'enabled': ps.referral_enabled, 'code': code,
        'invite_url': request.build_absolute_uri(reverse('hq:signup') + f'?ref={code.code}') if code else '',
        'invited': invited, 'percent_per_invite': ps.referral_percent_per_invite,
        'max_percent': ps.referral_max_percent,
        'available_percent': referrals.available_percent(owner),
        'paid_count': sum(1 for r in invited if r.status in ('QUALIFIED', 'USED')),
        'pending_count': sum(1 for r in invited if r.status == 'PENDING'),
        'target_invites': max(1, -(-int(ps.referral_max_percent) // max(1, int(ps.referral_percent_per_invite)))),
        'qualified_total': sum(1 for r in invited if r.status in ('QUALIFIED', 'USED')),
    })


@superadmin_required
def payment_settings_view(request):
    payment_settings = PaymentSettings.load()
    form = PaymentSettingsForm(request.POST or None, instance=payment_settings)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, "Payment settings updated.")
        return redirect('hq:payment_settings')
    return render(request, 'hq/payment_settings.html', {'form': form})


@login_required
def debt_sms_access_view(request):
    return _to_packages_center(request, 'debt')

@superadmin_required
def debt_sms_purchase_list_view(request):
    purchases = visible_purchases(SMSDebtPurchase.objects.select_related('owner','package').all())
    status = request.GET.get('status','')
    if status:
        purchases = purchases.filter(status=status)
    page_obj = Paginator(purchases, 20).get_page(request.GET.get('page'))
    return render(request, 'hq/debt_sms_purchases.html', {'page_obj': page_obj, 'status_choices': SMSDebtPurchase.Status.choices, 'current_status': status})


@superadmin_required
@require_http_methods(["POST"])
def approve_debt_sms_purchase_view(request, pk):
    purchase = get_object_or_404(SMSDebtPurchase.objects.select_related('owner','package'), pk=pk)
    if purchase.status != SMSDebtPurchase.Status.PENDING:
        messages.warning(request, 'This SMS purchase has already been processed.')
        return redirect('hq:debt_sms_purchases')
    admin_note = request.POST.get('admin_note', '').strip()
    if admin_note:
        purchase.admin_note = admin_note
        purchase.save(update_fields=['admin_note'])

    from sms_integration.services import approve_debt_sms_purchase
    approve_debt_sms_purchase(purchase, reviewed_by=request.user)

    messages.success(request, f'Premium Debt SMS activated for {purchase.owner.username}: +{purchase.package.sms_credits:,} SMS.')
    return redirect('hq:debt_sms_purchases')


@superadmin_required
@require_http_methods(["POST"])
def reject_debt_sms_purchase_view(request, pk):
    purchase = get_object_or_404(SMSDebtPurchase, pk=pk)
    if purchase.status != SMSDebtPurchase.Status.PENDING:
        messages.warning(request, 'This SMS purchase has already been processed.')
        return redirect('hq:debt_sms_purchases')
    purchase.status = SMSDebtPurchase.Status.REJECTED
    purchase.reviewed_at = timezone.now()
    purchase.reviewed_by = request.user
    purchase.admin_note = request.POST.get('admin_note','').strip()
    purchase.save(update_fields=['status','reviewed_at','reviewed_by','admin_note'])
    notify_many([purchase.owner], 'Premium Debt SMS payment update', f'Your {purchase.package.name} Premium SMS payment request was rejected. {purchase.admin_note}'.strip(), category=Notification.Category.SYSTEM, level=Notification.Level.WARNING, link_url=reverse('sms_integration:debt_sms'))
    messages.info(request, 'SMS purchase rejected.')
    return redirect('hq:debt_sms_purchases')


# ------------------------------------------------------- Package builder -

_CUSTOMER_SETUP_NOTES = """\
# Welcome to {business_name}!

This SmartPOS system is now fully yours — {owner_name}. Your database
(db.sqlite3) is completely fresh and contains no data from any other customer.

## Getting Started (example for Termux/Android)

    cd path-where-you-placed-this-file
    pip install -r requirements.txt
    python manage.py create_admin
    python manage.py runserver 0.0.0.0:8000

Then open http://127.0.0.1:8000 in your browser.

## Important
- Your .env file contains your unique SECRET_KEY. Keep it private and never share it.
- Add your Africa's Talking account details in /admin/ if you want SMS features to work.
- Make regular backups of db.sqlite3 — it contains ALL your data (products, sales, employees).
- For technical support, contact the person who supplied this system.
"""


def _build_customer_package(signup):
    """
    Copies the whole project's SOURCE CODE into a temp directory, applies a
    completely fresh set of migrations against a brand-new EMPTY SQLite
    file there (never a copy of this server's live database — the customer
    starts at zero, exactly like a fresh install), writes a new .env with a
    freshly generated SECRET_KEY (never this server's real one), then zips
    it all up. Returns the path to the finished zip file.
    """
    base_dir = str(django_settings.BASE_DIR)
    slug = "".join(ch for ch in signup.business_name.lower().replace(' ', '_') if ch.isalnum() or ch == '_') or 'mteja'
    timestamp = timezone.localtime(timezone.now()).strftime('%Y%m%d%H%M%S')
    package_name = f"{slug}_{timestamp}"

    work_dir = tempfile.mkdtemp(prefix='customer_package_')
    project_copy = os.path.join(work_dir, package_name)

    # Never copy the LIVE server's own secrets/data into a customer package.
    shutil.copytree(
        base_dir, project_copy,
        ignore=shutil.ignore_patterns(
            '.git', '__pycache__', 'media', 'backups', 'staticfiles',
            'customer_packages', '.venv', 'venv', 'node_modules',
            'db.sqlite3', '*.pyc', '.env',
        ),
    )

    # Fresh .env for the customer: brand-new SECRET_KEY, no leftover server secrets.
    env_example_path = os.path.join(project_copy, '.env.example')
    env_path = os.path.join(project_copy, '.env')
    if os.path.exists(env_example_path):
        from django.core.management.utils import get_random_secret_key
        with open(env_example_path, 'r') as fh:
            env_content = fh.read()
        env_content = env_content.replace(
            'SECRET_KEY=change-this-to-a-long-random-string',
            f'SECRET_KEY={get_random_secret_key()}',
        )
        with open(env_path, 'w') as fh:
            fh.write(env_content)

    with open(os.path.join(project_copy, 'ANZA_HAPA.md'), 'w') as fh:
        fh.write(_CUSTOMER_SETUP_NOTES.format(business_name=signup.business_name, owner_name=signup.owner_full_name))

    fresh_db_path = os.path.join(project_copy, 'db.sqlite3')
    env = os.environ.copy()
    env['SQLITE_PATH'] = fresh_db_path
    env.pop('DATABASE_URL', None)
    env.pop('DATABASE_ENGINE', None)  # force plain SQLite regardless of what THIS server uses

    result = subprocess.run(
        [sys.executable, 'manage.py', 'migrate', '--noinput'],
        cwd=project_copy, env=env, capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError(result.stderr[-800:] or "Migration failed with no error output.")

    zip_base_path = os.path.join(tempfile.gettempdir(), package_name)
    zip_path = shutil.make_archive(zip_base_path, 'zip', root_dir=work_dir, base_dir=package_name)

    shutil.rmtree(work_dir, ignore_errors=True)
    return zip_path


@superadmin_required
def customer_sms_access_view(request):
    customers_qs = User.objects.filter(role=User.Role.ADMIN, is_superuser=False).select_related('subscription').order_by('username')
    if request.method == 'POST':
        customer_id = request.POST.get('customer_id')
        customer = get_object_or_404(User, pk=customer_id, role=User.Role.ADMIN, is_superuser=False)
        obj, _ = CustomerSMSCredentials.objects.get_or_create(owner=customer)
        obj.access_enabled = request.POST.get('access_enabled') == '1'
        obj.save(update_fields=['access_enabled','updated_at'])
        messages.success(request, f"Custom SMS credential access {'enabled' if obj.access_enabled else 'disabled'} for {customer.username}.")
        page_number = request.GET.get('page')  # the form posts back to the current URL, so stay on the same page
        return redirect(f"{reverse('hq:customer_sms_access')}?page={page_number}" if page_number else 'hq:customer_sms_access')
    page_obj = Paginator(customers_qs, 20).get_page(request.GET.get('page'))
    for customer in page_obj:
        customer.sms_credentials_obj, _ = CustomerSMSCredentials.objects.get_or_create(owner=customer)
        customer.sms_wallet_obj, _ = SMSWallet.objects.get_or_create(owner=customer, defaults={'credits_granted':500})
    return render(request, 'hq/customer_sms_access.html', {'page_obj': page_obj})
