"""Template context for the online-seller workspace.

Inside the Seller Centre (and for accounts that exist only to sell online or list property) the
sidebar shows seller tools only: no POS, stock-room, staff or finance sections.
"""
SELLER_ONLY_TYPES = ('ONLINE_SELLER', 'REAL_ESTATE')


def seller_workspace(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated or user.is_superuser:
        return {'in_seller_centre': False}
    match = getattr(request, 'resolver_match', None)
    name = (getattr(match, 'url_name', '') or '')
    on_seller_page = bool(match and match.namespace == 'marketplace' and name.startswith('seller_')
                          and name != 'seller_store_public')
    pharmacy = getattr(request, 'pharmacy', None)
    seller_only = bool(pharmacy and getattr(pharmacy, 'business_type', '') in SELLER_ONLY_TYPES)
    if not (on_seller_page or seller_only):
        return {'in_seller_centre': False}
    ctx = {'in_seller_centre': True, 'seller_has_pos': not seller_only, 'seller_nav_store': None, 'seller_new_orders': 0}
    try:
        from .models import Order
        from .views import _primary_store
        store = _primary_store(user)
        ctx['seller_nav_store'] = store
        if store:
            ctx['seller_new_orders'] = Order.objects.filter(seller_store=store, status=Order.Status.NEW).count()
    except Exception:
        pass  # the sidebar must never break a page
    return ctx
