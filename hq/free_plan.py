"""SmartPOS Free Plan (freemium) rules.

A Free Plan account is a Subscription with ``is_trial=True`` and a far-future end date:

* 1 shop, 2 users (owner + 1 employee), FREE_PLAN_MONTHLY_SALES recorded sales per month.
* (New sign-ups get a 30-day trial instead: full Starter features, no sales cap, locks when it ends.)
* Nothing locks. When the month's allowance is used, only NEW sales are paused with a clear
  upgrade message; products, reports, customers and history stay fully usable.
* The allowance renews on the 1st of every month, and upgrading removes every limit.
"""
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone

from .models import FREE_PLAN_MONTHLY_SALES, Subscription


def starter_price():
    """Current Starter monthly price (HQ -> Payment Settings), as a whole number."""
    try:
        from .models import PaymentSettings
        return int(PaymentSettings.load().starter_monthly_price)
    except Exception:
        return 15000


def upgrade_message():
    return (f"Ili kuendelea kuuza, tafadhali lipia akaunti ya Starter ya TSh {starter_price():,}. "
            f"To keep selling, please upgrade to the Starter plan (TSh {starter_price():,}).")


def free_subscription_for_pharmacy(pharmacy):
    """The owner's Free Plan subscription for this branch, or None for paid/unknown accounts."""
    if pharmacy is None:
        return None
    sub = (Subscription.objects.filter(owner_id__in=pharmacy.managers.values_list('pk', flat=True))
           .order_by('-end_date').first())
    # Only the legacy lifetime Free Plan has a monthly sales cap. The one-month trial is uncapped.
    return sub if (sub is not None and sub.is_trial and not sub.is_timed_trial) else None


def sales_this_month(subscription):
    """Real sales recorded this calendar month across the owner's branches.

    Only sales containing at least one real (non-sample) product count, so a new customer can
    practise freely with the sample products.
    """
    from sales.models import Sale
    today = timezone.localdate()
    pharmacy_ids = subscription.owner.managed_pharmacies.values_list('pk', flat=True)
    return (Sale.objects.filter(pharmacy_id__in=pharmacy_ids,
                                business_date__year=today.year, business_date__month=today.month)
            .filter(items__product__is_demo=False).distinct().count())


def usage(subscription):
    """Plain dict for banners/pages: used, limit, remaining, percent, reached."""
    used = sales_this_month(subscription)
    limit = FREE_PLAN_MONTHLY_SALES
    pct = min(100, round(used * 100 / limit)) if limit else 100
    return {'used': used, 'limit': limit, 'remaining': max(0, limit - used), 'percent': pct,
            'reached': used >= limit, 'near': used >= int(limit * 0.8)}


def cached_usage(subscription, ttl=20):
    key = f'freeplan:usage:{subscription.pk}:{timezone.localdate():%Y%m}'
    data = cache.get(key)
    if data is None:
        data = usage(subscription)
        cache.set(key, data, ttl)
    return data


def sales_block_message(pharmacy, incoming=1):
    """None when the sale may go ahead; otherwise the upgrade message to show the cashier."""
    sub = free_subscription_for_pharmacy(pharmacy)
    if sub is None:
        return None
    if sales_this_month(sub) + incoming > FREE_PLAN_MONTHLY_SALES:
        return upgrade_message()
    return None


def account_value(subscription):
    """What the owner has built inside SmartPOS (shown on the Free Plan banner)."""
    from inventory.models import Product
    from sales.models import Sale
    pharmacy_ids = list(subscription.owner.managed_pharmacies.values_list('pk', flat=True))
    return {
        'products': Product.objects.filter(pharmacy_id__in=pharmacy_ids, is_active=True, is_demo=False).count(),
        'sales_total': Sale.objects.filter(pharmacy_id__in=pharmacy_ids).count(),
    }
