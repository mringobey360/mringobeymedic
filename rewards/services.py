from decimal import Decimal
from .models import VIPPolicy

DEFAULT_VIP_POLICY = {
    'enabled': True,
    'qualifying_visits': 4,
    'window_days': 90,
    'discount_type': 'PERCENT',
    'discount_percent': Decimal('5.00'),
    'discount_amount': Decimal('0.00'),
    'minimum_spend': Decimal('0.00'),
}

def get_vip_policy(pharmacy):
    """Return the single branch VIP policy, creating safe defaults for legacy branches."""
    if pharmacy is None:
        return DEFAULT_VIP_POLICY.copy()
    policy, _ = VIPPolicy.objects.get_or_create(pharmacy=pharmacy, defaults=DEFAULT_VIP_POLICY)
    return policy

def customer_vip_metrics(pharmacy, customer_contact, business_date=None):
    """Compute qualification consistently for POS, loyalty pages and alerts."""
    from datetime import timedelta
    from django.utils import timezone
    from django.db.models import Sum, F, DecimalField
    from django.db.models.functions import Coalesce
    from sales.models import Sale, SaleItem
    policy = get_vip_policy(pharmacy)
    if not customer_contact:
        return {'visits': 0, 'spend': Decimal('0.00'), 'vip': False, 'discount_type': getattr(policy, 'discount_type', 'PERCENT'), 'discount_percent': Decimal('0.00'), 'discount_amount': Decimal('0.00'), 'discount_value': Decimal('0.00'), 'discount_label': '0%', 'policy': policy}
    end_date = business_date or timezone.localdate()
    days = int(getattr(policy, 'window_days', 90) or 90)
    start_date = end_date - timedelta(days=max(1, days) - 1)
    valid_sales = Sale.objects.filter(
        pharmacy=pharmacy, customer_contact=customer_contact,
        business_date__gte=start_date, business_date__lte=end_date,
        status__in=[Sale.Status.COMPLETED, Sale.Status.PARTIALLY_RETURNED],
    )
    visits = valid_sales.count()
    spend = SaleItem.objects.filter(sale__in=valid_sales).aggregate(
        total=Coalesce(Sum(F('unit_price') * F('quantity'), output_field=DecimalField(max_digits=14, decimal_places=2)), Decimal('0.00'))
    )['total'] or Decimal('0.00')
    min_spend = Decimal(str(getattr(policy, 'minimum_spend', 0) or 0))
    required_visits = int(getattr(policy, 'qualifying_visits', 4) or 4)
    vip = bool(getattr(policy, 'enabled', True)) and visits >= required_visits and (min_spend <= 0 or spend >= min_spend)
    discount_type = getattr(policy, 'discount_type', 'PERCENT') or 'PERCENT'
    rate = Decimal(str(getattr(policy, 'discount_percent', 5) or 0)) if vip and discount_type == 'PERCENT' else Decimal('0.00')
    amount = Decimal(str(getattr(policy, 'discount_amount', 0) or 0)) if vip and discount_type == 'AMOUNT' else Decimal('0.00')
    value = rate if discount_type == 'PERCENT' else amount
    label = f'{rate.normalize()}%' if discount_type == 'PERCENT' else f'TZS {amount:,.0f} per unit'
    return {'visits': visits, 'spend': spend, 'vip': vip, 'discount_type': discount_type, 'discount_percent': rate, 'discount_amount': amount, 'discount_value': value, 'discount_label': label, 'policy': policy}
