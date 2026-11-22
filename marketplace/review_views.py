"""Verified-buyer product ratings and reviews."""
import logging
import uuid

from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import CharField, Count
from django.db.models.functions import Cast
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from .models import Order, OrderItem, ProductReview
from .views import _phone_key, _public_marketplace_products, cart_data

logger = logging.getLogger(__name__)
PAGE_SIZE = 8
MAX_COMMENT = 1000
HOURLY_LIMIT = 12


def review_context(product, page=1):
    qs = ProductReview.objects.filter(product=product, is_visible=True).order_by('-created_at')
    counts = {r['rating']: r['c'] for r in qs.values('rating').annotate(c=Count('id'))}
    total = sum(counts.values())
    dist = [{'star': s, 'count': counts.get(s, 0), 'pct': round(counts.get(s, 0) * 100 / total) if total else 0} for s in (5, 4, 3, 2, 1)]
    pg = Paginator(qs, PAGE_SIZE).get_page(page)
    return {'product': product, 'reviews': pg.object_list, 'has_next': pg.has_next(), 'next_page': pg.number + 1,
            'rating_dist': dist, 'rating_total': total}


def _find_order(reference, phone):
    """Same lookup rules as Track Order: full UUID, SP-XXXXXXXX ticket, or provider reference + checkout phone."""
    ref = (reference or '').strip()
    key = _phone_key(phone)
    if not ref or not key:
        return None
    base = Order.objects.select_related('seller_store')
    found = []
    try:
        found += list(base.filter(reference=uuid.UUID(ref))[:1])
    except (ValueError, AttributeError, TypeError):
        pass
    found += list(base.filter(payment_reference__iexact=ref).order_by('-created_at')[:20])
    compact = ''.join(c for c in ref.upper() if c.isalnum())
    if compact.startswith('SP') and len(compact) == 10 and all(c in '0123456789ABCDEF' for c in compact[2:]):
        found += list(base.annotate(_rt=Cast('reference', output_field=CharField()))
                      .filter(_rt__istartswith=compact[2:]).order_by('-created_at')[:20])
    for o in found:
        if _phone_key(o.phone) == key:
            return o
    return None


def _client_ip(request):
    fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return (fwd.split(',')[0].strip() if fwd else request.META.get('REMOTE_ADDR', '')) or 'unknown'


def _fail(message, status=400, **extra):
    return JsonResponse(dict(ok=False, message=message, **extra), status=status)


@require_POST
def submit_review(request, slug):
    product = get_object_or_404(_public_marketplace_products(), slug=slug)
    bucket = f'mk-review:{_client_ip(request)}'
    tries = cache.get(bucket, 0)
    if tries >= HOURLY_LIMIT:
        return _fail('Too many attempts. Please try again in a little while.', 429)
    cache.set(bucket, tries + 1, 3600)

    try:
        rating = int(request.POST.get('rating', ''))
    except (TypeError, ValueError):
        rating = 0
    if rating < 1 or rating > 5:
        return _fail('Please choose a star rating from 1 to 5.', field='rating')
    comment = (request.POST.get('comment') or '').strip()[:MAX_COMMENT]

    order = _find_order(request.POST.get('reference'), request.POST.get('phone'))
    generic = 'We could not verify this purchase. Check the Ticket ID and the phone number used at checkout.'
    if not order:
        return _fail(generic, field='reference')
    if order.payment_status != Order.PaymentStatus.PAID or order.status == Order.Status.CANCELLED:
        return _fail('Only paid orders can be rated. Once your payment is confirmed you can rate your purchase.', field='reference')
    if not OrderItem.objects.filter(order=order, product=product).exists():
        return _fail('This order does not include this product, so it cannot be rated here.', field='reference')

    review, created = ProductReview.objects.update_or_create(
        product=product, order=order,
        defaults={'rating': rating, 'comment': comment, 'customer_name': order.customer_name[:150], 'is_visible': True})
    product.refresh_from_db(fields=['rating_avg', 'rating_count'])
    ctx = review_context(product)
    ctx['request'] = request
    ctx['just_posted'] = review.pk
    return JsonResponse({
        'ok': True,
        'created': created,
        'message': 'Thank you! Your review has been published.' if created else 'Your review has been updated.',
        'rating_avg': str(product.rating_avg), 'rating_count': product.rating_count,
        'block': render_to_string('marketplace/_reviews_block.html', ctx, request=request),
        'line': render_to_string('marketplace/_rate_line.html', {'product': product}, request=request),
    })


@require_GET
def reviews_page(request, slug):
    product = get_object_or_404(_public_marketplace_products(), slug=slug)
    try:
        page = max(1, int(request.GET.get('page', '1')))
    except ValueError:
        raise Http404
    ctx = review_context(product, page)
    resp = JsonResponse({'html': render_to_string('marketplace/_review_items.html', ctx, request=request), 'has_next': ctx['has_next'], 'next_page': ctx['next_page']})
    return resp
