"""Packages Center — ONE professional page where a business owner can browse,
filter, compare and pay for every package the platform sells:

* Core subscription plans (Starter / Business / Professional / Enterprise)
* Premium Debt Reminder SMS
* Premium Receipt SMS
* Customer Marketing SMS
* Intelligent Report SMS

Every purchase still flows through the existing, proven paths:
online -> payments.* (Pesapal, instant activation)
manual -> a PENDING purchase row that the platform owner approves in HQ.
"""
from decimal import Decimal, ROUND_CEILING
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import User
from daily_reports.models import DailyReportPackage, DailyReportPurchase, DailyReportSubscription
from notifications.models import Notification
from notifications.utils import notify_many
from sms_integration.services import visible_purchases
from sms_integration.models import (
    SMSDebtPackage, SMSDebtPurchase, SMSDebtSubscription,
    SMSMarketingPackage, SMSMarketingPurchase, SMSMarketingSubscription,
    SMSReceiptPackage, SMSReceiptPurchase, SMSReceiptSubscription,
)

from .models import PaymentSettings, SignupRequest, Subscription
from payments import abliner_client
from payments.models import PaymentGatewaySettings, PesapalSettings
from payments.services import pesapal_usable, resolve_gateway


# Static description of each SMS-style service. Prices/credits always come from
# the database packages so the platform owner stays in full control.
SERVICES = {
    'debt': {
        'label': 'Debt Reminder SMS',
        'tagline': 'Recover unpaid credit faster with automatic reminders to customers who owe you.',
        'package': SMSDebtPackage, 'purchase': SMSDebtPurchase, 'subscription': SMSDebtSubscription,
        'credits_field': 'sms_credits', 'unit': 'debt SMS',
        'pay_url': 'payments:pay_sms_debt', 'hq_url': 'hq:debt_sms_purchases',
        'features': [
            'Send reminders to customers with outstanding balances',
            'Credits are separate from system SMS',
            'SMS never expire - use them until they finish',
        ],
    },
    'receipt': {
        'label': 'Receipt SMS',
        'tagline': 'Customers receive an SMS after every sale with a secure link to download their PDF receipt.',
        'package': SMSReceiptPackage, 'purchase': SMSReceiptPurchase, 'subscription': SMSReceiptSubscription,
        'credits_field': 'sms_credits', 'unit': 'receipt SMS',
        'pay_url': 'payments:pay_sms_receipt', 'hq_url': 'hq:receipt_sms_purchases',
        'features': [
            'Secure link to a professional PDF receipt',
            'Builds trust and gives customers proof of purchase',
            'SMS never expire - use them until they finish',
        ],
    },
    'marketing': {
        'label': 'Marketing SMS',
        'tagline': 'Run promotions and service campaigns to the customers in your Customer Registry.',
        'package': SMSMarketingPackage, 'purchase': SMSMarketingPurchase, 'subscription': SMSMarketingSubscription,
        'credits_field': 'sms_credits', 'unit': 'marketing SMS',
        'pay_url': 'payments:pay_sms_marketing', 'hq_url': 'hq:marketing_sms_purchases',
        'features': [
            'Campaigns sent to your registered customers',
            'SMS never expire - use them until they finish',
            'Failed sends release the reserved credit',
        ],
    },
    'report': {
        'label': 'Intelligent Report SMS',
        'tagline': 'Your daily sales and profit intelligence delivered by SMS, with a secure link to the full report.',
        'package': DailyReportPackage, 'purchase': DailyReportPurchase, 'subscription': DailyReportSubscription,
        'credits_field': 'report_sms_credits', 'unit': 'report SMS',
        'pay_url': 'payments:pay_daily_report', 'hq_url': 'hq:intelligent_report_purchases',
        'features': [
            'Daily sales, profit, expenses and debts summary',
            'Consolidated branch analytics and PDF report',
            'SMS never expire - use them until they finish',
        ],
    },
}

TIER_ORDER = ['STARTER', 'BUSINESS', 'PROFESSIONAL', 'ENTERPRISE']
TIER_INFO = {
    'STARTER': {'branches': '1 branch', 'users': 'Up to 3 users', 'blurb': 'For a single shop getting started.', 'reports': False},
    'BUSINESS': {'branches': 'Up to 2 branches', 'users': 'Up to 10 users', 'blurb': 'For a growing business with a small team.', 'reports': False},
    'PROFESSIONAL': {'branches': 'Up to 5 branches', 'users': 'Up to 25 users', 'blurb': 'For multi-branch operations that need insight.', 'reports': True},
    'ENTERPRISE': {'branches': 'Custom branches', 'users': 'Custom users', 'blurb': 'For large business groups.', 'reports': True},
}


def _owner_only(user):
    if user.is_superuser or not user.is_management_user():
        raise PermissionDenied('Packages are available to business owners.')


def _per_sms(price, credits):
    try:
        return (Decimal(price) / Decimal(credits)).quantize(Decimal('0.1'))
    except Exception:
        return None


def _current_subscription(service, owner):
    """The owner's live entitlement for a service. SMS credits never expire, so only the balance matters."""
    model = service['subscription']
    for sub in model.objects.filter(owner=owner, status='ACTIVE').order_by('-id'):
        if sub.is_current:
            return sub
    return None


def _build_service_catalog(key, owner):
    service = SERVICES[key]
    packages = list(service['package'].objects.filter(is_active=True).filter(Q(custom_owner__isnull=True) | Q(custom_owner=owner)).order_by('sort_order', 'price', 'id'))
    items = []
    for pkg in packages:
        credits = getattr(pkg, service['credits_field'])
        items.append({
            'service': key,
            'id': pkg.pk,
            'code': pkg.code,
            'name': pkg.name,
            'price': pkg.price,
            'credits': credits,
            'days': pkg.duration_days,
            'per_sms': _per_sms(pkg.price, credits),
            'popular': False,
            'best_value': False,
        })
    if items:
        units = [i['per_sms'] for i in items if i['per_sms'] is not None]
        cheapest_unit = min(units, default=None)
        priciest_unit = max(units, default=None)
        if len(items) >= 3:
            items[len(items) // 2]['popular'] = True
        if cheapest_unit is not None and len(items) > 1:
            for i in items:
                if i['per_sms'] == cheapest_unit:
                    i['best_value'] = True
        for i in items:
            i['per_day'] = round(i['credits'] / i['days']) if i['days'] else i['credits']
            if priciest_unit and i['per_sms'] is not None and priciest_unit > 0:
                saving = int(round((1 - (i['per_sms'] / priciest_unit)) * 100))
                i['saving_pct'] = saving if saving > 0 else 0
            else:
                i['saving_pct'] = 0
    current = _current_subscription(service, owner)
    pending = visible_purchases(service['purchase'].objects.filter(owner=owner, status='PENDING')).select_related('package').first()
    current_total = getattr(current, service['credits_field'], 0) if current else 0
    no_expiry = True   # SMS credits never expire
    days_left = None
    low = bool(current) and (
        (current_total and current.credits_remaining / current_total <= 0.2) or (days_left is not None and days_left <= 7)
    )
    return {
        'key': key,
        'label': service['label'],
        'tagline': service['tagline'],
        'unit': service['unit'],
        'features': service['features'],
        'pay_url': reverse(service['pay_url']),
        'items': items,
        'current': current,
        'current_total': current_total,
        'days_left': days_left,
        'no_expiry': no_expiry,
        'low': low,
        'starting_price': min((i['price'] for i in items), default=None),
        'pending': pending,
        'rate_tiers': [{'credits': c, 'price': int(p)} for c, p in _rate_tiers(service)],
    }


def _build_core(owner, payment_settings):
    try:
        subscription = owner.subscription
    except Subscription.DoesNotExist:
        return None
    from . import referrals
    referral_percent = referrals.available_percent(owner) if payment_settings.referral_enabled else 0
    current_idx = TIER_ORDER.index(subscription.tier) if subscription.tier in TIER_ORDER else 0
    tiers = []
    for idx, tier in enumerate(TIER_ORDER):
        monthly_full = payment_settings.price_for(SignupRequest.Plan.MONTHLY, tier)
        yearly_full = payment_settings.price_for(SignupRequest.Plan.YEARLY, tier)
        saving = (monthly_full * 12) - yearly_full
        monthly = referrals.discounted_price(owner, monthly_full)[0] if referral_percent else monthly_full
        yearly = referrals.discounted_price(owner, yearly_full)[0] if referral_percent else yearly_full
        if subscription.is_trial:
            action = 'Upgrade to this plan'   # a trial is never "renewed": every paid plan is an upgrade
        elif tier == subscription.tier:
            action = 'Renew plan'
        elif idx > current_idx:
            action = 'Upgrade'
        else:
            action = 'Switch to this plan'
        info = TIER_INFO[tier]
        tiers.append({
            'tier': tier,
            'label': dict(SignupRequest.Tier.choices)[tier],
            'monthly': monthly,
            'yearly': yearly,
            'monthly_full': monthly_full,
            'yearly_full': yearly_full,
            'saving': saving if saving > 0 else None,
            'is_current': tier == subscription.tier and not subscription.is_trial,
            'popular': tier == 'BUSINESS',
            'action': action,
            **info,
        })
    return {'subscription': subscription, 'tiers': tiers, 'pay_url': reverse('payments:pay_renewal'),
            'referral_percent': referral_percent}


def _history(owner):
    rows = []
    for key, service in SERVICES.items():
        qs = visible_purchases(service['purchase'].objects.filter(owner=owner)).select_related('package').order_by('-requested_at')[:40]
        for p in qs:
            rows.append({
                'date': p.requested_at, 'service': service['label'], 'package': p.package.name,
                'amount': p.amount, 'reference': p.payment_reference,
                'status': p.status, 'status_display': p.get_status_display(),
            })
    rows.sort(key=lambda r: r['date'], reverse=True)
    return rows


def _notify_hq(owner, title, body, url_name):
    superadmins = User.objects.filter(is_superuser=True, is_active=True)
    notify_many(
        superadmins, title, body,
        category=Notification.Category.SYSTEM, level=Notification.Level.INFO,
        link_url=reverse(url_name),
    )


CUSTOM_MIN_QTY, CUSTOM_MAX_QTY = 20, 1000000


def _rate_tiers(service):
    """Published (credits, price) steps of a service, smallest first. Drives the volume rate."""
    credits_field = service['credits_field']
    rows = []
    for pkg in service['package'].objects.filter(is_active=True, custom_owner__isnull=True):
        credits = getattr(pkg, credits_field, 0)
        if credits and pkg.price > 0:
            rows.append((int(credits), Decimal(pkg.price)))
    rows.sort()
    return rows


def custom_price(service, quantity):
    """Price in whole TZS for an exact quantity, or None when no published packages exist.

    Fair volume pricing: the rate is the best per-SMS rate among the published packages that the
    quantity is large enough to reach. A quantity below the smallest package pays that package's rate,
    so small custom orders never get the bulk price, and the more you buy the less each SMS costs.
    """
    tiers = _rate_tiers(service)
    if not tiers:
        return None
    reachable = [price / credits for credits, price in tiers if credits <= quantity]
    rate = min(reachable) if reachable else tiers[0][1] / tiers[0][0]
    return (Decimal(quantity) * rate).quantize(Decimal('1'), rounding=ROUND_CEILING)


def _get_or_create_custom_package(key, owner, quantity):
    """The owner's private package for an exact quantity. Returns (package, error_message)."""
    service = SERVICES[key]
    if quantity < CUSTOM_MIN_QTY or quantity > CUSTOM_MAX_QTY:
        return None, f'Choose a quantity between {CUSTOM_MIN_QTY} and {CUSTOM_MAX_QTY:,} SMS.'
    price = custom_price(service, quantity)
    if price is None:
        return None, 'Custom SMS pricing is temporarily unavailable. Please contact support.'
    model, field = service['package'], service['credits_field']
    existing = model.objects.filter(custom_owner=owner, is_custom=True, is_active=True, price=price, **{field: quantity}).first()
    if existing:
        return existing, None
    # Keep the owner's list clean: retire earlier private quotes that were never paid for.
    for old in model.objects.filter(custom_owner=owner, is_custom=True, is_active=True):
        if not old.purchases.exists():
            old.is_active = False
            old.save(update_fields=['is_active'])
    values = {'code': ('C' + uuid.uuid4().hex[:25]).upper(), 'name': f'Custom quantity \u2014 {quantity:,} SMS',
              'price': price, 'duration_days': 30, 'is_active': True, 'sort_order': 9999,
              'custom_owner': owner, 'is_custom': True, field: quantity}
    return model.objects.create(**values), None


@login_required
@require_http_methods(['POST'])
def custom_quote_view(request):
    """JSON: price an exact SMS quantity and return its private package, ready to pay for."""
    _owner_only(request.user)
    key = request.POST.get('service', '').strip()
    if key not in SERVICES:
        return JsonResponse({'ok': False, 'error': 'Unknown service.'}, status=400)
    try:
        quantity = int(request.POST.get('quantity', '0'))
    except (TypeError, ValueError):
        quantity = 0
    service = SERVICES[key]
    package, error = _get_or_create_custom_package(key, request.user.business_owner(), quantity)
    if error:
        return JsonResponse({'ok': False, 'error': error}, status=400)
    credits = getattr(package, service['credits_field'])
    return JsonResponse({
        'ok': True, 'package_id': package.pk, 'name': package.name, 'price': int(package.price),
        'credits': credits, 'per_sms': str(_per_sms(package.price, credits)), 'pay_url': reverse(service['pay_url']),
    })


def _handle_manual_payment(request):
    """Manual mobile-money submission for any package type. Returns None; always redirects."""
    # No-JavaScript fallback for the custom-quantity calculator: create the private package, then show it.
    custom_service = request.POST.get('custom_sms_service', '').strip()
    if custom_service in SERVICES:
        try:
            quantity = int(request.POST.get('custom_sms_quantity', '0'))
        except (TypeError, ValueError):
            quantity = 0
        package, error = _get_or_create_custom_package(custom_service, request.user.business_owner(), quantity)
        if error:
            messages.error(request, error)
            return redirect('hq:packages_center')
        credits = getattr(package, SERVICES[custom_service]['credits_field'])
        messages.success(request, f'Your quote is ready: {credits:,} SMS for TZS {package.price:,.0f}. Select the private custom package below to pay securely. The amount is fixed at checkout.')
        return redirect(reverse('hq:packages_center') + f'#svc-{custom_service}')

    key = request.POST.get('service', '')
    reference = request.POST.get('payment_reference', '').strip()
    note = request.POST.get('customer_note', '').strip()
    back = redirect('hq:packages_center')

    # ---- Core subscription (renewal / upgrade) ----
    if key == 'core':
        try:
            subscription = request.user.business_owner().subscription
        except Subscription.DoesNotExist:
            messages.error(request, 'No subscription was found for this account. Please contact support.')
            return back
        plan = request.POST.get('plan', subscription.plan)
        tier = request.POST.get('tier', subscription.tier)
        if plan not in (Subscription.Plan.MONTHLY, Subscription.Plan.YEARLY) or tier not in SignupRequest.Tier.values:
            messages.error(request, 'Please select a valid plan.')
            return back
        if not reference:
            messages.error(request, 'Enter the mobile-money transaction reference after paying.')
            return back
        now = timezone.localtime(timezone.now()).strftime('%Y-%m-%d %H:%M')
        line = (f"[RENEWAL REQUEST] {now} | Tier: {tier} | Plan: {plan} | "
                f"Payment reference: {reference} | Status: PENDING REVIEW")
        signup = subscription.signup_request
        if signup is not None:
            signup.notes = (signup.notes + "\n" if signup.notes else "") + line
            signup.save(update_fields=['notes'])
        _notify_hq(
            request.user, 'Subscription payment submitted',
            f'{request.user.username} submitted a {tier.title()} / {plan.title()} subscription payment. Reference: {reference}',
            'hq:subscription_list',
        )
        messages.success(request, 'Subscription payment submitted. Your plan will be updated after the platform owner confirms the payment.')
        return back

    # ---- SMS / report packages ----
    service = SERVICES.get(key)
    if service is None:
        messages.error(request, 'Please choose a valid package.')
        return back
    package = get_object_or_404(service['package'], pk=request.POST.get('package_id'), is_active=True)
    if package.custom_owner_id and package.custom_owner_id != request.user.business_owner().pk:
        raise PermissionDenied('This custom SMS quote belongs to another business.')
    if not reference:
        messages.error(request, 'Enter the mobile-money transaction reference after paying.')
        return back
    if visible_purchases(service['purchase'].objects.filter(owner=request.user.business_owner(), status='PENDING')).exists():
        messages.warning(request, f"You already have a {service['label']} payment waiting for verification.")
        return back
    service['purchase'].objects.create(
        owner=request.user.business_owner(), package=package, amount=package.price,
        payment_reference=reference, customer_note=note,
    )
    credits = getattr(package, service['credits_field'])
    _notify_hq(
        request.user, f"{service['label']} payment",
        f"{request.user.username} submitted TZS {package.price:,.0f} for {credits:,} {service['unit']}. Reference: {reference}",
        service['hq_url'],
    )
    messages.success(request, f"{package.name} request submitted. It activates after the platform owner verifies your payment.")
    return back


@login_required
@require_http_methods(['GET', 'POST'])
def packages_center_view(request):
    _owner_only(request.user)
    if request.method == 'POST':
        return _handle_manual_payment(request)

    payment_settings = PaymentSettings.load()
    gateway_settings = PaymentGatewaySettings.load()
    pesapal = PesapalSettings.load()
    abliner_ready = abliner_client.is_usable()
    # Match the provider the transaction service will select for a NEW payment.
    selected_gateway = resolve_gateway() if gateway_settings.is_enabled else None
    online_payment_ready = bool(
        gateway_settings.is_enabled and (
            (selected_gateway == PaymentGatewaySettings.Gateway.ABLINER and abliner_ready)
            or (selected_gateway == PaymentGatewaySettings.Gateway.PESAPAL and pesapal_usable())
        )
    )
    online_gateway_label = (
        'ABLiner' if selected_gateway == PaymentGatewaySettings.Gateway.ABLINER
        else 'PesaPal' if selected_gateway == PaymentGatewaySettings.Gateway.PESAPAL
        else ''
    )

    core = _build_core(request.user, payment_settings)
    sections = [_build_service_catalog(key, request.user) for key in SERVICES]

    history_page = Paginator(_history(request.user), 8).get_page(request.GET.get('history_page'))

    active_services = [
        {
            'key': s['key'], 'label': s['label'], 'unit': s['unit'],
            'remaining': s['current'].credits_remaining, 'total': s['current_total'],
            'end_date': s['current'].end_date, 'days_left': s['days_left'], 'no_expiry': s['no_expiry'], 'low': s['low'],
            'percent': int(100 * s['current'].credits_remaining / s['current_total']) if s['current_total'] else 0,
        }
        for s in sections if s['current']
    ]
    low_services = [a for a in active_services if a['low']]
    package_total = sum(len(s['items']) for s in sections) + (len(core['tiers']) if core else 0)
    starting_prices = [s['starting_price'] for s in sections if s['starting_price'] is not None]
    hero_stats = {
        'packages': package_total,
        'services': len(sections) + (1 if core else 0),
        'active': len(active_services) + (1 if core else 0),
        'from_price': min(starting_prices) if starting_prices else None,
    }

    filter_tabs = [{'key': 'all', 'label': 'All packages', 'count': sum(len(s['items']) for s in sections) + (len(core['tiers']) if core else 0)}]
    if core:
        filter_tabs.append({'key': 'core', 'label': 'Core plans', 'count': len(core['tiers'])})
    filter_tabs += [{'key': s['key'], 'label': s['label'], 'count': len(s['items'])} for s in sections]

    core_receipt = getattr(core['subscription'], 'payment_receipt', None) if core else None

    return render(request, 'hq/packages_center.html', {
        'core': core,
        'core_receipt': core_receipt,
        'sections': sections,
        'filter_tabs': filter_tabs,
        'active_services': active_services,
        'low_services': low_services,
        'hero_stats': hero_stats,
        'branch_addon_price': payment_settings.branch_addon_price,
        'history_page': history_page,
        'payment_settings': payment_settings,
        # Keep the legacy flag for other template integrations; the package checkout
        # now uses the actual selected provider rather than PesaPal readiness alone.
        'pesapal_ready': pesapal.is_enabled and pesapal.is_configured,
        'online_payment_ready': online_payment_ready,
        'online_gateway_label': online_gateway_label,
        'online_gateway_is_abliner': selected_gateway == PaymentGatewaySettings.Gateway.ABLINER,
    })
