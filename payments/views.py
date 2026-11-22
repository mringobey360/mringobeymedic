import json
import logging
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction as db_transaction
from django.db.models import F
from django.http import Http404, HttpResponse, JsonResponse
from django.utils.html import escape
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts.decorators import admin_required
from core import ratelimit
from django.views.decorators.cache import never_cache
from .privacy import redact_payload
from .security import secure_payment_page
from hq.models import PaymentSettings, SignupRequest, Subscription
from pharmacies.models import BranchAddOn
from daily_reports.models import DailyReportPackage, DailyReportPurchase
from sms_integration.models import (
    SMSDebtPackage, SMSDebtPurchase, SMSReceiptPackage, SMSReceiptPurchase,
    SMSMarketingPackage, SMSMarketingPurchase,
)

from sms_integration.services import ONLINE_CLOSED_NOTE

from . import pesapal_client, abliner_client, networks
from .models import PaymentEvent, PaymentGatewaySettings, PesapalSettings, PesapalTransaction
from .services import (
    MAX_ABLINER_ATTEMPTS, abliner_attempts, abliner_resend_wait, abliner_sent_seconds_ago, create_transaction,
    find_resumable_transaction, pesapal_usable, refresh_status, resolve_gateway, submit_to_abliner,
    submit_to_pesapal, switch_to_pesapal,
)

logger = logging.getLogger('payments.views')

POLL_MIN_INTERVAL_SECONDS = 2          # browser polling never hits Pesapal more often than this
RESUME_WINDOW_SECONDS = 1800           # an unpaid order can be resumed for 30 minutes
BRANCH_RENEWAL_WINDOW_DAYS = BranchAddOn.RENEWAL_WINDOW_DAYS   # renew an active branch this long before it is due
GENERIC_FAILURE = "Something went wrong while starting the payment. No money was taken. Please try again, or use the manual payment option."


# =============================================================== Helpers ===

def _purposes_pkg():
    P = PesapalTransaction.Purpose
    return (P.SMS_DEBT_PACKAGE, P.SMS_RECEIPT_PACKAGE, P.SMS_MARKETING_PACKAGE, P.DAILY_REPORT_PACKAGE, P.LAYAWAY_SMS_PACKAGE)


def _safe_target(txn):
    """The record a payment is for, or None if it has been deleted."""
    try:
        return txn.target
    except Exception:
        return None


def _final_destination(txn: PesapalTransaction):
    """Where to send the browser once a transaction has completed successfully."""
    purpose = txn.purpose
    P = PesapalTransaction.Purpose
    target = _safe_target(txn)
    if purpose == P.SUBSCRIPTION:
        if target is not None:
            return reverse('hq:signup_status', kwargs={'public_id': target.public_id})
        return reverse('accounts:login')
    if purpose == P.SUBSCRIPTION_RENEWAL:
        return reverse('dashboard:home')
    if purpose in (P.BRANCH_ADDON, P.BRANCH_ADDON_RENEWAL):
        return reverse('dashboard:home')
    if purpose == P.LAYAWAY_SMS_PACKAGE:
        return reverse('layaway:sms_packages')
    if purpose == P.MARKETPLACE_ORDER and target is not None:
        return reverse('marketplace:payment_result', kwargs={'reference': target.reference})
    if purpose in _purposes_pkg():
        return reverse('hq:packages_center')
    if purpose in (P.MARKETPLACE_ONLINE_SELLING, P.MARKETPLACE_ORDER_SMS):
        return reverse('marketplace:seller_dashboard')
    if purpose == P.MARKETPLACE_ORDER:
        return reverse('marketplace:cart')
    return reverse('accounts:login')


def _marketplace_cancel_url(txn: PesapalTransaction):
    """Professional 'payment cancelled' page for marketplace checkouts (None for every other purpose)."""
    if txn.purpose != PesapalTransaction.Purpose.MARKETPLACE_ORDER:
        return None
    target = _safe_target(txn)
    if target is None:
        return None
    return reverse('marketplace:payment_cancelled', kwargs={'reference': target.reference})


def _mark_cancelled_by_customer(txn, message):
    """Close an unpaid request as cancelled (idempotent; never touches a completed payment)."""
    with db_transaction.atomic():
        locked = PesapalTransaction.objects.select_for_update().get(pk=txn.pk)
        if locked.status in (PesapalTransaction.Status.PENDING, PesapalTransaction.Status.SUBMITTED):
            locked.status = PesapalTransaction.Status.CANCELLED
            locked.status_description = "Cancelled by customer from SmartPOS checkout."
            locked.error_message = ""
            locked.save(update_fields=["status", "status_description", "error_message", "updated_at"])
            PaymentEvent.objects.create(
                payment=locked, gateway=locked.gateway, event_type="CUSTOMER_CANCELLED",
                provider_status="cancelled", message=message,
            )
        return locked


def _manual_fallback_url(txn: PesapalTransaction):
    """Where 'Use manual mobile-money payment instead' should send the customer."""
    purpose = txn.purpose
    P = PesapalTransaction.Purpose
    target = _safe_target(txn)
    if purpose == P.SUBSCRIPTION and target is not None:
        return reverse('hq:payment_instructions', kwargs={'public_id': target.public_id})
    if purpose == P.SUBSCRIPTION_RENEWAL:
        return reverse('accounts:request_renewal')
    if purpose in (P.BRANCH_ADDON, P.BRANCH_ADDON_RENEWAL) and target is not None:
        return reverse('pharmacies:branch_addon_payment', kwargs={'pk': target.pharmacy_id})
    if purpose in _purposes_pkg():
        return reverse('hq:packages_center')
    if purpose in (P.MARKETPLACE_ONLINE_SELLING, P.MARKETPLACE_ORDER_SMS):
        return reverse('marketplace:seller_dashboard')
    if purpose == P.MARKETPLACE_ORDER:
        return reverse('marketplace:cart')
    return reverse('accounts:login')


def _owner_contact(user):
    return {
        'payer_name': (user.get_full_name() or user.username),
        'payer_email': user.email or '',
        'payer_phone': getattr(user, 'phone_number', '') or '',
    }


def _throttled_response(request, retry_after, *, fallback):
    """Friendly 'slow down' answer for the public payment endpoints (never a stack trace)."""
    messages.error(request, f'Too many payment attempts. Please wait {ratelimit.human_wait(retry_after)} and try again.')
    return redirect(fallback)


def _public_throttle(request, scope, *, limit, window):
    allowed, retry = ratelimit.hit(scope, ratelimit.client_ip(request), limit=limit, window=window)
    return allowed, retry


def _ensure_live_db_connection():
    """Drop a connection the database already closed BEFORE we open the payment transaction,
    so a stale pooled connection cannot fail halfway through creating a payment."""
    from django.db import connection
    try:
        if connection.connection is not None and not connection.is_usable():
            connection.close()
    except Exception:
        connection.close()


def _guarded(fallback):
    """
    Last line of defence for every payment-start view. Whatever goes wrong the
    customer lands on a normal page with a clear message — never a 500 — and the
    full traceback is logged. `fallback` is a url name or a callable(request, *a, **kw) -> url.
    """
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            try:
                return view(request, *args, **kwargs)
            except (PermissionDenied, Http404):
                raise
            except pesapal_client.PesapalError as exc:
                logger.warning("Payment start refused (%s): %s", view.__name__, exc.technical)
                messages.error(request, exc.detail)
            except Exception:
                logger.exception("Unexpected error in payment view %s", view.__name__)
                messages.error(request, GENERIC_FAILURE)
            url = fallback(request, *args, **kwargs) if callable(fallback) else reverse(fallback)
            return redirect(url)
        return wrapper
    return decorator


# --------------------------------------------------------- payability ---

def _renewal_choice(description, subscription):
    """Tier/plan encoded in a renewal transaction's '[TIER|PLAN]' prefix, validated."""
    tier = plan = None
    if description and description.startswith('[') and ']' in description:
        token = description[1:description.index(']')]
        if '|' in token:
            tier, plan = token.split('|', 1)
        else:
            plan = token
    if plan not in (Subscription.Plan.MONTHLY, Subscription.Plan.YEARLY):
        plan = subscription.plan
    if tier not in SignupRequest.Tier.values:
        tier = getattr(subscription, 'tier', SignupRequest.Tier.STARTER)
    return tier, plan


def _signup_amount(signup):
    payment_settings = PaymentSettings.load()
    amount = payment_settings.price_for(signup.plan, signup.tier)
    if signup.sms_package_id:
        amount += signup.sms_package.price
    if signup.receipt_sms_package_id:
        amount += signup.receipt_sms_package.price
    return amount


def _payability(purpose, target, description=''):
    """
    Returns (ok, message_or_amount). Single source of truth for 'can this thing be
    paid for right now, and for how much' — used both when starting a payment and
    when retrying one, so a stale/expired/processed item can never be charged again.
    """
    P = PesapalTransaction.Purpose
    if target is None:
        return False, "This payment is no longer valid. Please start again."
    if purpose == P.SUBSCRIPTION:
        if target.status != SignupRequest.Status.PENDING_PAYMENT:
            return False, "This registration has already been processed."
        return True, _signup_amount(target)
    if purpose == P.SUBSCRIPTION_RENEWAL:
        tier, plan = _renewal_choice(description, target)
        from hq import referrals
        net, _pct = referrals.discounted_price(target.owner, PaymentSettings.load().price_for(plan, tier))
        return True, net
    if purpose == P.BRANCH_ADDON:
        if target.status != BranchAddOn.Status.PENDING_PAYMENT:
            return False, "This branch payment request has already been processed."
        return True, target.refresh_price() or target.monthly_price
    if purpose == P.BRANCH_ADDON_RENEWAL:
        if target.status != BranchAddOn.Status.ACTIVE:
            return False, "Only an active branch can be renewed."
        return True, target.refresh_price() or target.monthly_price
    if purpose in _purposes_pkg():
        if target.status != target.Status.PENDING:
            return False, "This package request has already been processed. Please choose the package again."
        return True, target.amount
    if purpose in (P.MARKETPLACE_ONLINE_SELLING, P.MARKETPLACE_ORDER_SMS):
        from marketplace.models import OnlineServiceSubscription
        if not isinstance(target, OnlineServiceSubscription) or target.status != OnlineServiceSubscription.Status.PENDING:
            return False, 'This Marketplace service payment request is no longer payable.'
        return True, target.monthly_amount
    return False, "Unknown payment type."


def _resume_redirect(txn):
    return redirect('payments:checkout', merchant_reference=txn.merchant_reference)


def _begin_payment(request, *, purpose, target, description, owner=None, contact=None,
                   fallback_url, description_prefix=None, gateway=None):
    """
    Create (or resume) the transaction for `target` and send the customer to checkout.

    The target row is locked while we decide, so a double-click / two tabs / a
    retry racing the original request can never create two Pesapal orders for the
    same item: the second request waits, then finds and resumes the first.
    """
    ok, result = _payability(purpose, target, description)
    if not ok:
        messages.info(request, result)
        return redirect(fallback_url)
    amount = result
    _ensure_live_db_connection()
    # Resolve the provider once for this request and use the same choice for both
    # resume lookup and creation. This prevents a stale PesaPal transaction from
    # being resumed after the Packages page offered ABLiner checkout.
    selected_gateway = resolve_gateway(gateway)

    with db_transaction.atomic():
        type(target).objects.select_for_update().get(pk=target.pk)
        live = find_resumable_transaction(
            purpose, target, window_seconds=RESUME_WINDOW_SECONDS,
            description_prefix=description_prefix, amount=amount,
            gateway=selected_gateway,
        )
        if live:
            return _resume_redirect(live)
        txn = create_transaction(
            purpose=purpose, target=target, amount=amount, description=description,
            owner=owner, gateway=selected_gateway, **(contact or {}),
        )
        failure = _submit_or_record_failure(request, txn)
    if failure:
        messages.error(request, failure)
        return redirect(fallback_url)
    return _resume_redirect(txn)


def _submit_or_record_failure(request, txn):
    """Submit to Pesapal. On failure the transaction is marked FAILED (and kept for
    support) and a customer-safe message is returned; never raises.

    ABLiner payments are NOT submitted here: the customer first picks a network and
    types the number on our own checkout page (see abliner_pay_view). We only make
    sure ABLiner is usable so a broken setup fails early with a clear message."""
    try:
        if txn.gateway == PaymentGatewaySettings.Gateway.ABLINER:
            abliner_client.settings_obj()          # raises ABLinerError when not enabled/configured
            return None
        submit_to_pesapal(txn, request=request)
        return None
    except pesapal_client.PesapalError as exc:
        technical, safe = exc.technical, exc.detail
    except Exception as exc:                       # noqa: BLE001 — must not leak a 500 here
        logger.exception("Unexpected error submitting txn #%s to Pesapal", txn.pk)
        technical, safe = f"Unexpected error: {exc}", GENERIC_FAILURE
    txn.status = PesapalTransaction.Status.FAILED
    txn.error_message = technical[:255]
    txn.status_description = safe[:255]
    txn.save(update_fields=['status', 'error_message', 'status_description', 'updated_at'])
    return f"Online payment could not be started: {safe} (Ref #{txn.pk})"


def _owner_only(request, message):
    if request.user.is_superuser or not request.user.is_management_user():
        raise PermissionDenied(message)
    return request.user.business_owner()


# ========================================================== 1. Subscription ===

@require_http_methods(["POST"])
@_guarded(lambda request, public_id: reverse('hq:signup_status', kwargs={'public_id': public_id}))
def initiate_subscription_payment(request, public_id):
    """Public — new customer paying to activate their registration."""
    signup = get_object_or_404(SignupRequest, public_id=public_id)
    allowed, retry = _public_throttle(request, 'pay-start', limit=15, window=600)
    if not allowed:
        return _throttled_response(request, retry, fallback=reverse('hq:signup_status', kwargs={'public_id': public_id}))
    return _begin_payment(
        request, purpose=PesapalTransaction.Purpose.SUBSCRIPTION, target=signup,
        description=f"SmartPOS — {signup.get_plan_display()} subscription — {signup.business_name}",
        contact={'payer_name': signup.owner_full_name, 'payer_email': signup.email, 'payer_phone': signup.phone_number},
        fallback_url=reverse('hq:signup_status', kwargs={'public_id': public_id}),
    )


@login_required
@require_http_methods(["GET", "POST"])
def initiate_marketplace_service_payment(request, subscription_id):
    # GET is allowed on purpose: the Seller Centre redirects here right after a request is created and
    # "Continue payment" is a plain link. It only creates/resumes the caller's own pending payment and
    # forwards to the checkout page, so it is safe and idempotent (a 405 here blocked every payment).
    from marketplace.models import OnlineServiceSubscription
    from marketplace.views import _seller_pharmacies
    service = get_object_or_404(OnlineServiceSubscription.objects.select_related('store','store__pharmacy'), pk=subscription_id, store__pharmacy__in=_seller_pharmacies(request.user))
    purpose_map = {OnlineServiceSubscription.Service.ONLINE_SELLING: PesapalTransaction.Purpose.MARKETPLACE_ONLINE_SELLING, OnlineServiceSubscription.Service.ORDER_SMS: PesapalTransaction.Purpose.MARKETPLACE_ORDER_SMS}
    purpose = purpose_map.get(service.service)
    if not purpose:
        messages.error(request, 'Unknown Marketplace service.')
        return redirect('marketplace:seller_dashboard')
    return _begin_payment(request, purpose=purpose, target=service, description=f'SmartPOS Marketplace — {service.get_service_display()} — {service.store.public_name}', owner=request.user, contact=_owner_contact(request.user), fallback_url=reverse('marketplace:seller_dashboard'))


# ================================================== 2. Subscription renewal ===

@login_required
@require_http_methods(["POST"])
@_guarded('accounts:request_renewal')
def initiate_renewal_payment(request):
    """A locked-out owner paying online to renew (or change tier/period of) their subscription."""
    if not request.user.is_management_user() or request.user.is_superuser:
        raise PermissionDenied("Only the business owner can renew a subscription.")
    owner = request.user.business_owner()
    try:
        subscription = owner.subscription
    except Subscription.DoesNotExist:
        messages.error(request, "No subscription was found for this account. Please contact support.")
        return redirect('accounts:subscription_expired')

    plan = request.POST.get('plan', subscription.plan)
    tier = request.POST.get('tier', getattr(subscription, 'tier', SignupRequest.Tier.STARTER))
    if plan not in (Subscription.Plan.MONTHLY, Subscription.Plan.YEARLY) or tier not in SignupRequest.Tier.values:
        messages.error(request, "Please select a valid renewal plan.")
        return redirect('accounts:request_renewal')

    prefix = f"[{tier}|{plan}]"
    return _begin_payment(
        request, purpose=PesapalTransaction.Purpose.SUBSCRIPTION_RENEWAL, target=subscription,
        description=f"{prefix} SmartPOS — subscription renewal ({tier.title()} / {plan.title()})",
        description_prefix=prefix, owner=owner, contact=_owner_contact(owner),
        fallback_url=reverse('accounts:request_renewal'),
    )


# ======================================================== 3. Branch add-on ===

def _branch_for(request, pk):
    owner = request.user.business_owner()
    pharmacy = get_object_or_404(owner.managed_pharmacies.all(), pk=pk)
    return pharmacy, getattr(pharmacy, 'addon', None)


@admin_required
@require_http_methods(["POST"])
@_guarded(lambda request, pk: reverse('pharmacies:list'))
def initiate_branch_addon_payment(request, pk):
    """Pay online to unlock a newly added (additional) branch."""
    pharmacy, addon = _branch_for(request, pk)
    if addon is None:
        messages.info(request, f"'{pharmacy.name}' is included in your plan — no payment needed.")
        return redirect('pharmacies:list')
    owner = request.user.business_owner()
    return _begin_payment(
        request, purpose=PesapalTransaction.Purpose.BRANCH_ADDON, target=addon,
        description=f"[{addon.get_package_tier_display()}|{addon.get_billing_plan_display()}] SmartPOS — additional branch — {pharmacy.name}",
        owner=owner, contact=_owner_contact(owner),
        fallback_url=reverse('pharmacies:branch_addon_payment', kwargs={'pk': pharmacy.pk}),
    )


@admin_required
@require_http_methods(["POST"])
@_guarded(lambda request, pk: reverse('pharmacies:list'))
def initiate_branch_addon_renewal_payment(request, pk):
    """Pay online for the next month of an ALREADY ACTIVE additional branch."""
    pharmacy, addon = _branch_for(request, pk)
    if addon is None:
        messages.info(request, f"'{pharmacy.name}' is included in your plan — no payment needed.")
        return redirect('pharmacies:list')
    if addon.status != BranchAddOn.Status.ACTIVE:
        messages.info(request, "Only an active branch can be renewed.")
        return redirect('pharmacies:branch_addon_payment', pk=pharmacy.pk)
    due = addon.next_billing_date
    if due and due > timezone.localdate() + timedelta(days=BRANCH_RENEWAL_WINDOW_DAYS):
        messages.info(request, f"'{pharmacy.name}' is paid up until {due:%d %b %Y}. You can renew it closer to that date.")
        return redirect('pharmacies:list')
    owner = request.user.business_owner()
    return _begin_payment(
        request, purpose=PesapalTransaction.Purpose.BRANCH_ADDON_RENEWAL, target=addon,
        description=f"[{addon.get_package_tier_display()}|{addon.get_billing_plan_display()}] SmartPOS — branch renewal — {pharmacy.name}",
        owner=owner, contact=_owner_contact(owner),
        fallback_url=reverse('pharmacies:branch_addon_payment', kwargs={'pk': pharmacy.pk}),
    )


# =================================================== 4. Package purchases ===
# Buying a package the customer ALREADY has simply tops it up: credits are added
# to the live entitlement and its validity is extended (see the approve_* services).

_PACKAGE_SERVICES = {
    'debt': {
        'package': SMSDebtPackage, 'purchase': SMSDebtPurchase, 'label': 'Premium Debt SMS',
        'purpose': PesapalTransaction.Purpose.SMS_DEBT_PACKAGE,
        'denied': 'Premium Debt SMS is available to business owners.',
    },
    'receipt': {
        'package': SMSReceiptPackage, 'purchase': SMSReceiptPurchase, 'label': 'Premium Receipt SMS',
        'purpose': PesapalTransaction.Purpose.SMS_RECEIPT_PACKAGE,
        'denied': 'Premium Receipt SMS is available to business owners.',
    },
    'marketing': {
        'package': SMSMarketingPackage, 'purchase': SMSMarketingPurchase, 'label': 'Customer Marketing SMS',
        'purpose': PesapalTransaction.Purpose.SMS_MARKETING_PACKAGE,
        'denied': 'Customer Marketing SMS is available to business owners.',
    },
    'report': {
        'package': DailyReportPackage, 'purchase': DailyReportPurchase, 'label': 'Intelligent Report SMS',
        'purpose': PesapalTransaction.Purpose.DAILY_REPORT_PACKAGE,
        'denied': 'Intelligent Report SMS is available to business owners.',
    },
}


def _online_purchase_for(service, owner, package):
    """
    The PENDING purchase row to attach an ONLINE payment to.

    Online checkouts get their OWN purchase row (blank payment_reference) so they can
    never overwrite a manual mobile-money request the customer submitted for HQ to
    verify. Rules, all under a per-owner lock:
      * stale online placeholders (checkout abandoned: no live order, nothing paid)
        are closed as REJECTED so they never block the manual flow or clutter HQ;
      * a placeholder for the SAME package that still has a live order is resumed;
      * otherwise a fresh purchase is created with the package's CURRENT price.
    """
    model = service['purchase']
    purpose = service['purpose']
    ct = ContentType.objects.get_for_model(model)
    now = timezone.now()
    live_cutoff = now - timedelta(seconds=RESUME_WINDOW_SECONDS)

    placeholders = list(model.objects.filter(owner=owner, status=model.Status.PENDING, payment_reference=''))
    reusable = None
    for purchase in placeholders:
        txns = PesapalTransaction.objects.filter(content_type=ct, object_id=purchase.pk, purpose=purpose)
        if not txns.exists():
            continue                                    # not an online placeholder — leave alone
        paid = txns.filter(status=PesapalTransaction.Status.COMPLETED).exists()
        live = txns.filter(status=PesapalTransaction.Status.SUBMITTED, created_at__gte=live_cutoff).exists()
        if paid:
            continue                                    # paid, activation pending — never touch
        if live and purchase.package_id == package.pk and reusable is None:
            reusable = purchase
            continue
        if not live:
            model.objects.filter(pk=purchase.pk, status=model.Status.PENDING).update(
                status=model.Status.REJECTED, reviewed_at=now,
                admin_note=ONLINE_CLOSED_NOTE,
            )
    if reusable is not None:
        return reusable
    return model.objects.create(owner=owner, package=package, amount=package.price)


def _initiate_package_payment(request, key):
    service = _PACKAGE_SERVICES[key]
    owner = _owner_only(request, service['denied'])
    package = get_object_or_404(service['package'], pk=request.POST.get('package_id'), is_active=True)
    if package.custom_owner_id and package.custom_owner_id != owner.pk:
        raise PermissionDenied('This custom SMS quote belongs to another business.')
    fallback = reverse('hq:packages_center')

    with db_transaction.atomic():
        # Serialise per owner so two quick clicks cannot create two purchases.
        type(owner).objects.select_for_update().get(pk=owner.pk)
        purchase = _online_purchase_for(service, owner, package)
    return _begin_payment(
        request, purpose=service['purpose'], target=purchase,
        description=f"SmartPOS — {service['label']} — {package.name}",
        owner=owner, contact=_owner_contact(owner), fallback_url=fallback,
    )


@login_required
@require_http_methods(["POST"])
@_guarded('hq:packages_center')
def initiate_sms_debt_payment(request):
    return _initiate_package_payment(request, 'debt')


@login_required
@require_http_methods(["POST"])
@_guarded('hq:packages_center')
def initiate_sms_marketing_payment(request):
    return _initiate_package_payment(request, 'marketing')


@login_required
@require_http_methods(["POST"])
@_guarded('hq:packages_center')
def initiate_sms_receipt_payment(request):
    return _initiate_package_payment(request, 'receipt')


@login_required
@require_http_methods(["POST"])
@_guarded('hq:packages_center')
def initiate_daily_report_payment(request):
    return _initiate_package_payment(request, 'report')


@login_required
@require_http_methods(["POST"])
@_guarded('layaway:sms_packages')
def initiate_layaway_sms_payment(request):
    """Start a secure online checkout for a branch-scoped Layaway SMS credit pack."""
    if not request.user.is_management_user() or request.user.is_superuser:
        raise PermissionDenied("Only the shop owner/manager can purchase Layaway SMS credits.")
    pharmacy = getattr(request, 'pharmacy', None)
    if pharmacy is None:
        raise PermissionDenied("Select the branch that will receive these SMS credits.")
    from layaway.models import LayawaySMSPackage, LayawaySMSPurchase
    package = get_object_or_404(LayawaySMSPackage, pk=request.POST.get('package_id'), is_active=True)
    owner = request.user.business_owner()
    with db_transaction.atomic():
        # A fresh pending target is created per purchase. _begin_payment resumes an
        # existing live gateway transaction for this same target on double-clicks.
        purchase = LayawaySMSPurchase.objects.create(
            pharmacy=pharmacy, package=package, requested_by=owner,
            amount=package.price, payment_reference='', customer_note='Online checkout',
        )
    return _begin_payment(
        request, purpose=PesapalTransaction.Purpose.LAYAWAY_SMS_PACKAGE, target=purchase,
        description=f"SmartPOS — Layaway SMS — {package.name} ({package.sms_credits} credits)",
        owner=owner, contact=_owner_contact(owner), fallback_url=reverse('layaway:sms_packages'),
    )


# ===================================================================== Retry ===

@require_http_methods(["POST"])
@_guarded(lambda request, merchant_reference: reverse('payments:pending', kwargs={'merchant_reference': merchant_reference}))
def retry_view(request, merchant_reference):
    """
    'Try again' after a failed/cancelled/invalid payment.

    A Pesapal order that reached a final state generally won't reopen for payment,
    so this creates a brand-new transaction for the same item — but only if that
    item can still be paid for, and at its CURRENT price (never a stale amount).
    """
    allowed, retry = _public_throttle(request, 'pay-retry', limit=15, window=600)
    if not allowed:
        return _throttled_response(request, retry, fallback=reverse('payments:pending', kwargs={'merchant_reference': merchant_reference}))
    old = get_object_or_404(PesapalTransaction, merchant_reference=merchant_reference)
    if not old.is_final or old.status == PesapalTransaction.Status.COMPLETED:
        return redirect('payments:pending', merchant_reference=old.merchant_reference)

    target = _safe_target(old)
    wanted_gateway = PaymentGatewaySettings.Gateway.PESAPAL if request.POST.get('gateway') == 'PESAPAL' else None
    prefix = None
    if old.purpose == PesapalTransaction.Purpose.SUBSCRIPTION_RENEWAL and old.description.startswith('['):
        prefix = old.description[:old.description.index(']') + 1] if ']' in old.description else None
    return _begin_payment(
        request, purpose=old.purpose, target=target, description=old.description,
        description_prefix=prefix, owner=old.owner, gateway=wanted_gateway,
        contact={'payer_name': old.payer_name, 'payer_email': old.payer_email, 'payer_phone': old.payer_phone},
        fallback_url=_manual_fallback_url(old),
    )


# ================================================================= Tracking ===

def _refresh_for_browser(txn):
    """Refresh a transaction for a page view; never raises, never hammers Pesapal."""
    if txn.status in (PesapalTransaction.Status.SUBMITTED, PesapalTransaction.Status.PENDING) or (
        txn.status == PesapalTransaction.Status.COMPLETED and not txn.is_processed
    ):
        try:
            return refresh_status(txn, max_age_seconds=POLL_MIN_INTERVAL_SECONDS)
        except Exception:
            logger.exception("refresh_status failed for txn #%s", txn.pk)
    return txn



def _break_out_of_frame(request, url):
    """
    Pesapal loads its return/cancel URLs INSIDE our checkout iframe. Rendering a full
    page there (or redirecting to one) shows 'refused to connect' because the site
    forbids framing. These two endpoints are therefore allowed to be framed by OUR OWN
    pages only, and answer with a tiny page that moves the whole browser window (the
    top frame) to `url`. A plain link is the no-JavaScript fallback.
    """
    safe = escape(url)
    html = (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Redirecting…</title><style>body{font-family:system-ui,sans-serif;display:flex;min-height:100vh;'
        'align-items:center;justify-content:center;color:#475569;font-size:14px;margin:0}a{color:#0f766e;font-weight:600}</style></head>'
        f'<body><p>Returning to SmartPOS… <a href="{safe}" target="_top">Continue</a></p>'
        f'<script>var u={json.dumps(url)};try{{(window.top||window).location.replace(u)}}catch(e){{window.location.replace(u)}}</script>'
        '</body></html>'
    )
    return HttpResponse(html)


def _order_summary(txn):
    """Human-readable 'what you are paying for' shown beside the payment box.
    Purely presentational: any problem yields a minimal summary, never an error."""
    P = PesapalTransaction.Purpose
    rows, benefits = [], []
    try:
        target = _safe_target(txn)
        if txn.purpose == P.SUBSCRIPTION and target is not None:
            rows = [('Business', target.business_name), ('Plan', f"{target.get_tier_display()} · {target.get_plan_display()}")]
            benefits = ['Your account is created automatically', 'Login details are sent to you by SMS']
        elif txn.purpose == P.SUBSCRIPTION_RENEWAL and target is not None:
            tier, plan = _renewal_choice(txn.description, target)
            rows = [('Plan', f"{tier.title()} · {plan.title()}")]
            if target.end_date:
                rows.append(('Current expiry', target.end_date.strftime('%d %b %Y')))
            benefits = ['Access resumes immediately', 'New period is added after your current expiry']
        elif txn.purpose == P.BRANCH_ADDON and target is not None:
            rows = [('Branch', target.pharmacy.name), ('Billing', 'Monthly add-on')]
            benefits = ['The branch unlocks immediately', 'Billed monthly; renew from the Branches page']
        elif txn.purpose == P.BRANCH_ADDON_RENEWAL and target is not None:
            rows = [('Branch', target.pharmacy.name), ('Billing', 'One more month')]
            if target.next_billing_date:
                rows.append(('Paid until', target.next_billing_date.strftime('%d %b %Y')))
            benefits = ['One month is added after your due date', 'No days are lost by paying early']
        elif txn.purpose == P.MARKETPLACE_ORDER and target is not None:
            rows = [('Items', str(len(target.items_snapshot or []))), ('Order total', f'TZS {target.total:,.0f}') ]
            benefits = ['Order confirmation and Ticket ID are issued only after payment is verified', 'Your checkout snapshot is not an official order until payment succeeds']
        elif txn.purpose in _purposes_pkg() and target is not None:
            package = target.package
            rows = [('Package', package.name)]
            credits = getattr(package, 'sms_credits', None) or getattr(package, 'report_sms_credits', None)
            if credits:
                rows.append(('SMS credits', f"{credits:,}"))
            rows.append(('Validity', 'Never expires'))
            benefits = ['Credits are added to your account instantly', 'SMS never expire - use them until they finish', 'Already subscribed? It simply tops up your balance']
    except Exception:
        logger.exception("Could not build the order summary for txn #%s", txn.pk)
    return {'rows': rows, 'benefits': benefits}


def _activation_summary(txn):
    """What the purchase did for the customer, shown on the success screen. Never raises."""
    try:
        if txn.status != PesapalTransaction.Status.COMPLETED or not txn.is_processed:
            return {}
        target = _safe_target(txn)
        P = PesapalTransaction.Purpose
        rows = []
        if txn.purpose == P.SUBSCRIPTION_RENEWAL and target is not None:
            rows = [('Plan', f"{target.get_tier_display()} · {target.get_plan_display()}"),
                    ('Active until', target.end_date.strftime('%d %b %Y'))]
        elif txn.purpose in (P.BRANCH_ADDON, P.BRANCH_ADDON_RENEWAL) and target is not None:
            rows = [('Branch', target.pharmacy.name)]
            if getattr(target, 'next_billing_date', None):
                rows.append(('Active until', target.next_billing_date.strftime('%d %b %Y')))
        elif txn.purpose == P.MARKETPLACE_ORDER and target is not None:
            from marketplace.models import Order
            order = Order.objects.filter(reference=target.reference, payment_status=Order.PaymentStatus.PAID).first()
            if order:
                rows = [('Ticket ID', str(order.reference)), ('Items', str(order.items.count()))]
        elif txn.purpose in _purposes_pkg() and target is not None:
            package = target.package
            rows = [('Package', package.name)]
            credits = getattr(package, 'sms_credits', None) or getattr(package, 'report_sms_credits', None)
            if credits:
                rows.append(('SMS credits added', f"{credits:,}"))
        if rows:
            rows.append(('Amount paid', f"{txn.currency} {txn.amount:,.0f}"))
            if txn.completed_at:
                rows.append(('Paid on', timezone.localtime(txn.completed_at).strftime('%d %b %Y, %H:%M')))
        return {'rows': rows}
    except Exception:
        logger.exception("Could not build the activation summary for txn #%s", txn.pk)
        return {}


def _failure_copy(txn):
    """Plain-language reason for a payment that did not complete."""
    S = PesapalTransaction.Status
    if txn.status not in (S.FAILED, S.CANCELLED, S.INVALID, S.REVERSED):
        return {}
    word = (txn.status_description or '').lower()
    if txn.status == S.REVERSED or 'revers' in word:
        return {'title': 'Payment was reversed',
                'body': 'Your mobile network reversed this payment. Please contact support with the reference below.'}
    if txn.status == S.CANCELLED or 'cancel' in word or 'reject' in word:
        return {'title': 'Request was cancelled',
                'body': 'The request was cancelled or declined on the phone, so nothing was charged. You can try again.'}
    if 'expire' in word or 'timeout' in word or 'timed' in word:
        return {'title': 'Request expired',
                'body': 'The request was not approved in time, so nothing was charged. Send a new request and approve it quickly.'}
    if txn.status == S.INVALID:
        return {'title': 'Payment could not be verified',
                'body': 'We could not confirm this payment. If money left your wallet, contact support with the reference below.'}
    return {'title': 'Payment was not approved',
            'body': 'This usually means the PIN was wrong, the balance was too low, or the request timed out. Nothing was charged. Please try again.'}


def _abliner_context(request, txn, form_error='', form_phone=None, form_network=None, source='form'):
    """Everything the mobile-money part of the checkout needs. Presentational only.

    `source` says which screen the customer pressed a button on ('form' = number form,
    'wait' = the "Check your phone" screen). An error from the waiting screen (for example
    "please wait 38 seconds") is shown THERE, inline, instead of throwing the customer back
    to the number form.
    """
    S = PesapalTransaction.Status
    if txn.gateway != PaymentGatewaySettings.Gateway.ABLINER or txn.status not in (S.PENDING, S.SUBMITTED):
        return None
    attempts = abliner_attempts(txn)
    saved = networks.normalize_msisdn(txn.payer_phone)
    # Reuse the phone collected by the checkout wizard, while keeping it editable.
    hint_phone = networks.local_format(saved) if saved else ''
    shown_phone = form_phone if form_phone is not None else hint_phone
    typed = networks.normalize_msisdn(shown_phone)
    guess = networks.detect_network(typed)
    sent_network = next((n for n in networks.NETWORKS if n['label'] == txn.payment_method), None)
    selected_key = form_network or (sent_network['key'] if sent_network else '') or (guess['key'] if guess else '')
    resend_wait = abliner_resend_wait(txn) if attempts else 0
    wants_change = request.GET.get('change') == '1' and txn.status == S.SUBMITTED and attempts < MAX_ABLINER_ATTEMPTS
    show_form = txn.status == S.PENDING or wants_change or (bool(form_error) and source != 'wait')
    sent_ago = abliner_sent_seconds_ago(txn) if attempts else 0
    return {
        'networks': networks.NETWORKS,
        'selected': selected_key,
        'phone': shown_phone,
        'phone_placeholder': hint_phone or '0712 345 678',
        'error': form_error,
        'show_form': show_form,
        'wait_error': '' if show_form else form_error,
        'sent_ago': sent_ago,
        'attempts': attempts,
        'attempts_left': max(0, MAX_ABLINER_ATTEMPTS - attempts),
        'resend_wait': resend_wait,
        'phone_masked': networks.mask_msisdn(saved),
        'sent_network': sent_network,
        'pin_name': sent_network['pin_name'] if sent_network else 'mobile money PIN',
        'can_switch': txn.status == S.PENDING and attempts == 0 and pesapal_usable(),
    }


def _render_checkout(request, txn, **form_state):
    return render(request, 'payments/checkout.html', {
        'txn': txn,
        'payment_settings': PaymentSettings.load(),
        'pesapal_settings': PesapalSettings.load(),
        'gateway_settings': PaymentGatewaySettings.load(),
        'final_url': _final_destination(txn),
        'manual_url': _manual_fallback_url(txn),
        'is_embeddable': bool(txn.redirect_url),
        'summary': _order_summary(txn),
        'ab': _abliner_context(request, txn, **form_state),
        'done': _activation_summary(txn),
        'fail': _failure_copy(txn),
        'all_networks': networks.NETWORKS,
        'card_available': pesapal_usable(),
    })


@never_cache
@secure_payment_page
def checkout_view(request, merchant_reference):
    """Branded checkout: mobile-money form + waiting screen (ABLiner) or Pesapal's hosted box."""
    txn = get_object_or_404(PesapalTransaction, merchant_reference=merchant_reference)
    txn = _refresh_for_browser(txn)
    return _render_checkout(request, txn)


@require_http_methods(["POST"])
@never_cache
@secure_payment_page
def abliner_pay_view(request, merchant_reference):
    """Customer chose a network and typed a number: send (or re-send) the payment prompt."""
    txn = get_object_or_404(PesapalTransaction, merchant_reference=merchant_reference)
    S = PesapalTransaction.Status
    if txn.gateway != PaymentGatewaySettings.Gateway.ABLINER or txn.status not in (S.PENDING, S.SUBMITTED):
        return redirect('payments:checkout', merchant_reference=txn.merchant_reference)
    phone = (request.POST.get('phone') or '').strip()[:25]
    network_key = (request.POST.get('network') or '').strip()[:20]
    source = 'wait' if request.POST.get('src') == 'wait' else 'form'
    # Abuse guard: a checkout page must not be usable to spray payment prompts at random numbers.
    ip_ok, ip_retry = _public_throttle(request, 'abliner-pay-ip', limit=12, window=600)
    txn_ok, txn_retry = ratelimit.hit('abliner-pay-txn', str(txn.merchant_reference), limit=8, window=600)
    if not (ip_ok and txn_ok):
        wait_for = max(ip_retry, txn_retry)
        return _render_checkout(
            request, txn, form_phone=phone, form_network=network_key, source=source,
            form_error=f'Too many attempts. Please wait {ratelimit.human_wait(wait_for)} before trying again.',
        )
    refused = None
    try:
        with db_transaction.atomic():
            # Lock the row: a double-tap on "Pay" can never send two prompts.
            locked = PesapalTransaction.objects.select_for_update().get(pk=txn.pk)
            try:
                submit_to_abliner(locked, request=request, phone=phone, network_key=network_key)
            except pesapal_client.PesapalError as exc:
                refused = exc          # handled below, OUTSIDE the lock, so the failure log is kept
    except Exception:                                                     # noqa: BLE001
        logger.exception('Unexpected error sending ABLiner prompt for txn #%s', txn.pk)
        txn.refresh_from_db()
        return _render_checkout(request, txn, form_error=GENERIC_FAILURE, form_phone=phone, form_network=network_key, source=source)
    if refused is not None:
        logger.warning('ABLiner prompt refused for txn #%s: %s', txn.pk, refused.technical)
        txn.refresh_from_db()
        return _render_checkout(request, txn, form_error=refused.detail, form_phone=phone, form_network=network_key, source=source)
    return redirect('payments:checkout', merchant_reference=txn.merchant_reference)


@require_http_methods(["POST"])
def abliner_switch_view(request, merchant_reference):
    """'Pay by card instead' — only possible while no mobile-money prompt has been sent."""
    txn = get_object_or_404(PesapalTransaction, merchant_reference=merchant_reference)
    try:
        with db_transaction.atomic():
            locked = PesapalTransaction.objects.select_for_update().get(pk=txn.pk)
            switch_to_pesapal(locked, request=request)
    except pesapal_client.PesapalError as exc:
        logger.warning('Switch to Pesapal refused for txn #%s: %s', txn.pk, exc.technical)
        txn.refresh_from_db()
        return _render_checkout(request, txn, form_error=exc.detail)
    except Exception:                                                     # noqa: BLE001
        logger.exception('Unexpected error switching txn #%s to Pesapal', txn.pk)
        txn.refresh_from_db()
        return _render_checkout(request, txn, form_error=GENERIC_FAILURE)
    return redirect('payments:checkout', merchant_reference=txn.merchant_reference)


@never_cache
@secure_payment_page
def receipt_view(request, merchant_reference):
    """Customer-facing payment receipt. Access is protected by the unguessable UUID reference."""
    txn = get_object_or_404(PesapalTransaction, merchant_reference=merchant_reference)
    if not txn.is_final and txn.order_tracking_id:
        txn = _refresh_for_browser(txn)
    return render(request, 'payments/receipt.html', {
        'txn': txn,
        'payment_settings': PaymentSettings.load(),
        'final_url': _final_destination(txn),
    })


@require_http_methods(["POST"])
def cancel_payment_request_view(request, merchant_reference):
    """Customer explicitly cancels an unpaid payment request from the SmartPOS waiting screen."""
    with db_transaction.atomic():
        txn = get_object_or_404(
            PesapalTransaction.objects.select_for_update(),
            merchant_reference=merchant_reference,
        )
        if txn.status == PesapalTransaction.Status.COMPLETED:
            messages.success(request, "Payment is already confirmed. Your receipt is available.")
        elif txn.status in (PesapalTransaction.Status.PENDING, PesapalTransaction.Status.SUBMITTED):
            txn.status = PesapalTransaction.Status.CANCELLED
            txn.status_description = "Cancelled by customer from SmartPOS checkout."
            txn.error_message = ""
            txn.save(update_fields=["status", "status_description", "error_message", "updated_at"])
            PaymentEvent.objects.create(
                payment=txn, gateway=txn.gateway, event_type="CUSTOMER_CANCELLED",
                provider_status="cancelled", message="Customer cancelled the payment request from SmartPOS.",
            )
            messages.info(request, "Payment request cancelled. No payment was recorded. You can start a new request when ready.")
        else:
            messages.info(request, "This payment request is already closed.")
    txn.refresh_from_db()
    cancel_url = _marketplace_cancel_url(txn) if txn.status == PesapalTransaction.Status.CANCELLED else None
    if cancel_url:
        # Marketplace customers get a dedicated, professional cancellation page (no stray flash message).
        list(messages.get_messages(request))
        return redirect(cancel_url)
    return redirect("payments:pending", merchant_reference=merchant_reference)


@xframe_options_sameorigin
def cancel_view(request, merchant_reference):
    """The customer pressed 'Cancel Request' on Pesapal. Leave the checkout and go back to
    where they started — nothing is marked failed (an unpaid order simply expires), and if
    they actually paid in the meantime the status check still activates it."""
    txn = get_object_or_404(PesapalTransaction, merchant_reference=merchant_reference)
    txn = _refresh_for_browser(txn)
    if txn.status == PesapalTransaction.Status.COMPLETED:
        return _break_out_of_frame(request, reverse('payments:pending', kwargs={'merchant_reference': txn.merchant_reference}))
    cancel_url = _marketplace_cancel_url(txn)
    if cancel_url:
        txn = _mark_cancelled_by_customer(txn, "Customer cancelled the payment on the payment provider's page.")
        if txn.status == PesapalTransaction.Status.COMPLETED:
            return _break_out_of_frame(request, reverse('payments:pending', kwargs={'merchant_reference': txn.merchant_reference}))
        return _break_out_of_frame(request, cancel_url)
    messages.info(request, "Payment cancelled. No money was taken. You can start again whenever you are ready.")
    return _break_out_of_frame(request, _manual_fallback_url(txn))


@never_cache
@secure_payment_page
def pending_view(request, merchant_reference):
    """Professional 'processing payment' page — polls status, then moves on."""
    txn = get_object_or_404(PesapalTransaction, merchant_reference=merchant_reference)
    txn = _refresh_for_browser(txn)
    if txn.status == PesapalTransaction.Status.CANCELLED:
        cancel_url = _marketplace_cancel_url(txn)
        if cancel_url:
            return redirect(cancel_url)
    final_url = _final_destination(txn)
    target = _safe_target(txn)
    if (
        txn.status == PesapalTransaction.Status.COMPLETED
        and txn.is_processed
        and txn.purpose in (PesapalTransaction.Purpose.BRANCH_ADDON, PesapalTransaction.Purpose.BRANCH_ADDON_RENEWAL)
        and target is not None
        and getattr(target, 'status', None) == BranchAddOn.Status.ACTIVE
        and request.user.is_authenticated
        and request.user.business_owner().pk == target.owner_id
    ):
        request.session['active_pharmacy_id'] = target.pharmacy_id
    return render(request, 'payments/pending.html', {
        'txn': txn,
        'final_url': final_url,
        'manual_url': _manual_fallback_url(txn),
    })


@never_cache
@secure_payment_page
def status_check_view(request, merchant_reference):
    """AJAX polling endpoint used by the pending page. Always answers JSON."""
    txn = get_object_or_404(PesapalTransaction, merchant_reference=merchant_reference)
    txn = _refresh_for_browser(txn)
    paid_not_activated = txn.status == PesapalTransaction.Status.COMPLETED and not txn.is_processed
    done = txn.status == PesapalTransaction.Status.COMPLETED and txn.is_processed
    target = _safe_target(txn)
    if (
        done
        and txn.purpose in (PesapalTransaction.Purpose.BRANCH_ADDON, PesapalTransaction.Purpose.BRANCH_ADDON_RENEWAL)
        and target is not None
        and getattr(target, 'status', None) == BranchAddOn.Status.ACTIVE
        and request.user.is_authenticated
        and request.user.business_owner().pk == target.owner_id
    ):
        request.session['active_pharmacy_id'] = target.pharmacy_id
    return JsonResponse({
        'status': txn.status,
        'status_display': txn.get_status_display(),
        # A paid-but-not-yet-activated payment is NOT final: keep the page polling
        # (each poll retries the activation) instead of sending the customer away early.
        'is_final': txn.is_final and not paid_not_activated,
        'activation_pending': paid_not_activated,
        'redirect_url': _final_destination(txn) if done else None,
        'error_message': '' if paid_not_activated else txn.error_message,
        'resend_wait': abliner_resend_wait(txn) if txn.gateway == PaymentGatewaySettings.Gateway.ABLINER else 0,
        'message': ("Payment received — activating your purchase. This usually takes a few seconds."
                    if paid_not_activated else ''),
        'stage': ('done' if done else 'activating' if paid_not_activated
                  else 'failed' if txn.status in (PesapalTransaction.Status.FAILED, PesapalTransaction.Status.CANCELLED,
                                                  PesapalTransaction.Status.INVALID, PesapalTransaction.Status.REVERSED)
                  else 'waiting'),
    })


def _ipn_params(request):
    """Pesapal sends the IPN as GET query params (our registered type) but tolerate POST form/JSON too."""
    params = {}
    for source in (request.GET, request.POST):
        for key in ('OrderTrackingId', 'OrderMerchantReference', 'OrderNotificationType'):
            if source.get(key):
                params.setdefault(key, source.get(key))
    if not params and request.method == 'POST' and request.body:
        try:
            body = json.loads(request.body.decode('utf-8'))
            if isinstance(body, dict):
                for key in ('OrderTrackingId', 'OrderMerchantReference', 'OrderNotificationType'):
                    value = body.get(key) or body.get(key[0].lower() + key[1:])
                    if value:
                        params[key] = str(value)
        except (ValueError, UnicodeDecodeError):
            pass
    return params


def _find_txn(order_tracking_id, merchant_reference):
    txn = None
    if order_tracking_id:
        txn = PesapalTransaction.objects.filter(order_tracking_id=order_tracking_id).first()
    if txn is None and merchant_reference:
        try:
            txn = PesapalTransaction.objects.filter(merchant_reference=merchant_reference).first()
        except (ValueError, ValidationError):     # malformed UUID from an outside caller
            txn = None
    return txn


@csrf_exempt
@require_http_methods(["GET", "POST"])
def ipn_view(request):
    """
    Pesapal's server-to-server notification. Must always answer 200 with the exact
    JSON shape Pesapal expects, whatever we find (or don't find) — otherwise Pesapal
    keeps retrying. We never trust the IPN's content: it only tells us WHICH order to
    re-check; the real status always comes from Pesapal's own API.
    """
    params = _ipn_params(request)
    order_tracking_id = params.get('OrderTrackingId', '')
    merchant_reference = params.get('OrderMerchantReference', '')
    notification_type = params.get('OrderNotificationType') or 'IPNCHANGE'

    try:
        txn = _find_txn(order_tracking_id, merchant_reference)
        if txn is not None:
            PesapalTransaction.objects.filter(pk=txn.pk).update(ipn_count=F('ipn_count') + 1)
            refresh_status(txn)             # no max_age: an IPN must always re-check
        else:
            logger.warning("Pesapal IPN for unknown order: tracking_id=%s ref=%s", order_tracking_id, merchant_reference)
    except Exception:
        logger.exception("IPN processing failed (tracking_id=%s ref=%s)", order_tracking_id, merchant_reference)

    return JsonResponse({
        "orderNotificationType": notification_type,
        "orderTrackingId": order_tracking_id,
        "orderMerchantReference": merchant_reference,
        "status": 200,
    })


@csrf_exempt
@require_http_methods(["POST"])
def abliner_webhook_view(request):
    """
    Verified ABLiner webhook.

    The event is stored first, but is only treated as 'already handled' once it was
    processed successfully (processed_at set). If processing crashes we answer 500, so
    ABLiner's retry is processed again instead of being skipped as a duplicate.
    """
    raw = request.body
    signature = request.headers.get('x-webhook-signature', '')
    timestamp = request.headers.get('x-webhook-timestamp', '')
    if not abliner_client.verify_webhook(raw, signature, timestamp):
        return JsonResponse({'status': 'error', 'message': 'invalid webhook'}, status=401)
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'status': 'error', 'message': 'invalid json'}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({'status': 'error', 'message': 'invalid json'}, status=400)
    event_id = str(request.headers.get('x-webhook-id') or payload.get('id') or '')
    event_type = payload.get('event') or 'unknown'
    data = payload.get('data') or {}
    customer_reference = data.get('customer_reference') or data.get('reference')
    txn = None
    if customer_reference:
        try:
            txn = PesapalTransaction.objects.filter(merchant_reference=customer_reference, gateway='ABLINER').first()
        except (ValueError, ValidationError):
            txn = None
    if txn is None and data.get('id'):
        txn = PesapalTransaction.objects.filter(order_tracking_id=data.get('id'), gateway='ABLINER').first()
    if txn is None:
        logger.warning('ABLiner webhook for unknown payment: event=%s reference=%s', event_type, customer_reference)
        return JsonResponse({'status': 'ok'})

    event = None
    if event_id:
        event = PaymentEvent.objects.filter(gateway='ABLINER', provider_event_id=event_id[:180]).first()
        if event is not None and event.processed_at is not None:
            return JsonResponse({'status': 'ok', 'duplicate': True})
    if event is None:
        event = PaymentEvent.objects.create(
            payment=txn, gateway='ABLINER', event_type=event_type, provider_event_id=event_id[:180],
            provider_status=str(data.get('status') or ''), payload=redact_payload(payload),
        )

    status = str(data.get('status') or '').lower()
    failure_words = ('failed', 'cancelled', 'canceled', 'expired', 'rejected', 'reversed')
    is_failure = event_type in ('transaction.failed', 'transaction.cancelled', 'transaction.canceled',
                                'transaction.expired', 'transaction.rejected', 'transaction.reversed') or any(
        word in status for word in failure_words
    )
    is_cancelled = ('cancel' in status or 'cancel' in str(event_type).lower())
    is_reversed = 'revers' in status or 'revers' in str(event_type).lower()
    is_success = event_type == 'transaction.completed' or status == 'completed'

    # A customer may have been sent more than one prompt. An OLDER prompt expiring must not
    # mark the payment failed while the newest prompt is still waiting for the PIN.
    if is_failure and not is_success and data.get('id') and txn.order_tracking_id \
            and str(data.get('id')) != txn.order_tracking_id and txn.status == PesapalTransaction.Status.SUBMITTED:
        PaymentEvent.objects.filter(pk=event.pk).update(processed_at=timezone.now(), message='Older prompt expired; newer prompt still active.')
        return JsonResponse({'status': 'ok', 'ignored': 'older_prompt'})

    normalized = {
        'merchant_reference': data.get('customer_reference') or data.get('reference'),
        'amount': data.get('amount'), 'currency': data.get('currency'),
        'payment_method': data.get('method'), 'confirmation_code': data.get('provider_reference') or data.get('reference'),
        'status_code': 200, 'payment_status_description': event_type,
        'status': ('COMPLETED' if is_success else
                   'REVERSED' if is_reversed else
                   'CANCELLED' if is_cancelled else
                   'FAILED' if is_failure else 'SUBMITTED'),
    }
    try:
        from .services import _apply_provider_status, _activate
        _apply_provider_status(txn.pk, normalized, gateway='ABLINER', raw=payload)
        txn.refresh_from_db()
        if txn.status == PesapalTransaction.Status.COMPLETED and not txn.is_processed:
            _activate(txn)
    except Exception:
        logger.exception('ABLiner webhook processing failed for txn #%s', txn.pk)
        return JsonResponse({'status': 'error'}, status=500)
    PaymentEvent.objects.filter(pk=event.pk).update(processed_at=timezone.now())
    return JsonResponse({'status': 'ok'})


@xframe_options_sameorigin
def return_view(request):
    """Browser callback after the customer finishes on Pesapal's checkout page."""
    order_tracking_id = request.GET.get('OrderTrackingId')
    merchant_reference = request.GET.get('OrderMerchantReference')
    txn = _find_txn(order_tracking_id, merchant_reference)
    if txn is None:
        messages.error(request, "We could not find that payment. Please contact support if you were charged.")
        return _break_out_of_frame(request, reverse('accounts:login'))
    return _break_out_of_frame(request, reverse('payments:pending', kwargs={'merchant_reference': txn.merchant_reference}))
