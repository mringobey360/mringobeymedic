from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test, login_required
from django import forms
from django.db.models import Count
from django.db import transaction, IntegrityError
from django.db.models import Q, Count, Sum, Exists, OuterRef, F, Prefetch
from django.db.models.deletion import ProtectedError
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from datetime import timedelta
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_http_methods
from django.views.decorators.csrf import csrf_exempt
import json
import logging
import os
from .digital import (DIGITAL_ACCEPT, PREVIEW_ACCEPT, WHATS_INSIDE_MAX_ITEMS, WHATS_INSIDE_MAX_LEN,
                      parse_whats_inside, validate_digital_upload, validate_preview_upload)
from payments import abliner_client, networks
from payments.models import PesapalTransaction
from payments.services import create_transaction

logger = logging.getLogger(__name__)
from .models import Inquiry, ProductImage, Category, Product, Order, OrderItem, OrderStatusEvent, PaymentAttempt, SellerStore, MerchantPaymentProfile, OnlineServiceSubscription, OnlineServiceRequest
from . import orders as order_flow
from .movies import (MOVIE_FILE_EXT, MOVIE_MAX_MINUTES, QUALITY_CHOICES, TRAILER_ACCEPT,
                     clean_year, split_genres, validate_trailer_upload)
from .refs import short_reference
from .social import PLATFORMS as SOCIAL_PLATFORMS, form_value, normalize_social, parse_social_post, social_form_fields

def _public_marketplace_products():
    """Published legacy catalogue items plus products from currently entitled seller stores.

    Uses an EXISTS sub-query instead of joining subscriptions + DISTINCT: no duplicate rows, a much
    cheaper COUNT for pagination, and the same visibility rules.
    """
    entitled = OnlineServiceSubscription.objects.filter(
        store_id=OuterRef('seller_store_id'),
        service=OnlineServiceSubscription.Service.ONLINE_SELLING,
        status=OnlineServiceSubscription.Status.ACTIVE,
        period_end__gt=timezone.now(),
    )
    return Product.objects.filter(is_active=True, archived_at__isnull=True, category__is_active=True).filter(
        Q(seller_store__isnull=True) | Q(seller_store__is_published=True, seller_store__pharmacy__name__iexact=PLATFORM_BUSINESS_NAME) | (Q(seller_store__is_published=True) & Exists(entitled))
    )


# A product can be bought when it is physical with stock on the shelf, or digital with its file attached.
AVAILABLE_Q = (Q(kind=Product.Kind.PHYSICAL, stock__gt=0) | (Q(kind=Product.Kind.DIGITAL) & ~Q(digital_file='')))

# List pages never show the long description, so it is not loaded from the database.
_LIST_DEFER = ('description',)


PLATFORM_BUSINESS_NAME = 'SmartPOS'


def _platform_pharmacy():
    """The platform owner's own business ("SmartPOS"). Created on first use so the system admin can sell on the marketplace."""
    from pharmacies.models import Pharmacy
    obj = Pharmacy.objects.filter(name__iexact=PLATFORM_BUSINESS_NAME).first()
    if obj is None:
        obj = Pharmacy.objects.create(name=PLATFORM_BUSINESS_NAME, is_active=True)
    return obj


def _is_platform_store(store):
    return bool(store and store.pharmacy_id and store.pharmacy.name.strip().lower() == PLATFORM_BUSINESS_NAME.lower())


def _online_selling_active(store):
    if _is_platform_store(store):
        return True   # the platform's own shop never needs an Online Selling subscription
    return store.service_subscriptions.filter(
        service=OnlineServiceSubscription.Service.ONLINE_SELLING,
        status=OnlineServiceSubscription.Status.ACTIVE,
        period_end__gt=timezone.now(),
    ).exists()


def _require_active_seller_store(request, store):
    if not _online_selling_active(store):
        messages.warning(request, 'Online Selling is not active for this store. Complete payment or wait for an authorized admin approval before managing products.')
        return False
    return True


def _trending_ids(period):
    """Best-selling product ids for the period, cached for a few minutes (it is the heaviest query)."""
    def build():
        since=timezone.now()-timedelta(days={'today':1,'week':7,'month':30}[period])
        ids=list(_public_marketplace_products().filter(orderitem__order__created_at__gte=since)
                 .annotate(sold=Sum('orderitem__quantity')).order_by('-sold','-created_at')
                 .values_list('pk',flat=True)[:10])
        if not ids:
            ids=list(_public_marketplace_products().order_by('-featured','-created_at').values_list('pk',flat=True)[:10])
        return ids
    return cache.get_or_set(f'marketplace:trending:{period}', build, 300)


def _public_category_chips():
    """Category chips for the shared marketplace.

    Categories are private to each shop, so the marketplace only shows a chip per distinct
    name that currently has a publicly visible product (cached for a few minutes).
    """
    def build():
        rows = (Category.objects.filter(is_active=True)
                .filter(Exists(_public_marketplace_products().filter(category=OuterRef('pk'))))
                .order_by('name').values('name', 'slug'))
        seen = {}
        for row in rows:
            seen.setdefault(row['name'].strip().lower(), row)
        return list(seen.values())
    return cache.get_or_set('marketplace:category-chips', build, 300)


def _apply_search(products, q):
    """Word-by-word search: every word must match the name, description, category, shop or seller.

    Shared by the catalogue page and the live-suggestion endpoint so what the customer sees in the
    dropdown is exactly what the full results page shows.
    """
    for word in q.split()[:6]:
        products = products.filter(
            Q(name__icontains=word) | Q(description__icontains=word) | Q(category__name__icontains=word)
            | Q(seller_name__icontains=word) | Q(seller_store__public_name__icontains=word)
        )
    return products


def search_suggest(request):
    """Live search-as-you-type. Always answers JSON; never raises to the browser."""
    q = request.GET.get('q', '').strip()[:100]
    chips = [{'name': c['name'], 'slug': c['slug']} for c in _public_category_chips()[:6]]
    if len(q) < 2:
        return JsonResponse({'q': q, 'results': [], 'more': False, 'categories': chips})
    try:
        qs = _apply_search(
            _public_marketplace_products().select_related('category', 'seller_store').defer(*_LIST_DEFER), q
        ).order_by('-featured', '-created_at')
        rows = list(qs[:7])
    except Exception:
        logger.exception('Marketplace live search failed for %r', q)
        return JsonResponse({'q': q, 'results': [], 'more': False, 'categories': chips, 'error': True})
    results = []
    for p in rows[:6]:
        shop = p.seller_store.public_name if p.seller_store_id and p.seller_store.is_published else (p.seller_name or 'SmartPOS Marketplace')
        results.append({
            'name': p.name,
            'price': f'{p.price:,.0f}',
            'url': reverse('marketplace:detail', args=[p.slug]),
            'image': p.image.url if p.image else '',
            'shop': shop,
            'category': p.category.name,
            'in_stock': p.in_stock,
        })
    return JsonResponse({'q': q, 'results': results, 'more': len(rows) > 6, 'categories': chips})


def catalog(request):
    products=(_public_marketplace_products().select_related('category', 'seller_store', 'seller_store__pharmacy')
              .defer(*_LIST_DEFER))
    q=request.GET.get('q','').strip()[:100]
    category=request.GET.get('category','').strip()[:100]
    seller=request.GET.get('seller','').strip()[:150]
    period=request.GET.get('period','today')
    if period not in ('today','week','month'): period='today'
    if q: products=_apply_search(products,q)
    chips=_public_category_chips()
    if category:
        chip=next((c for c in chips if c['slug']==category),None)
        products=products.filter(category__name__iexact=chip['name']) if chip else products.filter(category__slug=category)
    if seller: products=products.filter(seller_name__iexact=seller)
    ids=_trending_ids(period)
    by_id=(_public_marketplace_products().filter(pk__in=ids)
           .select_related('category','seller_store','seller_store__pharmacy').defer(*_LIST_DEFER).in_bulk(ids))
    trending=[by_id[i] for i in ids if i in by_id]
    page=Paginator(products,24).get_page(request.GET.get('page'))
    cart=cart_data(request)
    return render(request,'marketplace/catalog.html',{'products':page,'categories':chips, 'trending':trending, 'period':period, 'q':q, 'selected_category':category, 'selected_seller':seller, 'cart_count':sum(cart.values()), 'cart':cart})

def detail(request,slug):
    product=get_object_or_404(_public_marketplace_products().select_related('category', 'seller_store', 'seller_store__pharmacy'),slug=slug)
    base=_public_marketplace_products().exclude(pk=product.pk).select_related('category', 'seller_store', 'seller_store__pharmacy').defer(*_LIST_DEFER)
    store = product.seller_store if product.seller_store_id and product.seller_store.is_published else None
    if store:
        related=base.filter(seller_store=store).order_by('-featured','-created_at')[:8]
        related_heading=f'More from {store.public_name}'
    elif product.seller_name.strip():
        related=base.filter(seller_name__iexact=product.seller_name.strip()).order_by('-featured','-created_at')[:8]
        related_heading=f'More from {product.seller_name.strip()}'
    else:
        related=base.filter(category=product.category).order_by('-featured','-created_at')[:8]
        related_heading='More in this category'
    cart=cart_data(request)
    from .review_views import review_context
    ctx={
        'product':product,'gallery':list(product.gallery.all()),'related_products':related,'related_heading':related_heading,'seller_store':store,
        'cart_count':sum(cart.values()),'cart':cart,
    }
    ctx.update(review_context(product))
    return render(request,'marketplace/detail.html',ctx)

def cart_data(request):
    result={}
    for k,v in request.session.get('marketplace_cart',{}).items():
        try:
            pk=int(k); qty=int(v)
            if pk>0 and 0<qty<=99: result[str(pk)]=qty
        except (ValueError,TypeError): pass
    return result

def _cart_has_other_shop(cart, product):
    """True when the cart already holds items sold by a different shop than `product`."""
    others = [int(k) for k in cart if k != str(product.pk)]
    if not others:
        return False
    shops = set(Product.objects.filter(pk__in=others).values_list('seller_store_id', flat=True))
    return bool(shops) and shops != {product.seller_store_id}


@require_POST
def add_cart(request, pk):
    """Add or adjust one product; AJAX callers receive a small cart-state response."""
    product = get_object_or_404(_public_marketplace_products(), pk=pk)
    cart = cart_data(request)
    current = cart.get(str(pk), 0)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')
    operation = request.POST.get('operation', 'add').strip().lower()
    try:
        qty = int(request.POST.get('quantity', '1'))
    except (ValueError, TypeError):
        qty = 1

    if operation == 'decrement':
        next_qty = max(0, current - 1)
    elif operation == 'set':
        next_qty = qty
    else:
        next_qty = current + max(1, qty)

    if next_qty > current and _cart_has_other_shop(cart, product):
        message = 'Your cart already has items from another shop. Each order is prepared and delivered by one shop, so finish or clear that cart first.'
        if is_ajax:
            return JsonResponse({'ok': False, 'message': message, 'quantity': current, 'cart_count': sum(cart.values())}, status=409)
        messages.error(request, message)
        return redirect('marketplace:cart')

    max_qty = product.max_cart_qty
    if next_qty > max_qty:
        message = ('A digital download is bought once per order.' if product.is_digital and max_qty
                   else 'Only ' + str(max_qty) + ' unit(s) are currently available.')
        if is_ajax:
            return JsonResponse({'ok': False, 'message': message, 'quantity': current, 'cart_count': sum(cart.values()), 'stock': max_qty}, status=400)
        messages.error(request, message)
        return redirect('marketplace:catalog')

    if next_qty <= 0:
        cart.pop(str(pk), None)
    else:
        cart[str(pk)] = next_qty
    request.session['marketplace_cart'] = cart
    request.session.modified = True

    if is_ajax:
        total_count = sum(cart.values())
        subtotal = product.price * next_qty
        return JsonResponse({
            'ok': True,
            'product_id': product.pk,
            'quantity': next_qty,
            'cart_count': total_count,
            'subtotal': str(subtotal),
            'stock': max_qty,
            'message': 'Cart updated.' if operation != 'add' else 'Added to cart.',
        })
    messages.success(request, 'Product added to cart.' if operation == 'add' else 'Your cart has been updated.')
    return redirect('marketplace:cart')

@require_POST
def update_cart(request):
    cart = cart_data(request)
    changed = False
    for key in list(cart):
        raw = request.POST.get(f'quantity_{key}')
        if raw is None:
            continue
        try:
            qty = int(raw)
        except (TypeError, ValueError):
            qty = 0
        product = _public_marketplace_products().filter(pk=int(key)).first()
        if qty <= 0:
            cart.pop(key, None)
            changed = True
        elif product and qty <= product.max_cart_qty:
            cart[key] = qty
            changed = True
        else:
            messages.error(request, f'Quantity for {product.name if product else "an item"} exceeds available stock.')
    request.session['marketplace_cart'] = cart
    if changed:
        request.session.modified = True
        messages.success(request, 'Your cart has been updated.')
    return redirect('marketplace:cart')

@require_POST
def remove_cart(request,pk):
    cart=cart_data(request); cart.pop(str(pk),None)
    request.session['marketplace_cart']=cart
    messages.success(request, 'Item removed from your cart.')
    return redirect('marketplace:cart')

def cart_items(request):
    cart=cart_data(request)
    products=_public_marketplace_products().filter(pk__in=[int(k) for k in cart]).select_related('category','seller_store','seller_store__pharmacy')
    items=[{'product':p,'quantity':cart[str(p.pk)],'subtotal':p.price*cart[str(p.pk)]} for p in products]
    return items,sum((i['subtotal'] for i in items),Decimal('0'))

def cart_view(request):
    items,total=cart_items(request)
    # Drop products that were unpublished / removed since they were added, so the cart badge,
    # the item count and the checkout all agree (a stale entry used to make checkout fail).
    cart=cart_data(request)
    live={str(i['product'].pk) for i in items}
    stale=[k for k in cart if k not in live]
    if stale:
        for k in stale: cart.pop(k,None)
        request.session['marketplace_cart']=cart
        request.session.modified=True
        messages.info(request,'Some items in your cart are no longer available and were removed.')
    has_physical=any(not i['product'].is_digital for i in items)
    return render(request,'marketplace/cart.html',{'items':items,'total':total,'has_physical':has_physical,'has_digital':any(i['product'].is_digital for i in items),'cart_count':sum(cart_data(request).values())})

@require_POST
def checkout(request):
    """Capture delivery details, then send the customer to SmartPOS's standard payment page."""
    name=request.POST.get('name','').strip()[:150]
    phone=request.POST.get('phone','').strip()[:25]  # the shop needs a number to arrange delivery
    location=request.POST.get('location','').strip()[:250]
    notes=request.POST.get('notes','').strip()[:1000]
    network_key=request.POST.get('network','').strip().lower()  # optional hint only
    selected_network=networks.network_by_key(network_key) if network_key else None
    if not name or not phone:
        messages.error(request,'Please enter your full name and phone number before continuing to payment.')
        return redirect('marketplace:cart')
    digits=''.join(ch for ch in phone if ch.isdigit())
    if digits.startswith('0') and len(digits)==10: digits='255'+digits[1:]
    elif digits.startswith('255') and len(digits)==12: pass
    elif len(digits)==9: digits='255'+digits
    else:
        messages.error(request,'Use a valid Tanzania mobile number such as 0712345678 so the shop can reach you for delivery.')
        return redirect('marketplace:cart')
    with transaction.atomic():
        cart=cart_data(request)
        # of=('self',): the public-products filter joins nullable shop rows, which Postgres cannot lock.
        products=list(_public_marketplace_products().select_for_update(of=('self',)).filter(pk__in=[int(k) for k in cart]))
        if not products or len(products)!=len(cart) or any(cart[str(p.pk)]>p.max_cart_qty for p in products):
            messages.error(request,'Some items are unavailable. Review your cart and update quantities.')
            return redirect('marketplace:cart')
        if any(not p.is_digital for p in products):
            if not location:
                messages.error(request,'Please enter your delivery location so the shop can deliver your order.')
                return redirect('marketplace:cart')
        else:
            location='Digital delivery (download link)'  # nothing to ship: access is issued after payment
        shop_ids={p.seller_store_id for p in products}
        if len(shop_ids)>1:
            messages.error(request,'Your cart has items from more than one shop. Each order is delivered by a single shop, so please order from one shop at a time.')
            return redirect('marketplace:cart')
        total=sum((p.price*cart[str(p.pk)] for p in products),Decimal('0'))
        if total < abliner_client.MIN_AMOUNT or total > abliner_client.MAX_AMOUNT:
            messages.error(request, f'Online payment amount must be between TSh {abliner_client.MIN_AMOUNT:,} and TSh {abliner_client.MAX_AMOUNT:,}.')
            return redirect('marketplace:cart')
        snapshot=[{'product_id':p.pk,'name':p.name,'quantity':cart[str(p.pk)],'unit_price':str(p.price)} for p in products]
        delivery_notes=('Delivery location: ' if any(not p.is_digital for p in products) else 'Delivery: ')+location
        if notes: delivery_notes += '\nDelivery notes: '+notes
        if selected_network: delivery_notes += '\nPreferred mobile-money network: '+selected_network['label']
        attempt=PaymentAttempt.objects.create(customer_name=name,phone=digits,location=location,notes=delivery_notes[:1000],items_snapshot=snapshot,total=total,seller_store_id=next(iter(shop_ids)))
    try:
        txn=create_transaction(
            purpose=PesapalTransaction.Purpose.MARKETPLACE_ORDER, target=attempt, amount=total,
            description=f'SmartPOS Marketplace order · {len(snapshot)} item type(s)',
            payer_name=name, payer_phone=digits,
        )
        # Keep an optional network preference as a hint. The customer can select/confirm
        # their actual network and number on the standard SmartPOS payment screen.
        if selected_network:
            txn.payment_method=selected_network['label']
            txn.save(update_fields=['payment_method','updated_at'])
    except Exception:
        logger.exception('Could not create standard SmartPOS payment transaction for marketplace attempt %s', attempt.reference)
        attempt.status=PaymentAttempt.Status.FAILED
        attempt.save(update_fields=['status','updated_at'])
        messages.error(request,'The payment page is temporarily unavailable. Please try again; no official order has been created.')
        return redirect('marketplace:cart')
    return redirect('payments:checkout', merchant_reference=txn.merchant_reference)


def payment_result(request, reference):
    """Show final marketplace result after the standard SmartPOS payment workflow."""
    attempt=get_object_or_404(PaymentAttempt, reference=reference)
    order=Order.objects.prefetch_related('items').filter(reference=attempt.reference, payment_status=Order.PaymentStatus.PAID).first()
    if order:
        request.session['marketplace_cart']={}
        request.session.modified=True
    txn=PesapalTransaction.objects.filter(content_type__app_label='marketplace', content_type__model='paymentattempt', object_id=attempt.pk).order_by('-created_at').first()
    return render(request,'marketplace/payment_result.html',{'attempt':attempt,'short_ref':short_reference(attempt.reference),'order':order,'txn':txn,'cart_count':sum(cart_data(request).values())})


def payment_cancelled(request, reference):
    """Professional landing page after a customer cancels (or abandons) a marketplace payment."""
    attempt = get_object_or_404(PaymentAttempt, reference=reference)
    # If the money actually arrived, the customer belongs on the confirmation page instead.
    if Order.objects.filter(reference=attempt.reference, payment_status=Order.PaymentStatus.PAID).exists():
        return redirect('marketplace:payment_result', reference=attempt.reference)
    txn = (PesapalTransaction.objects
           .filter(content_type__app_label='marketplace', content_type__model='paymentattempt', object_id=attempt.pk)
           .order_by('-created_at').first())
    if txn and txn.status == PesapalTransaction.Status.COMPLETED:
        return redirect('marketplace:payment_result', reference=attempt.reference)
    cart = cart_data(request)
    snapshot = attempt.items_snapshot if isinstance(attempt.items_snapshot, list) else []
    items = []
    for row in snapshot:
        try:
            items.append({'name': row.get('name', 'Item'), 'quantity': int(row.get('quantity', 1)),
                          'subtotal': Decimal(str(row.get('unit_price', '0'))) * int(row.get('quantity', 1))})
        except Exception:
            continue
    response = render(request, 'marketplace/payment_cancelled.html', {
        'attempt': attempt, 'txn': txn, 'items': items, 'short_ref': short_reference(attempt.reference),
        'can_retry': bool(txn and txn.is_final),
        'cart_count': sum(cart.values()), 'cart_saved': bool(cart),
    })
    response['Cache-Control'] = 'no-store'
    return response


def start_payment(request, reference):
    # Compatibility endpoint: orders already created by earlier versions can still be paid.
    order = get_object_or_404(Order, reference=reference)
    if order.payment_status == Order.PaymentStatus.PAID:
        return redirect(f"/marketplace/track/?reference={order.reference}&phone={order.phone}")
    messages.info(request, 'Please use the payment instructions for this order.')
    return redirect(f"/marketplace/track/?reference={order.reference}&phone={order.phone}")


@csrf_exempt
@require_http_methods(['POST'])
def payment_webhook(request):
    """Only create a customer Order after a signed, amount-matched successful payment."""
    raw=request.body
    signature=request.headers.get('x-abliner-signature','') or request.headers.get('x-webhook-signature','')
    timestamp=request.headers.get('x-abliner-timestamp','') or request.headers.get('x-webhook-timestamp','')
    if not abliner_client.verify_webhook(raw,signature,timestamp):
        return JsonResponse({'status':'error','message':'invalid signature'},status=401)
    try: payload=json.loads(raw.decode('utf-8'))
    except (ValueError,UnicodeDecodeError): return JsonResponse({'status':'error'},status=400)
    data=payload.get('data') if isinstance(payload,dict) else None
    if not isinstance(data,dict): return JsonResponse({'status':'error'},status=400)
    reference=data.get('customer_reference') or data.get('reference')
    status=str(data.get('status') or payload.get('event') or '').lower()
    success=status in ('completed','success','successful','transaction.completed') or payload.get('event')=='transaction.completed'
    failure=any(word in status for word in ('failed','cancel','expired','rejected'))
    try: attempt=PaymentAttempt.objects.get(reference=reference)
    except Exception:
        # Support legacy orders initiated before this deployment.
        try: order=Order.objects.get(reference=reference)
        except Exception: return JsonResponse({'status':'ok','ignored':True})
        try: paid_amount=Decimal(str(data.get('amount')))
        except Exception: return JsonResponse({'status':'error','message':'invalid amount'},status=400)
        if success and paid_amount==order.total and str(data.get('currency') or 'TZS').upper()=='TZS':
            if order.payment_status != Order.PaymentStatus.PAID:
                order.payment_status=Order.PaymentStatus.PAID; order.paid_at=timezone.now()
                order.payment_reference=str(data.get('provider_reference') or data.get('id') or order.payment_reference)[:120]
                order.save(update_fields=['payment_status','paid_at','payment_reference'])
                transaction.on_commit(lambda pk=order.pk: order_flow.send_customer_ticket_sms(pk))
        elif failure and order.payment_status != Order.PaymentStatus.PAID:
            order.payment_status=Order.PaymentStatus.FAILED; order.save(update_fields=['payment_status'])
        return JsonResponse({'status':'ok'})
    if attempt.status == PaymentAttempt.Status.PAID:
        return JsonResponse({'status':'ok','duplicate':True})
    if failure:
        attempt.status=PaymentAttempt.Status.FAILED; attempt.save(update_fields=['status','updated_at'])
        return JsonResponse({'status':'ok'})
    if not success: return JsonResponse({'status':'ok','pending':True})
    try: paid_amount=Decimal(str(data.get('amount')))
    except Exception: return JsonResponse({'status':'error','message':'invalid amount'},status=400)
    if paid_amount != attempt.total or str(data.get('currency') or 'TZS').upper() != 'TZS':
        logger.error('Marketplace payment mismatch for attempt %s',attempt.reference)
        return JsonResponse({'status':'error','message':'amount/currency mismatch'},status=400)
    with transaction.atomic():
        attempt=PaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
        if attempt.status == PaymentAttempt.Status.PAID:
            return JsonResponse({'status':'ok','duplicate':True})
        # Validate stock again before creating the paid order.
        ids=[int(row['product_id']) for row in attempt.items_snapshot]
        products={p.pk:p for p in Product.objects.select_for_update().filter(pk__in=ids,is_active=True,category__is_active=True)}
        for row in attempt.items_snapshot:
            product=products.get(int(row['product_id']))
            if not product or int(row['quantity']) > product.available_qty:
                logger.error('Paid Marketplace attempt %s needs manual stock resolution',attempt.reference)
                return JsonResponse({'status':'error','message':'stock requires review'},status=409)
        order,created=Order.objects.get_or_create(reference=attempt.reference,defaults={'seller_store':attempt.seller_store,'customer_name':attempt.customer_name,'phone':attempt.phone,'location':attempt.location,'notes':attempt.notes,'total':attempt.total,'payment_status':Order.PaymentStatus.PAID,'payment_method':'ABLiner Mobile Money','payment_reference':str(data.get('provider_reference') or data.get('id') or attempt.payment_reference)[:120],'paid_at':timezone.now()})
        if created:
            OrderItem.objects.bulk_create([OrderItem(order=order,product=products[int(row['product_id'])],name=row['name'],quantity=int(row['quantity']),unit_price=Decimal(row['unit_price']),is_digital=products[int(row['product_id'])].is_digital) for row in attempt.items_snapshot])
            for row in attempt.items_snapshot:
                if products[int(row['product_id'])].is_digital:
                    continue
                Product.objects.filter(pk=int(row['product_id'])).update(stock=F('stock')-int(row['quantity']))
            order_flow.record_created(order)
            order_flow.provision_digital(order)
            transaction.on_commit(lambda pk=order.pk: order_flow.notify_new_order(Order.objects.get(pk=pk)))
            transaction.on_commit(lambda pk=order.pk: order_flow.send_customer_ticket_sms(pk))
        attempt.status=PaymentAttempt.Status.PAID; attempt.save(update_fields=['status','updated_at'])
    return JsonResponse({'status':'ok','order_reference':str(attempt.reference)})


def _phone_key(value):
    """Compare phone numbers by their last 9 digits so 0712…, 255712… and +255712… all match."""
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    return digits[-9:]


def track_order(request):
    """Recover an order from its public Ticket ID and checkout phone.

    Supports the full order UUID, the customer-facing SP-XXXXXXXX ticket, and legacy
    alphanumeric provider/payment references. Phone matching is required before details
    or download links are disclosed.
    """
    import uuid
    from django.db.models import CharField
    from django.db.models.functions import Cast

    order = None
    reference = (request.GET.get('reference') or '').strip()
    phone = (request.GET.get('phone') or '').strip()
    if reference and phone:
        base = (Order.objects.select_related('seller_store', 'seller_store__pharmacy')
                .prefetch_related('items__product', 'events'))
        candidates = []
        raw_reference = reference.strip()

        # Full order UUID (case-insensitive input accepted by UUID parser).
        try:
            order_uuid = uuid.UUID(raw_reference)
            found = base.filter(reference=order_uuid).first()
            if found:
                candidates.append(found)
        except (ValueError, AttributeError, TypeError):
            pass

        # Legacy payment-provider references are often alphanumeric and may differ
        # from the UUID shown in newer checkout pages.
        try:
            candidates.extend(base.filter(payment_reference__iexact=raw_reference).order_by('-created_at')[:20])
            attempts = PaymentAttempt.objects.filter(payment_reference__iexact=raw_reference).order_by('-created_at')[:20]
            attempt_ids = [a.reference for a in attempts]
            if attempt_ids:
                candidates.extend(base.filter(reference__in=attempt_ids).order_by('-created_at')[:20])
        except (ValueError, TypeError):
            logger.debug('Could not resolve legacy marketplace payment reference', exc_info=True)

        # Public Ticket ID: SP- plus first 8 hex characters of order UUID. Cast UUID
        # to text and query in the database instead of scanning only the latest 5,000
        # orders (which caused older valid tickets to be missed).
        compact = ''.join(ch for ch in raw_reference.upper() if ch.isalnum())
        if compact.startswith('SP') and len(compact) == 10 and all(ch in '0123456789ABCDEF' for ch in compact[2:]):
            prefix = compact[2:]
            ticket_matches = (base.annotate(_reference_text=Cast('reference', output_field=CharField()))
                              .filter(_reference_text__istartswith=prefix)
                              .order_by('-created_at')[:20])
            candidates.extend(ticket_matches)

        phone_key = _phone_key(phone)
        seen = set()
        for candidate in candidates:
            if candidate.pk in seen:
                continue
            seen.add(candidate.pk)
            if phone_key and _phone_key(candidate.phone) == phone_key:
                order = candidate
                break

        # Recreate missing grants for paid digital orders only; this is idempotent.
        if order and order.payment_status == Order.PaymentStatus.PAID and order.status != Order.Status.CANCELLED:
            order_flow.provision_digital(order)
            order._prefetched_objects_cache.pop('items', None)

    context = {
        'order': order,
        'ticket_id': short_reference(order.reference) if order else '',
        'searched': bool(reference and phone),
        'reference': reference,
        'cart_count': sum(cart_data(request).values()),
    }
    # The Track Order popup asks for just the result block (no page reload).
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        response = render(request, 'marketplace/_track_result.html', context)
        response['Cache-Control'] = 'no-store'
        response['X-Track-Found'] = '1' if order else '0'
        return response
    return render(request, 'marketplace/track.html', context)



@require_http_methods(['GET'])
def customer_receipt(request, reference):
    """Watermarked SmartPOS payment receipt; require the checkout phone to access it."""
    from io import BytesIO
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from django.http import FileResponse, Http404
    from .refs import short_reference
    order = get_object_or_404(Order.objects.prefetch_related('items'), reference=reference,
                              payment_status=Order.PaymentStatus.PAID)
    if not _phone_key(request.GET.get('phone')) or _phone_key(request.GET.get('phone')) != _phone_key(order.phone):
        raise Http404('Receipt not found')
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    width, height = A4
    pdf.saveState()
    pdf.translate(width / 2, height / 2)
    pdf.rotate(35)
    pdf.setFillColorRGB(.91, .94, .93)
    pdf.setFont('Helvetica-Bold', 66)
    pdf.drawCentredString(0, 0, 'SMARTPOS')
    pdf.restoreState()
    y = height - 65
    pdf.setFillColorRGB(.07, .24, .22)
    pdf.setFont('Helvetica-Bold', 22)
    pdf.drawString(48, y, 'SmartPOS Marketplace')
    y -= 25
    pdf.setFont('Helvetica', 11)
    pdf.drawString(48, y, 'Official payment receipt')
    y -= 40
    lines = [('Ticket ID', short_reference(order.reference)),
             ('Customer', order.customer_name), ('Payment', 'PAID'),
             ('Amount', 'TZS {:,.2f}'.format(order.total)),
             ('Paid at', order.paid_at.strftime('%Y-%m-%d %H:%M') if order.paid_at else 'Verified')]
    for label, value in lines:
        pdf.setFont('Helvetica-Bold', 10)
        pdf.drawString(48, y, label + ':')
        pdf.setFont('Helvetica', 10)
        pdf.drawString(160, y, str(value)[:65])
        y -= 26
    y -= 20
    pdf.setFont('Helvetica-Bold', 12)
    pdf.drawString(48, y, 'Purchased items')
    y -= 25
    for item in order.items.all():
        if y < 90:
            pdf.showPage()
            y = height - 65
        pdf.setFont('Helvetica', 10)
        pdf.drawString(48, y, (item.name[:65] + ' x ' + str(item.quantity)))
        y -= 21
    pdf.setFont('Helvetica', 9)
    pdf.drawString(48, 55, 'SmartPOS | Keep your Ticket ID and checkout phone for order recovery.')
    pdf.save()
    output.seek(0)
    response = FileResponse(output, as_attachment=True, filename='SmartPOS-Receipt-' + short_reference(order.reference) + '.pdf', content_type='application/pdf')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response

def _is_marketplace_admin(user):
    return user.is_authenticated and user.is_active and user.is_superuser

def _unique_slug(model, source, max_length, exclude_pk=None, fallback='item'):
    """Build a URL-safe slug from `source`, adding -2, -3 … until it is unique for `model`."""
    base = (slugify(source or '') or fallback)[:max_length - 8].strip('-') or fallback
    candidate, n = base, 2
    qs = model.objects.all()
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    while qs.filter(slug=candidate).exists():
        candidate = f'{base}-{n}'
        n += 1
    return candidate


class AutoSlugFormMixin:
    """The link (slug) is generated automatically from the name and kept stable after creation."""
    slug_source = 'name'
    slug_max_length = 180

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not obj.slug:
            obj.slug = _unique_slug(type(obj), getattr(obj, self.slug_source, ''), self.slug_max_length, obj.pk)
        if commit:
            obj.save()
            self.save_m2m()
        return obj


class ProductForm(AutoSlugFormMixin, forms.ModelForm):
    slug_max_length = 220
    class Meta:
        model = Product
        fields = ('category','name','seller_name','seller_store','description','price','image','stock','featured','is_active')

class CategoryForm(AutoSlugFormMixin, forms.ModelForm):
    """Platform (non-shop) categories only; every shop manages its own from the Seller Centre."""
    slug_max_length = 120
    class Meta:
        model = Category
        fields = ('name','is_active')

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        clash = Category.objects.filter(seller_store__isnull=True, name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError('A platform category with this name already exists.')
        return name


@login_required
@user_passes_test(_is_marketplace_admin)
def manage_dashboard(request):
    # Server-side pagination keeps the owner console fast as the catalogue grows.
    product_q = request.GET.get('product_q', '').strip()[:100]
    products_qs = Product.objects.select_related('category').order_by('-updated_at')
    if product_q:
        products_qs = products_qs.filter(
            Q(name__icontains=product_q) | Q(slug__icontains=product_q) |
            Q(category__name__icontains=product_q)
        )
    product_page = Paginator(products_qs, 12).get_page(request.GET.get('product_page'))
    categories_qs = Category.objects.filter(seller_store__isnull=True).annotate(product_count=Count('products')).order_by('name')
    category_page = Paginator(categories_qs, 8).get_page(request.GET.get('category_page'))
    context = {
        'product_count': Product.objects.count(),
        'active_products': Product.objects.filter(is_active=True).count(),
        'category_count': Category.objects.filter(seller_store__isnull=True).count(),
        'order_count': Order.objects.count(),
        'pending_orders': Order.objects.filter(status=Order.Status.NEW).count(),
        'unpaid_orders': Order.objects.exclude(payment_status=Order.PaymentStatus.PAID).count(),
        'recent_orders': Order.objects.select_related().order_by('-created_at')[:8],
        'product_page': product_page,
        'product_q': product_q,
        'category_page': category_page,
    }
    return render(request, 'marketplace/manage_dashboard.html', context)

@login_required
@user_passes_test(_is_marketplace_admin)
def manage_orders(request):
    qs = Order.objects.prefetch_related('items').order_by('-created_at')
    q = request.GET.get('q', '').strip()[:100]
    status = request.GET.get('status', '').strip()
    payment = request.GET.get('payment', '').strip()
    if q:
        qs = qs.filter(Q(customer_name__icontains=q) | Q(phone__icontains=q) | Q(location__icontains=q) | Q(reference__icontains=q))
    if status in dict(Order.Status.choices):
        qs = qs.filter(status=status)
    if payment in dict(Order.PaymentStatus.choices):
        qs = qs.filter(payment_status=payment)
    page_obj = Paginator(qs, 25).get_page(request.GET.get('page'))
    return render(request, 'marketplace/manage_orders.html', {
        'page_obj': page_obj, 'q': q, 'status': status, 'payment': payment,
        'status_choices': Order.Status.choices, 'payment_choices': Order.PaymentStatus.choices,
    })


@login_required
@user_passes_test(_is_marketplace_admin)
def manage_product(request, pk=None):
    obj = get_object_or_404(Product, pk=pk) if pk else None
    form = ProductForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        old_image = obj.image if obj and obj.image else None
        old_image_name = old_image.name if old_image else ''
        old_image_storage = old_image.storage if old_image else None
        clear_image = bool(old_image_name and request.POST.get('image-clear'))
        form.save()
        # Remove the old stored object when the administrator explicitly clears/replaces it.
        if old_image_name and (clear_image or ('image' in request.FILES and request.FILES.get('image'))):
            try:
                old_image_storage.delete(old_image_name)
            except Exception:
                logger.warning('Could not remove replaced marketplace image %s', old_image.name, exc_info=True)
        messages.success(request, 'Marketplace product saved successfully.')
        return redirect('marketplace:manage')
    return render(request, 'marketplace/manage_form.html', {'form':form,'heading':'Edit product' if obj else 'Add product'})

@login_required
@user_passes_test(_is_marketplace_admin)
def manage_category(request, pk=None):
    obj = get_object_or_404(Category, pk=pk, seller_store__isnull=True) if pk else None
    form = CategoryForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save(); messages.success(request, 'Category saved successfully.'); return redirect('marketplace:manage')
    return render(request, 'marketplace/manage_form.html', {'form':form,'heading':'Edit category' if obj else 'Add category'})

@login_required
@user_passes_test(_is_marketplace_admin)
@require_POST
def delete_product(request, pk):
    product = get_object_or_404(Product, pk=pk)
    # Keep historical order lines intact; archive products that have ever been ordered.
    if product.orderitem_set.exists():
        product.is_active = False
        product.save(update_fields=['is_active', 'updated_at'])
        messages.success(request, 'Product archived because it is referenced by order history.')
    else:
        product.delete()
        messages.success(request, 'Product permanently deleted.')
    return redirect('marketplace:manage')

@login_required
@user_passes_test(_is_marketplace_admin)
@require_POST
def delete_category(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if category.products.exists():
        messages.error(request, 'This category still contains products. Move or remove those products before deleting it.')
    else:
        category.delete()
        messages.success(request, 'Category deleted.')
    return redirect('marketplace:manage')

@login_required
@user_passes_test(_is_marketplace_admin)
def manage_order(request, reference):
    order = get_object_or_404(Order.objects.prefetch_related('items'), reference=reference)
    if request.method == 'POST':
        status = request.POST.get('status'); payment = request.POST.get('payment_status')
        if status in dict(Order.Status.choices): order.status = status
        if payment in dict(Order.PaymentStatus.choices):
            order.payment_status = payment
            if payment == Order.PaymentStatus.PAID and not order.paid_at: order.paid_at = timezone.now()
        order.payment_reference = request.POST.get('payment_reference','').strip()[:120]
        order.payment_method = request.POST.get('payment_method','').strip()[:60]
        order.save(); messages.success(request, 'Order updated. Mark Paid only after verifying the payment with the provider.'); return redirect('marketplace:manage')
    return render(request, 'marketplace/manage_order.html', {'order':order, 'status_choices':Order.Status.choices, 'payment_choices':Order.PaymentStatus.choices})


# Seller centre: all data is scoped to pharmacies the signed-in manager controls.
def _seller_pharmacies(user):
    from pharmacies.models import Pharmacy
    if not user.is_authenticated or not user.is_active:
        return Pharmacy.objects.none()
    if user.is_superuser:
        return Pharmacy.objects.filter(is_active=True)
    if getattr(user, 'role', '') == 'ADMIN':
        return user.managed_pharmacies.filter(is_active=True)
    return Pharmacy.objects.none()


def _seller_store_for_user(request, store_id=None):
    from pharmacies.models import Pharmacy
    pharmacies = _seller_pharmacies(request.user)
    qs = SellerStore.objects.select_related('pharmacy').filter(pharmacy__in=pharmacies).order_by('created_at', 'pk')
    if store_id:
        return get_object_or_404(qs, pk=store_id)
    return qs.first()


def _primary_store(user):
    """The one online shop a manager is allowed to run (the oldest one, for accounts created before the one-shop rule)."""
    return (SellerStore.objects.select_related('pharmacy')
            .filter(pharmacy__in=_seller_pharmacies(user)).order_by('created_at', 'pk').first())

def _annotate_store_services(store, now=None):
    """Attach subscription/plan state to a store (shared by the dashboard and the Plans & billing page)."""
    now = now or timezone.now()
    subs = store.service_subscriptions
    S = OnlineServiceSubscription
    store.online_service_active = _is_platform_store(store) or subs.filter(service=S.Service.ONLINE_SELLING, status=S.Status.ACTIVE, period_end__gt=now).exists()
    sms_active = subs.filter(service=S.Service.ORDER_SMS, status=S.Status.ACTIVE, period_end__gt=now)
    store.order_sms_active = sms_active.exists()
    store.active_sms_until = sms_active.values_list('period_end', flat=True).first()
    online_active = subs.filter(service=S.Service.ONLINE_SELLING, status=S.Status.ACTIVE, period_end__gt=now).values_list('period_end', flat=True).first()
    store.active_online_until = online_active
    store.pending_online = subs.filter(service=S.Service.ONLINE_SELLING, status=S.Status.PENDING).first()
    store.pending_sms = subs.filter(service=S.Service.ORDER_SMS, status=S.Status.PENDING).first()
    return store


@login_required
def seller_dashboard(request):
    stores = list(SellerStore.objects.filter(pharmacy__in=_seller_pharmacies(request.user)).select_related('pharmacy').prefetch_related('service_subscriptions', Prefetch('products', queryset=Product.objects.filter(archived_at__isnull=True).select_related('category').order_by('-created_at'))).order_by('created_at', 'pk'))
    now = timezone.now()
    for store in stores:
        _annotate_store_services(store, now)
        store.product_count = len(store.products.all())
        store.category_count = store.categories.count()
        store.new_order_count = store.orders.filter(status=Order.Status.NEW).count()
        store.listing_new_inquiries = Inquiry.objects.filter(listing__store=store, status='new').count()
        store.open_order_count = store.orders.exclude(status__in=(Order.Status.FULFILLED, Order.Status.CANCELLED)).count()
        # Headline numbers and recent activity for the overview page (never allowed to break the page).
        try:
            stats = _order_stats(store)
            store.revenue_30d = stats.get('revenue_30d') or 0
            store.revenue_total = stats.get('revenue_total') or 0
            store.delivered_count = stats.get('delivered') or 0
            store.recent_orders = list(store.orders.prefetch_related('items').order_by('-created_at')[:5])
            # Last 7 days of paid sales for the overview chart (one bar per day, oldest first).
            from django.db.models.functions import TruncDate
            today = timezone.localdate()
            first_day = today - timedelta(days=6)
            per_day = {row['d']: row['t'] or 0 for row in store.orders.filter(
                payment_status=Order.PaymentStatus.PAID, created_at__date__gte=first_day).exclude(
                status=Order.Status.CANCELLED).annotate(d=TruncDate('created_at')).values('d').annotate(t=Sum('total'))}
            days = [first_day + timedelta(days=i) for i in range(7)]
            peak = max([float(per_day.get(d, 0)) for d in days] + [0])
            store.sales_week = [{'label': d.strftime('%a'), 'full': d.strftime('%d %b'), 'amount': per_day.get(d, 0),
                                 'pct': (max(6, int(round(100 * float(per_day.get(d, 0)) / peak))) if per_day.get(d) else 0)} for d in days]
            store.sales_week_total = sum(per_day.get(d, 0) for d in days)
        except Exception:
            logger.exception('Could not build dashboard activity for store %s', store.pk)
            store.revenue_30d = store.revenue_total = store.delivered_count = 0
            store.recent_orders = []
            store.sales_week = []
            store.sales_week_total = 0
        _prods = [pr for pr in store.products.all() if pr.is_active]
        _phys = [pr for pr in _prods if not pr.is_digital]
        store.out_of_stock_count = sum(1 for pr in _phys if pr.stock == 0)
        store.low_stock_count = sum(1 for pr in _phys if 0 < pr.stock <= 5)
        store.hidden_count = sum(1 for pr in store.products.all() if not pr.is_active)
        # Getting-started checklist: simple booleans the template renders as steps.
        store.setup_steps = [
            {'label': 'Create your shop', 'hint': 'Name, logo and description customers will see.', 'done': True, 'url': reverse('marketplace:seller_store_setup')},
            {'label': 'Activate Online Selling', 'hint': 'Choose a plan so you can publish and receive orders.', 'done': store.online_service_active, 'url': reverse('marketplace:seller_service_requests')},
            {'label': 'Add your first category', 'hint': 'Group products so shoppers can browse easily.', 'done': store.category_count > 0, 'url': reverse('marketplace:seller_store_categories', args=[store.pk])},
            {'label': 'Add your first product', 'hint': 'Photos, price and stock - then it is ready to sell.', 'done': store.product_count > 0, 'url': reverse('marketplace:seller_product_add', args=[store.pk])},
            {'label': 'Publish your shop', 'hint': 'Switch your shop on so the public link works.', 'done': bool(store.is_published), 'url': reverse('marketplace:seller_store_setup')},
        ]
        store.setup_done = sum(1 for step in store.setup_steps if step['done'])
        store.setup_total = len(store.setup_steps)
        store.setup_pct = int(round(100 * store.setup_done / store.setup_total))
        store.setup_next = next((step for step in store.setup_steps if not step['done']), None)
    can_create_store = bool(request.user.is_superuser or not stores)
    return render(request, 'marketplace/seller_dashboard.html', {'stores': stores, 'online_price': Decimal('10000'), 'sms_price': Decimal('5000'), 'can_create_store': can_create_store})

@login_required
@require_POST
def seller_request_service(request, store_id):
    store = _seller_store_for_user(request, store_id)
    service = request.POST.get('service', '').strip().upper()
    configs = {
        OnlineServiceSubscription.Service.ONLINE_SELLING: (Decimal('10000'), 0),
        OnlineServiceSubscription.Service.ORDER_SMS: (Decimal('5000'), 500),
    }
    if service not in configs:
        messages.error(request, 'Choose a valid Marketplace service.')
        return redirect('marketplace:seller_dashboard')
    if not request.user.is_superuser:
        primary = _primary_store(request.user)
        if primary is not None and primary.pk != store.pk:
            messages.error(request, f'Each manager can run one online shop. Your shop is "{primary.public_name}"; services can only be requested for it.')
            return redirect('marketplace:seller_dashboard')
    now = timezone.now()
    current = store.service_subscriptions.filter(service=service, status=OnlineServiceSubscription.Status.ACTIVE, period_end__gt=now).first()
    if current:
        messages.info(request, 'This service is already active for your store.')
        return redirect('marketplace:seller_dashboard')
    pending = store.service_subscriptions.filter(service=service, status=OnlineServiceSubscription.Status.PENDING).first()
    if pending:
        service_request, created = OnlineServiceRequest.objects.get_or_create(
            subscription=pending,
            defaults={'store': store, 'requested_by': request.user, 'service': service,
                      'status': OnlineServiceRequest.Status.AWAITING_PAYMENT, 'amount': pending.monthly_amount},
        )
        if created:
            try:
                from notifications.utils import notify
                from notifications.models import Notification
                from django.contrib.auth import get_user_model
                User = get_user_model()
                for admin_user in User.objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(is_staff=True)).distinct():
                    notify(admin_user, title='New Online Selling service request',
                           message=f'{store.public_name} requested {pending.get_service_display()}. Payment or admin approval is required.',
                           category=Notification.Category.SYSTEM, level=Notification.Level.INFO,
                           link_url='/marketplace/admin/service-requests/', pharmacy=store.pharmacy,
                           dedupe_key=f'marketplace-service-request:{service_request.pk}:{admin_user.pk}')
            except Exception:
                logger.exception('Could not notify admin for pending Marketplace subscription %s', pending.pk)
        return redirect('payments:marketplace_service_payment', subscription_id=pending.pk)
    amount, quota = configs[service]
    with transaction.atomic():
        SellerStore.objects.select_for_update().get(pk=store.pk)  # serialise double-clicks / two tabs
        duplicate = store.service_subscriptions.filter(service=service, status=OnlineServiceSubscription.Status.PENDING).first()
        if duplicate:
            return redirect('payments:marketplace_service_payment', subscription_id=duplicate.pk)
        sub = OnlineServiceSubscription.objects.create(store=store, service=service, monthly_amount=amount, sms_quota=quota)
        service_request = OnlineServiceRequest.objects.create(
            store=store, subscription=sub, requested_by=request.user, service=service,
            status=OnlineServiceRequest.Status.AWAITING_PAYMENT, amount=amount,
        )
        # Notify platform owners and active Shop Owners with admin access.
        try:
            from notifications.utils import notify
            from notifications.models import Notification
            from django.contrib.auth import get_user_model
            User = get_user_model()
            admins = User.objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(is_staff=True)).distinct()
            for admin_user in admins:
                notify(
                    admin_user,
                    title='New Online Selling service request',
                    message=f'{store.public_name} requested {dict(OnlineServiceSubscription.Service.choices).get(service, service)}. Payment or admin approval is required.',
                    category=Notification.Category.SYSTEM,
                    level=Notification.Level.INFO,
                    link_url='/marketplace/admin/service-requests/',
                    pharmacy=store.pharmacy,
                    dedupe_key=f'marketplace-service-request:{service_request.pk}:{admin_user.pk}',
                )
        except Exception:
            logger.exception('Could not create admin notification for marketplace service request %s', service_request.pk)
    messages.info(request, 'Request submitted. Admin has been notified. You can pay now for automatic activation after payment verification, or wait for admin review.')
    return redirect('payments:marketplace_service_payment', subscription_id=sub.pk)


@login_required
def seller_service_requests(request):
    """Plans & billing: the plan cards plus the history of every service request."""
    stores = list(SellerStore.objects.filter(pharmacy__in=_seller_pharmacies(request.user)).select_related('pharmacy').prefetch_related('service_subscriptions').order_by('created_at', 'pk'))
    now = timezone.now()
    for store in stores:
        _annotate_store_services(store, now)
    requests = OnlineServiceRequest.objects.filter(store__in=stores).select_related('store', 'subscription', 'reviewed_by')
    return render(request, 'marketplace/seller_service_requests.html', {
        'service_requests': requests, 'stores': stores,
        'online_price': Decimal('10000'), 'sms_price': Decimal('5000'),
    })


def _can_review_marketplace_requests(user):
    return bool(user.is_authenticated and user.is_active and (user.is_superuser or user.is_staff))

@login_required
@user_passes_test(_can_review_marketplace_requests)
def admin_service_requests(request):
    requests = OnlineServiceRequest.objects.select_related('store', 'store__pharmacy', 'requested_by', 'subscription', 'reviewed_by')
    status_filter = request.GET.get('status', '').upper()
    valid_statuses = {choice[0] for choice in OnlineServiceRequest.Status.choices}
    if status_filter in valid_statuses:
        requests = requests.filter(status=status_filter)
    return render(request, 'marketplace/admin_service_requests.html', {
        'service_requests': requests,
        'status_choices': OnlineServiceRequest.Status.choices,
        'selected_status': status_filter if status_filter in valid_statuses else '',
    })

@login_required
@require_POST
@user_passes_test(_can_review_marketplace_requests)
def admin_review_service_request(request, request_id):
    with transaction.atomic():
        # Lock only the request row (of=('self',)). Locking the joined, nullable subscription/requester
        # rows is what made Postgres fail with "FOR UPDATE cannot be applied to the nullable side of an outer join".
        service_request = get_object_or_404(
            OnlineServiceRequest.objects.select_for_update(of=('self',)).select_related('store', 'store__pharmacy', 'requested_by'),
            pk=request_id)
        sub = (OnlineServiceSubscription.objects.select_for_update().filter(pk=service_request.subscription_id).first()
               if service_request.subscription_id else None)
        action = request.POST.get('action', '').lower()
        note = request.POST.get('admin_note', '').strip()[:500]
        if service_request.status in (OnlineServiceRequest.Status.ACTIVE, OnlineServiceRequest.Status.REJECTED, OnlineServiceRequest.Status.CANCELLED):
            messages.info(request, 'This request has already been resolved.')
            return redirect('marketplace:admin_service_requests')
        if action == 'approve':
            if sub is None:
                messages.error(request, 'The linked subscription is missing; no access was granted.')
                return redirect('marketplace:admin_service_requests')
            now = timezone.now()
            if not sub.is_current:  # a payment that already activated the plan keeps its own period
                sub.status = OnlineServiceSubscription.Status.ACTIVE
                sub.period_start = now
                sub.period_end = now + timedelta(days=30)
                sub.save(update_fields=['status', 'period_start', 'period_end', 'updated_at'])
            if sub.service == OnlineServiceSubscription.Service.ONLINE_SELLING:
                service_request.store.is_published = True
                service_request.store.save(update_fields=['is_published', 'updated_at'])
            service_request.status = OnlineServiceRequest.Status.ACTIVE
            service_request.admin_note = note or 'Approved by administrator.'
            messages.success(request, 'Service approved and activated for 30 days. Approval has been recorded.')
        elif action == 'reject':
            service_request.status = OnlineServiceRequest.Status.REJECTED
            service_request.admin_note = note or 'Rejected by administrator.'
            if sub is not None and sub.status == OnlineServiceSubscription.Status.PENDING:
                sub.status = OnlineServiceSubscription.Status.CANCELLED
                sub.save(update_fields=['status', 'updated_at'])
            messages.success(request, 'Request rejected. The reason is saved for the audit trail.')
        else:
            messages.error(request, 'Choose approve or reject.')
            return redirect('marketplace:admin_service_requests')
        service_request.reviewed_by = request.user
        service_request.reviewed_at = timezone.now()
        service_request.save(update_fields=['status', 'admin_note', 'reviewed_by', 'reviewed_at', 'updated_at'])
        try:
            from notifications.utils import notify
            from notifications.models import Notification
            if service_request.requested_by:
                notify(service_request.requested_by, title='Marketplace service request updated',
                    message=f'{service_request.store.public_name}: {service_request.get_service_display()} — {service_request.get_status_display()}. {service_request.admin_note}',
                    category=Notification.Category.SYSTEM,
                    level=Notification.Level.SUCCESS if action == 'approve' else Notification.Level.WARNING,
                    link_url='/marketplace/seller/requests/', pharmacy=service_request.store.pharmacy,
                    dedupe_key=f'marketplace-service-review:{service_request.pk}:{service_request.status}')
        except Exception:
            logger.exception('Could not notify requester for marketplace request %s', service_request.pk)
    return redirect('marketplace:admin_service_requests')



def _create_store_category(store, name):
    """Create (or return the existing, case-insensitively matching) category that belongs to `store`."""
    name = ' '.join((name or '').split())[:100]
    existing = Category.objects.filter(seller_store=store, name__iexact=name).first()
    if existing:
        return existing
    return Category.objects.create(
        seller_store=store, name=name,
        slug=_unique_slug(Category, f'{store.slug}-{name}', 120, fallback='category'))


class SellerProductForm(AutoSlugFormMixin, forms.ModelForm):
    slug_max_length = 220
    new_category = forms.CharField(
        required=False, max_length=100, label='Or create a new category',
        widget=forms.TextInput(attrs={'placeholder': 'e.g. Body Lotions'}),
        help_text='Categories belong to your shop only. Type a name here if the category does not exist yet.')

    rights_confirmed = forms.BooleanField(
        required=False, label='I own this content or have the right to sell it',
        help_text='Required for digital downloads. Selling copied books, films or music you have no licence for is not allowed and the listing may be removed.')

    movie_quality = forms.ChoiceField(choices=QUALITY_CHOICES, required=False, label='Quality')
    remove_trailer = forms.BooleanField(required=False, label='Remove the current trailer')
    remove_preview = forms.BooleanField(required=False, label='Remove the current free preview')
    upload_token = forms.CharField(required=False, widget=forms.HiddenInput())   # set by the direct-upload script when the file is already in the bucket

    class Meta:
        model = Product
        fields = ('kind', 'category', 'name', 'description', 'price', 'compare_at_price', 'image', 'stock', 'digital_file', 'is_movie', 'trailer_file', 'movie_year', 'movie_minutes', 'movie_genres', 'movie_quality', 'preview_file', 'whats_inside', 'content_summary', 'content_language', 'featured', 'is_active')
        labels = {'kind': 'What are you selling?', 'digital_file': 'Downloadable file', 'is_movie': 'This is a movie', 'trailer_file': 'Trailer (plays automatically on the movie card)', 'movie_year': 'Release year', 'movie_minutes': 'Running time (minutes)', 'movie_genres': 'Genres', 'preview_file': 'Free sample (preview)', 'whats_inside': "What's inside (one item per line)", 'content_summary': 'Size of the full product', 'content_language': 'Language'}
        help_texts = {'preview_file': 'PUBLIC sample, so upload a short extract only: a few pages (PDF, up to 15 MB), a clip of the audio (MP3, up to 15 MB) or a short video (MP4/WebM, up to 80 MB). Never the full file.', 'digital_file': 'PDF, EPUB, MP4/MOV/WebM/MKV/AVI/3GP/MPEG video, MP3, ZIP, Word, PowerPoint or Excel, up to 1 GB. Actual upload size may be limited by hosting configuration. Buyers get access only after verified payment.'}
        widgets = {
            'kind': forms.RadioSelect(),
            'digital_file': forms.FileInput(attrs={'accept': DIGITAL_ACCEPT}),
            'trailer_file': forms.FileInput(attrs={'accept': TRAILER_ACCEPT}),
            'preview_file': forms.FileInput(attrs={'accept': PREVIEW_ACCEPT}),
            'whats_inside': forms.Textarea(attrs={'rows': 5, 'placeholder': 'Stock control sheet\nSales and profit tracker\nDebt register\nStep-by-step guide in Kiswahili'}),
            'content_summary': forms.TextInput(attrs={'placeholder': 'e.g. 120 pages, or 8 lessons - 2h 30m', 'maxlength': '80'}),
            'content_language': forms.TextInput(attrs={'placeholder': 'e.g. Kiswahili', 'maxlength': '30'}),
            'movie_year': forms.NumberInput(attrs={'min': '1900', 'max': '2100', 'step': '1', 'inputmode': 'numeric', 'placeholder': 'e.g. 2024'}),
            'movie_minutes': forms.NumberInput(attrs={'min': '1', 'max': '1000', 'step': '1', 'inputmode': 'numeric', 'placeholder': 'e.g. 105'}),
            'movie_genres': forms.TextInput(attrs={'placeholder': 'Action, Drama'}),
            'description': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Describe the product, size, condition or key features.'}),
            'price': forms.NumberInput(attrs={'min': '0.01', 'step': '0.01', 'inputmode': 'decimal'}),
            'compare_at_price': forms.NumberInput(attrs={'min': '0.01', 'step': '0.01', 'inputmode': 'decimal', 'placeholder': 'Optional old price'}),
            'stock': forms.NumberInput(attrs={'min': '0', 'step': '1', 'inputmode': 'numeric'}),
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Body Perfume 100ml'}),
        }

    def __init__(self, *args, store, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store
        self.instance.seller_store = store
        self.fields['category'].queryset = Category.objects.filter(seller_store=store, is_active=True)
        self.fields['category'].required = False
        self.fields['category'].empty_label = 'Choose one of your categories'
        self.fields['new_category'].widget.attrs['list'] = 'mk-category-suggestions'
        self.fields['stock'].required = False   # digital downloads have no stock
        self.fields['kind'].choices = Product.Kind.choices
        self.fields['digital_file'].required = False
        self.had_file = bool(self.instance.pk and self.instance.digital_file)
        self.pending_upload = None
        self.order_fields(['kind', 'category', 'new_category'] + [f for f in self.fields if f not in ('kind', 'category', 'new_category')])

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if not slugify(name):
            raise forms.ValidationError('Enter a product name using letters or numbers so a shareable link can be created.')
        return name

    def clean(self):
        data = super().clean()
        old_price, price = data.get('compare_at_price'), data.get('price')
        if old_price is not None and price is not None and old_price <= price:
            self.add_error('compare_at_price', 'The old price must be higher than the current price, or leave it empty.')
        kind = data.get('kind') or Product.Kind.PHYSICAL
        upload = data.get('digital_file')
        has_new_upload = bool(upload) and hasattr(upload, 'content_type')
        pending = self._resolve_pending_upload(data)
        if pending and kind != Product.Kind.DIGITAL:
            self.add_error('upload_token', 'A file can only be attached to a digital download product.')
        if kind == Product.Kind.DIGITAL:
            if pending:
                if not data.get('rights_confirmed'):
                    self.add_error('rights_confirmed', 'Please confirm you have the right to sell this file.')
            elif has_new_upload:
                problem = validate_digital_upload(upload)
                if problem:
                    self.add_error('digital_file', problem)
                elif not data.get('rights_confirmed'):
                    self.add_error('rights_confirmed', 'Please confirm you have the right to sell this file.')
            elif not self.had_file:
                self.add_error('digital_file', 'Upload the file buyers will download after paying.')
            data['stock'] = 0
        else:
            if data.get('stock') is None and 'stock' not in self.errors:
                self.add_error('stock', 'Enter how many units you have in stock.')
            if has_new_upload:
                self.add_error('digital_file', 'A file can only be attached to a digital download product.')
            if self.had_file:
                data['digital_file'] = False   # switching back to a physical product detaches the old file
            if self.instance.pk and self.instance.kind == Product.Kind.DIGITAL:
                from .models import DownloadGrant
                if DownloadGrant.objects.filter(order_item__product=self.instance).exists():
                    self.add_error('kind', 'Customers have already bought this download, so it cannot be changed into a physical product.')
        self._clean_movie(data, kind)
        self._clean_preview(data, kind, upload if has_new_upload else None)
        new_name = ' '.join((data.get('new_category') or '').split())
        if new_name:
            if not slugify(new_name):
                self.add_error('new_category', 'Use letters or numbers in the category name.')
            elif data.get('category') is None:
                data['category'] = Category.objects.filter(seller_store=self.store, name__iexact=new_name).first()
        elif data.get('category') is None and 'category' not in self.errors:
            self.add_error('category', 'Choose a category or type a new one.')
        return data

    def _clean_movie(self, data, kind):
        """Movie rules: digital video file, poster, optional validated trailer, and a distribution-rights confirmation."""
        trailer = data.get('trailer_file')
        new_trailer = bool(trailer) and hasattr(trailer, 'content_type')
        if not data.get('is_movie'):
            if new_trailer:
                self.add_error('trailer_file', 'A trailer can only be added to a movie. Tick "This is a movie".')
            return
        if kind != Product.Kind.DIGITAL:
            self.add_error('is_movie', 'A movie is sold as a digital download. Choose "Digital download" above.')
            return
        if self.pending_upload and os.path.splitext(self.pending_upload.filename)[1].lower() not in MOVIE_FILE_EXT:
            self.add_error('digital_file', 'A movie must be a video file (MP4, MOV, MKV, WebM, AVI, 3GP or MPEG).')
        upload = data.get('digital_file')
        if upload and hasattr(upload, 'content_type') and 'digital_file' not in self.errors:
            if os.path.splitext(upload.name or '')[1].lower() not in MOVIE_FILE_EXT:
                self.add_error('digital_file', 'A movie must be a video file (MP4, MOV, MKV, WebM, AVI, 3GP or MPEG).')
        if not data.get('image') and 'image' not in self.errors:
            self.add_error('image', 'Add a poster image: it is the cover of the movie card.')
        if new_trailer:
            problem = validate_trailer_upload(trailer)
            if problem:
                self.add_error('trailer_file', problem)
            elif not data.get('rights_confirmed'):
                self.add_error('rights_confirmed', 'Please confirm you have the right to distribute this movie and its trailer.')
        if data.get('remove_trailer') and not new_trailer:
            data['trailer_file'] = False
        year = data.get('movie_year')
        if year is not None and clean_year(year) is None:
            self.add_error('movie_year', 'Enter a real release year, e.g. 2024.')
        minutes = data.get('movie_minutes')
        if minutes is not None and not (1 <= minutes <= MOVIE_MAX_MINUTES):
            self.add_error('movie_minutes', 'Running time must be between 1 and %d minutes.' % MOVIE_MAX_MINUTES)
        data['movie_genres'] = ', '.join(split_genres(data.get('movie_genres'), limit=6))

    def _clean_preview(self, data, kind, new_upload):
        """Free preview rules: a short public sample for digital (non-movie) products, plus the checklist and file facts."""
        preview = data.get('preview_file')
        new_preview = bool(preview) and hasattr(preview, 'content_type')
        if kind != Product.Kind.DIGITAL or data.get('is_movie'):
            if new_preview:
                self.add_error('preview_file', 'A free preview can only be added to a digital download that is not a movie (movies use a trailer).')
            elif self.instance.pk and self.instance.preview_file:
                data['preview_file'] = False   # no longer applies: the old public sample is removed
            return
        if new_preview:
            if self.pending_upload is not None:
                full_size = self.pending_upload.size
            elif new_upload is not None:
                full_size = new_upload.size
            else:
                full_size = (self.instance.digital_file_size or None) if self.instance.pk else None
            problem = validate_preview_upload(preview, full_size=full_size)
            if problem:
                self.add_error('preview_file', problem)
        if data.get('remove_preview') and not new_preview:
            data['preview_file'] = False
        raw_items = parse_whats_inside(data.get('whats_inside'), limit=1000, max_len=10000)
        if len(raw_items) > WHATS_INSIDE_MAX_ITEMS:
            self.add_error('whats_inside', 'List at most %d items, one per line.' % WHATS_INSIDE_MAX_ITEMS)
        elif any(len(i) > WHATS_INSIDE_MAX_LEN for i in raw_items):
            self.add_error('whats_inside', 'Keep each item under %d characters.' % WHATS_INSIDE_MAX_LEN)
        else:
            data['whats_inside'] = '\n'.join(raw_items)
        data['content_summary'] = ' '.join((data.get('content_summary') or '').split())[:80]
        data['content_language'] = ' '.join((data.get('content_language') or '').split())[:30]

    def _resolve_pending_upload(self, data):
        """The verified bucket upload named by the hidden token, or None. Bad tokens become a form error."""
        token = (data.get('upload_token') or '').strip()
        self.pending_upload = None
        if not token:
            return None
        from .models import PendingUpload
        try:
            p = PendingUpload.objects.filter(pk=token, seller_store=self.store).first()
        except Exception:   # malformed uuid
            p = None
        if p is None or p.consumed or p.status != PendingUpload.Status.COMPLETE:
            self.add_error('upload_token', 'The uploaded movie could not be found or was already used. Choose the file again.')
            return None
        self.pending_upload = p
        return p

    def finish_direct_upload(self):
        """Call after the product row is saved: the bucket file now belongs to it."""
        if self.pending_upload:
            self.pending_upload.consumed = True
            self.pending_upload.save(update_fields=['consumed', 'updated_at'])

    def save(self, commit=True):
        if self.pending_upload and self.cleaned_data.get('kind') == Product.Kind.DIGITAL:
            self.instance.digital_file = self.pending_upload.storage_name
            self.instance.digital_file_name = self.pending_upload.filename[:180]
            self.instance.digital_file_size = self.pending_upload.size
        new_name = ' '.join((self.cleaned_data.get('new_category') or '').split())
        if new_name and self.cleaned_data.get('category') is None:
            self.instance.category = _create_store_category(self.store, new_name)
        upload = self.cleaned_data.get('digital_file')
        if self.cleaned_data.get('kind') == Product.Kind.DIGITAL and upload and hasattr(upload, 'content_type'):
            self.instance.digital_file_name = os.path.basename(upload.name)[:180]
            self.instance.digital_file_size = upload.size
        return super().save(commit)


def _store_or_setup(request, store_id):
    """Resolve the manager's shop (explicit id, or their only/primary shop)."""
    store = _seller_store_for_user(request, store_id) if store_id else _seller_store_for_user(request)
    return store



MAX_GALLERY = 6

# Ready-made ideas for a seller's own categories (shown as suggestions; every shop still owns its categories).
CATEGORY_SUGGESTIONS = [
    'Stationery', 'School supplies', 'Books', 'E-books & PDFs', 'Videos & movies', 'Music & audio',
    'Electronics', 'Phones & accessories', 'Clothing', 'Shoes', 'Beauty & cosmetics', 'Food & drinks',
    'Car spare parts', 'Furniture', 'Building materials', 'Services',
]


def _discard_private_file(product, name):
    try:
        product.digital_file.storage.delete(name)
    except Exception:
        logger.warning('Could not remove replaced digital file %s', name, exc_info=True)


def _discard_public_file(storage, name):
    try:
        storage.delete(name)
    except Exception:
        logger.warning('Could not remove replaced file %s', name, exc_info=True)


def _gallery_files(request, product=None):
    """Validate extra product photos. Returns (files, errors)."""
    from .listing_views import _validate_images
    files, errors = _validate_images(request.FILES.getlist('gallery'))
    existing = product.gallery.count() if product is not None else 0
    if existing + len(files) > MAX_GALLERY:
        errors.append(f'A product can have at most {MAX_GALLERY} extra photos besides the main photo.')
    return files, errors


def _save_gallery(product, files):
    start = (product.gallery.order_by('-position').values_list('position', flat=True).first() or 0) + 1
    for i, f in enumerate(files):
        ProductImage.objects.create(product=product, image=f, position=start + i)


@login_required
@require_POST
def seller_product_gallery_delete(request, store_id, pk, image_id):
    store = _seller_store_for_user(request, store_id)
    product = get_object_or_404(Product, pk=pk, seller_store=store)
    image = get_object_or_404(ProductImage, pk=image_id, product=product)
    image.image.delete(save=False)
    image.delete()
    messages.success(request, 'Photo removed.')
    return redirect('marketplace:seller_product_edit', store_id=store.pk, pk=product.pk)

@login_required
def seller_product_add(request, store_id):
    store = _seller_store_for_user(request, store_id)
    if not _require_active_seller_store(request, store):
        return redirect('marketplace:seller_dashboard')
    kind_hint = request.GET.get('kind')
    initial = {'kind': 'DIGITAL', 'is_movie': True} if kind_hint == 'movie' else ({'kind': 'DIGITAL'} if kind_hint == 'digital' else None)
    form = SellerProductForm(request.POST or None, request.FILES or None, store=store, initial=initial)
    gallery, gallery_errors = _gallery_files(request) if request.method == 'POST' else ([], [])
    for e in gallery_errors:
        form.add_error(None, e)
    if request.method == 'POST' and form.is_valid() and not gallery_errors:
        product = form.save(commit=False)
        product.seller_store = store
        product.seller_name = store.public_name
        product.save()
        _save_gallery(product, gallery)
        form.finish_direct_upload()
        messages.success(request, 'Product saved to your online shop.')
        return redirect('marketplace:seller_store_products', store_id=store.pk)
    return render(request, 'marketplace/seller_product_form.html', {'form': form, 'store': store, 'heading': 'Add movie' if (initial or {}).get('is_movie') else ('Add digital download' if (initial or {}).get('kind') == 'DIGITAL' else 'Add product'), 'gallery': [], 'max_gallery': MAX_GALLERY, 'category_suggestions': CATEGORY_SUGGESTIONS, 'direct_upload': bool(getattr(settings, 'USE_S3', False))})

@login_required
def seller_product_edit(request, store_id, pk):
    store = _seller_store_for_user(request, store_id)
    if not _require_active_seller_store(request, store):
        return redirect('marketplace:seller_dashboard')
    product = get_object_or_404(Product, pk=pk, seller_store=store)
    old_file = product.digital_file.name if product.digital_file else ''
    old_trailer = product.trailer_file.name if product.trailer_file else ''
    old_preview = product.preview_file.name if product.preview_file else ''
    form = SellerProductForm(request.POST or None, request.FILES or None, instance=product, store=store)
    gallery, gallery_errors = _gallery_files(request, product) if request.method == 'POST' else ([], [])
    for e in gallery_errors:
        form.add_error(None, e)
    if request.method == 'POST' and form.is_valid() and not gallery_errors:
        product = form.save(commit=False)
        product.seller_store = store
        product.seller_name = store.public_name
        product.save()
        _save_gallery(product, gallery)
        form.finish_direct_upload()
        if old_file and old_file != (product.digital_file.name if product.digital_file else ''):
            _discard_private_file(product, old_file)   # a replaced download must not linger in storage
        if old_trailer and old_trailer != (product.trailer_file.name if product.trailer_file else ''):
            _discard_public_file(product.trailer_file.storage, old_trailer)   # a replaced trailer must not linger either
        if old_preview and old_preview != (product.preview_file.name if product.preview_file else ''):
            _discard_public_file(product.preview_file.storage, old_preview)   # nor a replaced free sample
        messages.success(request, 'Product updated.')
        return redirect('marketplace:seller_store_products', store_id=store.pk)
    return render(request, 'marketplace/seller_product_form.html', {'form': form, 'store': store, 'heading': 'Edit product', 'product': product, 'gallery': product.gallery.all(), 'max_gallery': MAX_GALLERY, 'category_suggestions': CATEGORY_SUGGESTIONS, 'direct_upload': bool(getattr(settings, 'USE_S3', False))})


# ---------------------------------------------------------------- shop products (list / hide / stock / delete) --
def _safe_next(request, fallback):
    """Return a same-site relative URL from ?next= / POST next, or `fallback`."""
    from django.utils.http import url_has_allowed_host_and_scheme
    target = request.POST.get('next') or request.GET.get('next') or ''
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return fallback


@login_required
def seller_products(request, store_id=None):
    """Full product list for the manager's shop: search, filter, paginate."""
    store = _store_or_setup(request, store_id)
    if store is None:
        messages.info(request, 'Create your online shop first; your products will be listed here.')
        return redirect('marketplace:seller_store_setup')
    base = Product.objects.filter(seller_store=store, archived_at__isnull=True).select_related('category').defer('description')
    q = request.GET.get('q', '').strip()[:100]
    view = request.GET.get('view', '').strip()
    low_limit = 5
    qs = base
    if view == 'active':
        qs = qs.filter(is_active=True)
    elif view == 'hidden':
        qs = qs.filter(is_active=False)
    elif view == 'low':
        qs = qs.filter(kind='PHYSICAL', stock__gt=0, stock__lte=low_limit)
    elif view == 'out':
        qs = qs.filter(kind='PHYSICAL', stock=0)
    elif view == 'digital':
        qs = qs.filter(kind='DIGITAL')
    elif view == 'featured':
        qs = qs.filter(featured=True)
    else:
        view = ''
    if q:
        for word in q.split()[:5]:
            qs = qs.filter(Q(name__icontains=word) | Q(category__name__icontains=word))
    counts = base.aggregate(
        all_count=Count('pk'),
        active_count=Count('pk', filter=Q(is_active=True)),
        hidden_count=Count('pk', filter=Q(is_active=False)),
        low_count=Count('pk', filter=Q(kind='PHYSICAL', stock__gt=0, stock__lte=low_limit)),
        out_count=Count('pk', filter=Q(kind='PHYSICAL', stock=0)),
        digital_count=Count('pk', filter=Q(kind='DIGITAL')),
    )
    page_obj = Paginator(qs.order_by('-created_at'), 15).get_page(request.GET.get('page'))
    return render(request, 'marketplace/seller_products.html', {
        'store': store, 'page_obj': page_obj, 'q': q, 'view': view, 'counts': counts,
        'low_limit': low_limit, 'selling_active': _online_selling_active(store),
    })


@login_required
@require_POST
def seller_product_action(request, store_id, pk):
    """One-click product actions: show/hide, feature, set stock, delete (archive when it has order history)."""
    store = _seller_store_for_user(request, store_id)
    product = get_object_or_404(Product, pk=pk, seller_store=store)
    action = request.POST.get('action', '')
    dest = _safe_next(request, reverse('marketplace:seller_store_products', args=[store.pk]))
    if action == 'toggle_active':
        product.is_active = not product.is_active
        product.save(update_fields=['is_active', 'updated_at'])
        messages.success(request, f'“{product.name}” is now visible to shoppers.' if product.is_active
                         else f'“{product.name}” is hidden from shoppers. You can show it again any time.')
    elif action == 'toggle_featured':
        product.featured = not product.featured
        product.save(update_fields=['featured', 'updated_at'])
        messages.success(request, f'“{product.name}” is now featured.' if product.featured else f'“{product.name}” is no longer featured.')
    elif action == 'set_stock':
        raw = (request.POST.get('stock') or '').strip()
        if product.is_digital:
            messages.error(request, 'Digital downloads have no stock: they stay available while the file is attached.')
        elif not raw.isdigit() or int(raw) > 1_000_000:
            messages.error(request, 'Enter a whole number for the stock quantity (0 or more).')
        else:
            product.stock = int(raw)
            product.save(update_fields=['stock', 'updated_at'])
            messages.success(request, f'Stock for “{product.name}” updated to {product.stock}.')
    elif action == 'delete':
        # Permanently delete when no protected order/download history depends on the product.
        # If historical relations protect it, safely archive it instead.
        try:
            with transaction.atomic():
                locked = Product.objects.select_for_update().get(pk=product.pk, seller_store=store)
                try:
                    locked.delete()
                    deleted_permanently = True
                except ProtectedError:
                    deleted_permanently = False
                    locked.archived_at = timezone.now()
                    locked.is_active = False
                    locked.featured = False
                    locked.save(update_fields=['archived_at', 'is_active', 'featured', 'updated_at'])
            for period in ('today', 'week', 'month'):
                cache.delete(f'marketplace:trending:{period}')
            if deleted_permanently:
                messages.success(request, 'Product permanently deleted.')
            else:
                messages.warning(request, 'Product removed from your shop. It is retained in archived form because order or download history depends on it.')
        except Exception:
            logger.exception('Marketplace seller product delete failed: store_id=%s product_id=%s', store.pk, product.pk)
            messages.error(request, 'Unable to delete this product right now. Please try again or contact support.')
    else:
        messages.error(request, 'Unknown action.')
    return redirect(dest)


@login_required
@require_POST
def seller_products_bulk_delete(request, store_id):
    """Bulk-delete selected products, archiving only those protected by historical orders."""
    store = _seller_store_for_user(request, store_id)
    raw_ids = request.POST.getlist('product_ids')
    ids = []
    for value in raw_ids[:100]:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        messages.info(request, 'Select at least one product first.')
        return redirect(_safe_next(request, reverse('marketplace:seller_store_products', args=[store.pk])))
    deleted = archived = failed = 0
    for product in Product.objects.filter(pk__in=ids, seller_store=store, archived_at__isnull=True):
        try:
            with transaction.atomic():
                locked = Product.objects.select_for_update().get(pk=product.pk, seller_store=store)
                try:
                    locked.delete()
                    deleted += 1
                except ProtectedError:
                    locked.archived_at = timezone.now()
                    locked.is_active = False
                    locked.featured = False
                    locked.save(update_fields=['archived_at', 'is_active', 'featured', 'updated_at'])
                    archived += 1
        except Exception:
            failed += 1
            logger.exception('Marketplace bulk delete failed: store_id=%s product_id=%s', store.pk, product.pk)
    for period in ('today', 'week', 'month'):
        cache.delete(f'marketplace:trending:{period}')
    if deleted:
        messages.success(request, f'{deleted} product(s) permanently deleted.')
    if archived:
        messages.warning(request, f'{archived} product(s) archived because order or download history depends on them.')
    if failed:
        messages.error(request, f'{failed} product(s) could not be deleted. Check server logs.')
    return redirect(_safe_next(request, reverse('marketplace:seller_store_products', args=[store.pk])))


# ---------------------------------------------------------------- shop categories --
@login_required
def seller_categories(request, store_id=None):
    store = _store_or_setup(request, store_id)
    if store is None:
        messages.info(request, 'Create your online shop first, then add your product categories.')
        return redirect('marketplace:seller_store_setup')
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'add':
            name = ' '.join(request.POST.get('name', '').split())[:100]
            if not name or not slugify(name):
                messages.error(request, 'Enter a category name using letters or numbers.')
            elif Category.objects.filter(seller_store=store, name__iexact=name).exists():
                messages.info(request, f'You already have a category called "{name}".')
            else:
                _create_store_category(store, name)
                messages.success(request, f'Category "{name}" added to your shop.')
        else:
            category = get_object_or_404(Category, pk=request.POST.get('category_id') or 0, seller_store=store)
            if action == 'rename':
                name = ' '.join(request.POST.get('name', '').split())[:100]
                clash = Category.objects.filter(seller_store=store, name__iexact=name).exclude(pk=category.pk).exists()
                if not name or not slugify(name):
                    messages.error(request, 'Enter a category name using letters or numbers.')
                elif clash:
                    messages.error(request, f'You already have a category called "{name}".')
                else:
                    category.name = name
                    category.save(update_fields=['name'])
                    messages.success(request, 'Category renamed.')
            elif action == 'toggle':
                category.is_active = not category.is_active
                category.save(update_fields=['is_active'])
                messages.success(request, 'Category shown to shoppers.' if category.is_active else 'Category hidden from shoppers (its products are hidden too).')
            elif action == 'delete':
                # Products must remain attached to a valid category (PROTECT FK).
                products = category.products.all()
                if products.exists():
                    target_id = request.POST.get('target_category_id')
                    target = Category.objects.filter(pk=target_id, seller_store=store).exclude(pk=category.pk).first()
                    if target is None:
                        messages.error(request, 'Choose another category to move these products into before deleting this category.')
                    else:
                        moved = products.update(category=target)
                        category.delete()
                        messages.success(request, f'Category deleted. {moved} product(s) moved to “{target.name}”.')
                else:
                    category.delete()
                    messages.success(request, 'Category deleted.')
            else:
                messages.error(request, 'Unknown action.')
        return redirect('marketplace:seller_store_categories', store_id=store.pk)
    categories = store.categories.annotate(product_count=Count('products')).order_by('name')
    return render(request, 'marketplace/seller_categories.html', {'store': store, 'categories': categories})


# ---------------------------------------------------------------- shop orders --
def _empty_order_stats():
    """Safe zero-state used if the statistics query ever fails."""
    return {
        'order_count': 0, 'new': 0, 'preparing': 0, 'out': 0,
        'delivered': 0, 'cancelled': 0, 'revenue_30d': 0, 'revenue_total': 0,
    }


def _order_stats(store):
    """Headline numbers for the shop orders page.

    NOTE: aggregate aliases must never share a name with a model field
    (Order.total is a field), otherwise Django raises
    "Cannot compute Sum('total'): 'total' is an aggregate".
    """
    now = timezone.now()
    paid_live = Q(payment_status=Order.PaymentStatus.PAID) & ~Q(status=Order.Status.CANCELLED)
    try:
        return Order.objects.filter(seller_store=store).aggregate(
            order_count=Count('pk'),
            new=Count('pk', filter=Q(status=Order.Status.NEW)),
            preparing=Count('pk', filter=Q(status__in=(Order.Status.CONTACTED, Order.Status.CONFIRMED))),
            out=Count('pk', filter=Q(status=Order.Status.OUT_FOR_DELIVERY)),
            delivered=Count('pk', filter=Q(status=Order.Status.FULFILLED)),
            cancelled=Count('pk', filter=Q(status=Order.Status.CANCELLED)),
            revenue_30d=Sum('total', filter=paid_live & Q(created_at__gte=now - timedelta(days=30))),
            revenue_total=Sum('total', filter=paid_live),
        )
    except Exception:
        # Statistics are secondary: never take the whole orders page down for them.
        logger.exception('Could not compute order stats for store %s', getattr(store, 'pk', None))
        return _empty_order_stats()


@login_required
def seller_orders(request, store_id=None):
    store = _store_or_setup(request, store_id)
    if store is None:
        messages.info(request, 'Create your online shop first; customer orders will appear here.')
        return redirect('marketplace:seller_store_setup')
    qs = Order.objects.filter(seller_store=store).prefetch_related('items')
    q = request.GET.get('q', '').strip()[:100]
    view = request.GET.get('view', '').strip()
    open_states = (Order.Status.NEW, Order.Status.CONTACTED, Order.Status.CONFIRMED, Order.Status.OUT_FOR_DELIVERY)
    if view == 'open':
        qs = qs.filter(status__in=open_states)
    elif view in dict(Order.Status.choices):
        qs = qs.filter(status=view)
    else:
        view = ''
    if q:
        qs = qs.filter(Q(customer_name__icontains=q) | Q(phone__icontains=q) | Q(location__icontains=q) | Q(reference__icontains=q))
    page_obj = Paginator(qs, 20).get_page(request.GET.get('page'))
    return render(request, 'marketplace/seller_orders.html', {
        'store': store, 'page_obj': page_obj, 'q': q, 'view': view, 'stats': _order_stats(store),
        'status_choices': Order.Status.choices,
    })


@login_required
def seller_order_detail(request, store_id, reference):
    store = _seller_store_for_user(request, store_id)
    order = get_object_or_404(Order.objects.prefetch_related('items', 'events__actor'), reference=reference, seller_store=store)
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'save_note':
            order.seller_note = request.POST.get('seller_note', '').strip()[:2000]
            order.save(update_fields=['seller_note'])
            messages.success(request, 'Internal note saved.')
        else:
            try:
                updated = order_flow.change_status(order.pk, action, actor=request.user, note=request.POST.get('note', ''), store=store)
                messages.success(request, f'Order is now: {updated.get_status_display()}.')
            except order_flow.TransitionError as exc:
                messages.error(request, str(exc))
        return redirect('marketplace:seller_order_detail', store_id=store.pk, reference=order.reference)
    return render(request, 'marketplace/seller_order_detail.html', {'store': store, 'order': order, 'next_steps': order.allowed_next()})


# ---------------------------------------------------------------- store setup --
@login_required
def seller_store_setup(request):
    if request.user.is_superuser:
        _platform_pharmacy()   # make sure the "SmartPOS" business exists for the system admin
    pharmacies = _seller_pharmacies(request.user)
    if request.user.is_superuser:
        # Platform business first, so the admin sells as SmartPOS by default.
        pharmacies = sorted(pharmacies, key=lambda p: (p.name.strip().lower() != PLATFORM_BUSINESS_NAME.lower(), p.name.lower()))
    if not pharmacies:
        messages.error(request, 'Only an active Shop Owner with managed branches can create an online store.')
        return redirect('marketplace:seller_dashboard')
    # One manager runs one online shop. Platform super-admins are exempt.
    existing = None if request.user.is_superuser else _primary_store(request.user)
    social_typed = None   # what the seller typed, re-shown if the save is rejected
    if request.method == 'POST':
        public_name = request.POST.get('public_name', '').strip()[:150]
        description = request.POST.get('description', '').strip()[:3000]
        social_clean, social_errors, social_typed = parse_social_post(request.POST)
        if not public_name or not slugify(public_name):
            messages.error(request, 'Enter a shop name using letters or numbers so a public link can be created.')
        elif social_errors:
            for problem in social_errors:
                messages.error(request, problem)
        else:
            with transaction.atomic():
                if not request.user.is_superuser:
                    # Serialise concurrent submits so one manager can never end up with two shops.
                    get_user_model().objects.select_for_update().get(pk=request.user.pk)
                    existing = _primary_store(request.user)
                if existing is not None:
                    store, created = existing, False
                else:
                    pharmacy = get_object_or_404(_seller_pharmacies(request.user), pk=request.POST.get('pharmacy') or 0)
                    store, created = SellerStore.objects.get_or_create(
                        pharmacy=pharmacy,
                        defaults={'slug': _unique_slug(SellerStore, public_name, 180, fallback='shop'), 'public_name': public_name})
                # The public link is created once from the shop name and stays stable afterwards.
                store.public_name, store.description = public_name, description
                for field_name, url in social_clean.items():
                    setattr(store, field_name, url)
                wants_publish = request.POST.get('is_published') == 'on'
                online_active = _online_selling_active(store)
                store.is_published = wants_publish and online_active
                if request.FILES.get('logo'):
                    store.logo = request.FILES['logo']
                store.save()
            if wants_publish and not online_active:
                messages.info(request, 'Your store details are saved. Complete the Online Selling subscription payment to publish the store.')
            messages.success(request, 'Online store created. Next: add your categories and products.' if created else 'Online store details updated.')
            return redirect('marketplace:seller_dashboard')
    if social_typed is None:
        social_typed = {k: (form_value(k, getattr(existing, c['field'], '')) if existing else '') for k, c in SOCIAL_PLATFORMS.items()}
    return render(request, 'marketplace/seller_store_setup.html', {'pharmacies': pharmacies, 'store': existing, 'social_fields': social_form_fields(social_typed, existing)})

@login_required
def seller_payment_settings(request, store_id):
    store = _seller_store_for_user(request, store_id)
    if not _require_active_seller_store(request, store):
        return redirect('marketplace:seller_dashboard')
    profile, _ = MerchantPaymentProfile.objects.get_or_create(store=store)
    if request.method == 'POST':
        provider = request.POST.get('provider', '').upper()
        if provider not in dict(MerchantPaymentProfile.Provider.choices):
            messages.error(request, 'Choose ABLiner or Pesapal.')
        else:
            profile.provider = provider
            # Blank secret inputs mean keep existing credentials; never echo secrets back into HTML.
            if request.POST.get('abliner_api_key', '').strip(): profile.abliner_api_key = request.POST['abliner_api_key'].strip()
            if request.POST.get('abliner_webhook_secret', '').strip(): profile.abliner_webhook_secret = request.POST['abliner_webhook_secret'].strip()
            if request.POST.get('pesapal_consumer_key', '').strip(): profile.pesapal_consumer_key = request.POST['pesapal_consumer_key'].strip()
            if request.POST.get('pesapal_consumer_secret', '').strip(): profile.pesapal_consumer_secret = request.POST['pesapal_consumer_secret'].strip()
            profile.abliner_base_url = (request.POST.get('abliner_base_url') or 'https://abliner.net').strip().rstrip('/')
            profile.pesapal_environment = request.POST.get('pesapal_environment', 'SANDBOX') if request.POST.get('pesapal_environment') in ('SANDBOX','LIVE') else 'SANDBOX'
            # Credentials submitted by a seller are not proof of merchant approval or callback verification.
            profile.verified = False
            profile.enabled = False
            profile.save()
            messages.warning(request, 'Payment details saved securely. Direct payment remains disabled until provider approval, webhook/IPN verification, and an end-to-end test are completed.')
            return redirect('marketplace:seller_payment_settings', store_id=store.pk)
    return render(request, 'marketplace/seller_payment_settings.html', {'store': store, 'profile': profile})

def _store_trending(store, available_qs, limit=8):
    """Trending products for ONE shop, shown at the top of its public page.

    Ranked by units ordered (cancelled / failed / refunded orders ignored) over the last 30 days,
    widening to 90 days for quiet shops. When a shop has no real sales yet we fall back to its
    featured / best-rated products and say so honestly ("Top picks"), never pretending they sell fast.
    Only product ids are cached (5 min); products are re-read so price and stock are always current.
    """
    def build():
        valid = (~Q(orderitem__order__status=Order.Status.CANCELLED)
                 & ~Q(orderitem__order__payment_status__in=(Order.PaymentStatus.FAILED, Order.PaymentStatus.REFUNDED)))
        for days in (30, 90):
            since = timezone.now() - timedelta(days=days)
            rows = list(available_qs.filter(orderitem__order__created_at__gte=since)
                        .annotate(sold=Sum('orderitem__quantity', filter=valid))
                        .filter(sold__gt=0).order_by('-sold', '-rating_avg', '-created_at')
                        .values_list('pk', 'sold')[:limit])
            if rows:
                return {'mode': 'sales', 'days': days, 'rows': [(pk, int(n)) for pk, n in rows]}
        rows = list(available_qs.order_by('-featured', '-rating_avg', '-rating_count', '-created_at').values_list('pk', flat=True)[:limit])
        return {'mode': 'picks', 'days': 0, 'rows': [(pk, 0) for pk in rows]}
    data = cache.get_or_set(f'marketplace:store-trending:{store.pk}', build, 300)
    ids = [pk for pk, _ in data['rows']]
    by_id = available_qs.filter(pk__in=ids).in_bulk(ids)
    items = []
    for pk, sold in data['rows']:
        product = by_id.get(pk)
        if product is not None:          # sold out / removed since the cache was built -> silently skipped
            product.trend_sold = sold
            items.append(product)
    return items, data['mode'], data['days']

def seller_store_public(request, slug):
    """The link a manager shares: only this shop's products, categories and contact details."""
    store = SellerStore.objects.select_related('pharmacy').filter(slug=slug).first()
    selling = bool(store and store.is_published and store.pharmacy.is_active and _online_selling_active(store))
    has_listings = bool(store and store.pharmacy.is_active and store.listings.filter(status__in=('PUBLISHED', 'RESERVED', 'CLOSED')).exists())
    available = selling or has_listings
    if not available:
        cart = cart_data(request)
        return render(request, 'marketplace/shop_unavailable.html', {'store': store, 'cart_count': sum(cart.values())}, status=404)
    products = (Product.objects.filter(seller_store=store, is_active=True, archived_at__isnull=True, category__is_active=True, category__seller_store=store).filter(AVAILABLE_Q)
                .select_related('category', 'seller_store', 'seller_store__pharmacy').defer(*_LIST_DEFER))
    q = request.GET.get('q', '').strip()[:100]
    category_slug = request.GET.get('category', '').strip()[:120]
    store_categories = list(Category.objects.filter(seller_store=store, is_active=True)
                            .filter(Exists(Product.objects.filter(category=OuterRef('pk'), is_active=True).filter(AVAILABLE_Q)))
                            .only('name', 'slug').order_by('name'))
    if category_slug and any(c.slug == category_slug for c in store_categories):
        products = products.filter(category__slug=category_slug)
    else:
        category_slug = ''
    if q:
        products = products.filter(Q(name__icontains=q) | Q(description__icontains=q))
    if not selling:
        products = products.none()   # listing-only seller: no shop products on a free profile
    trending, trending_mode, trending_days = [], '', 0
    if selling and not q and not category_slug and request.GET.get('page', '1') in ('', '1'):
        try:
            base_qs = (Product.objects.filter(seller_store=store, is_active=True, archived_at__isnull=True,
                                              category__is_active=True, category__seller_store=store)
                       .filter(AVAILABLE_Q).select_related('category', 'seller_store', 'seller_store__pharmacy').defer(*_LIST_DEFER))
            trending, trending_mode, trending_days = _store_trending(store, base_qs)
        except Exception:
            logger.exception('Store trending failed for store %s', store.pk)   # never break the shop page
            trending = []
    page = Paginator(products.order_by('-featured', '-created_at'), 24).get_page(request.GET.get('page'))
    cart = cart_data(request)
    other_shop_in_cart = False
    if cart:
        shops = set(Product.objects.filter(pk__in=[int(k) for k in cart]).values_list('seller_store_id', flat=True))
        other_shop_in_cart = bool(shops) and shops != {store.pk}
    return render(request, 'marketplace/seller_store_public.html', {
        'trending': trending, 'trending_mode': trending_mode, 'trending_days': trending_days,
        'store': store, 'selling': selling, 'products': page, 'cart_count': sum(cart.values()), 'cart': cart,
        'store_categories': store_categories, 'selected_category': category_slug, 'q': q,
        'other_shop_in_cart': other_shop_in_cart, 'contact_phone': store.pharmacy.phone_number,
        'contact_wa': normalize_social('whatsapp', store.whatsapp_url)[0] or '',
        'store_listings': list(store.listings.filter(status__in=('PUBLISHED', 'RESERVED', 'CLOSED')).prefetch_related('images').select_related('store').order_by('-published_at')[:12]),
    })
