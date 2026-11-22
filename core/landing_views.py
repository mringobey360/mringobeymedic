"""Public marketing landing page for SmartPOS.

URL: ``/`` (see pharmacy_system/urls.py).

* Signed-in users get the normal dashboard, exactly as before (the dashboard
  view is called directly, so the URL stays ``/`` and no redirect loop exists).
* Visitors who are not signed in see the landing page.
* The installed PWA opens ``/?source=pwa`` (see static/manifest.json); staff
  using the app are sent straight to the login screen, never to marketing.

Prices come from HQ -> Payment Settings, so changing a price in Django Admin
updates the landing page automatically. Nothing is hardcoded here except the
fallback used if the database is unreachable.
"""
from decimal import Decimal

from django.conf import settings
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .seo import landing_jsonld

LANDING_DESCRIPTION = (
    'SmartPOS is a modern point-of-sale and business management system for shops in Tanzania: '
    'barcode selling, stock and expiry tracking, multi-branch control, cash management and SMS receipts. Start free.'
)

_FALLBACK = {
    'STARTER': (Decimal('15000'), Decimal('150000')),
    'BUSINESS': (Decimal('30000'), Decimal('300000')),
    'PROFESSIONAL': (Decimal('50000'), Decimal('500000')),
    'ENTERPRISE': (Decimal('100000'), Decimal('1000000')),
}

# Plan limits mirror hq.packages_views.TIER_INFO.
_TIERS = [
    {
        'code': 'STARTER', 'name': 'Starter', 'branches': '1 branch', 'users': 'Up to 3 users',
        'blurb': 'For a single shop getting started.', 'reports': False, 'popular': False,
    },
    {
        'code': 'BUSINESS', 'name': 'Business', 'branches': 'Up to 2 branches', 'users': 'Up to 10 users',
        'blurb': 'For a growing business with a small team.', 'reports': False, 'popular': True,
    },
    {
        'code': 'PROFESSIONAL', 'name': 'Professional', 'branches': 'Up to 5 branches', 'users': 'Up to 25 users',
        'blurb': 'For multi-branch operations that need insight.', 'reports': True, 'popular': False,
    },
    {
        'code': 'ENTERPRISE', 'name': 'Enterprise', 'branches': 'Custom branches', 'users': 'Custom users',
        'blurb': 'For large business groups.', 'reports': True, 'popular': False,
    },
]


def _pricing():
    try:
        from hq.models import PaymentSettings
        ps = PaymentSettings.load()
        prices = {
            'STARTER': (ps.starter_monthly_price, ps.starter_yearly_price),
            'BUSINESS': (ps.business_monthly_price, ps.business_yearly_price),
            'PROFESSIONAL': (ps.professional_monthly_price, ps.professional_yearly_price),
            'ENTERPRISE': (ps.enterprise_monthly_price, ps.enterprise_yearly_price),
        }
        addon = ps.branch_addon_price
    except Exception:  # never let a pricing lookup take the public page down
        prices, addon = _FALLBACK, Decimal('10000')

    tiers = []
    for tier in _TIERS:
        monthly, yearly = prices[tier['code']]
        saving = (monthly * 12) - yearly
        tiers.append({**tier, 'monthly': monthly, 'yearly': yearly,
                      'saving': saving if saving > 0 else None})
    return tiers, addon


_FALLBACK_BUSINESS_TYPES = [
    'Pharmacy / Drug store', 'Retail shop / Duka', 'Wholesale / Distribution', 'Supermarket / Grocery',
    'Restaurant / Cafe', 'Hardware / Building materials', 'Electronics / Phones', 'Clothing / Boutique',
    'Salon / Beauty', 'Agro-vet / Farm inputs',
]


def _business_types():
    """Business types offered on the registration form (kept in sync automatically)."""
    try:
        from hq.models import SignupRequest
        labels = [str(label) for value, label in SignupRequest.BusinessType.choices if value != 'OTHER']
    except Exception:
        labels = []
    return labels or _FALLBACK_BUSINESS_TYPES


def _whatsapp_digits():
    raw = getattr(settings, 'SUPPORT_WHATSAPP_PHONE', '') or ''
    return ''.join(ch for ch in raw if ch.isdigit())


def _reviews():
    """Approved testimonials only. Never lets a failure here take the public page down."""
    try:
        from testimonials import services
        from testimonials.models import TestimonialSettings
        if not TestimonialSettings.load().show_on_landing:
            return [], {}
        return services.public_reviews(), services.public_stats()
    except Exception:
        return [], {}


@never_cache
@require_GET
def landing_view(request):
    if request.user.is_authenticated:
        from dashboard.views import dashboard_home_view
        return dashboard_home_view(request)

    if request.GET.get('source') == 'pwa':
        return redirect('accounts:login')

    tiers, branch_addon = _pricing()
    try:
        from hq.models import TRIAL_DAYS
    except Exception:
        TRIAL_DAYS = 30

    reviews, review_stats = _reviews()
    try:
        from hq.models import FREE_PLAN_MAX_USERS, FREE_PLAN_MONTHLY_SALES, TRIAL_SMS_CREDITS
        free_users, free_sales, trial_sms = FREE_PLAN_MAX_USERS, FREE_PLAN_MONTHLY_SALES, TRIAL_SMS_CREDITS
    except Exception:
        free_users, free_sales, trial_sms = 2, 100, 20
    starter_price = int(tiers[0]['monthly'])
    try:
        from hq.models import SETUP_EXTRA_BLOCK, SETUP_EXTRA_FEE, SETUP_INCLUDED_PRODUCTS, SETUP_MAX_STANDARD
    except Exception:
        SETUP_INCLUDED_PRODUCTS, SETUP_EXTRA_BLOCK, SETUP_EXTRA_FEE, SETUP_MAX_STANDARD = 300, 250, 20000, 1000
    setup_price = starter_price * 3

    return render(request, 'public/landing.html', {
        'reviews': reviews,
        'review_stats': review_stats,
        'tiers': tiers,
        'branch_addon_price': branch_addon,
        'trial_days': TRIAL_DAYS,
        'free_users': free_users, 'free_sales': free_sales, 'trial_sms': trial_sms,
        'starter_price': starter_price, 'setup_price': setup_price,
        'setup_included': SETUP_INCLUDED_PRODUCTS, 'setup_block': SETUP_EXTRA_BLOCK, 'setup_fee': SETUP_EXTRA_FEE,
        'setup_max': SETUP_MAX_STANDARD, 'setup_mid_from': setup_price + SETUP_EXTRA_FEE,
        'whatsapp_digits': _whatsapp_digits(),
        'business_types': _business_types(),
        'seo_jsonld': landing_jsonld(tiers, free_users, free_sales, TRIAL_DAYS, LANDING_DESCRIPTION),
    })
