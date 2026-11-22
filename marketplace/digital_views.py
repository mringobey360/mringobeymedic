"""Buyer downloads for paid digital products, plus the seller's "what are you listing?" chooser."""
import mimetypes
import os
from urllib.parse import quote

from django.contrib.auth.decorators import login_required
from django.core.files.storage import FileSystemStorage
from django.db.models import F
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import DownloadGrant, Product


def _no_cache(response):
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def serve_private_file(product):
    """Stream the product's private file as an attachment (local disk) or redirect to a 5-minute signed URL (S3/R2).

    Returns None when the file is missing from storage.
    """
    stored = product.digital_file
    if not stored:
        return None
    filename = (product.digital_file_name or os.path.basename(stored.name) or 'download').replace('"', '')
    storage = stored.storage
    if isinstance(storage, FileSystemStorage):
        try:
            handle = storage.open(stored.name, 'rb')
        except (FileNotFoundError, OSError):
            return None
        content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        return _no_cache(FileResponse(handle, as_attachment=True, filename=filename, content_type=content_type))
    url = storage.url(stored.name, expire=300,
                      parameters={'ResponseContentDisposition': "attachment; filename*=UTF-8''" + quote(filename)})
    return _no_cache(HttpResponseRedirect(url))


@csrf_exempt
@require_http_methods(['GET', 'HEAD', 'POST'])
def download_file(request, token):
    """Serve a paid digital download through an unguessable bearer token.

    This endpoint is intentionally CSRF-exempt: the token in the URL is the
    capability credential for the download, and the endpoint never relies on
    the buyer's authenticated session. Every POST still re-verifies payment,
    revocation, the remaining allowance, and the private file before atomically
    consuming one download. This also supports mobile/webview clients that send
    ``Origin: null`` on the download form submission.

    GET/HEAD only render the page and never consume the allowance.
    """
    grant = get_object_or_404(
        DownloadGrant.objects.select_related('order_item__order__seller_store__pharmacy', 'order_item__product'),
        token=token)
    item = grant.order_item
    product = item.product
    order = item.order
    ctx = {'grant': grant, 'item': item, 'product': product, 'order': order, 'store': order.seller_store,
           'cart_count': 0, 'state': 'ready'}
    if grant.revoked or order.status == order.Status.CANCELLED or order.payment_status != order.PaymentStatus.PAID:
        ctx['state'] = 'blocked'
        return _no_cache(render(request, 'marketplace/download.html', ctx, status=403))
    if grant.remaining <= 0:
        ctx['state'] = 'limit'
        return _no_cache(render(request, 'marketplace/download.html', ctx, status=403))
    if product is None or not product.digital_file:
        ctx['state'] = 'missing'
        return _no_cache(render(request, 'marketplace/download.html', ctx, status=404))
    if request.method != 'POST':
        return _no_cache(render(request, 'marketplace/download.html', ctx))

    # Claim one download atomically so concurrent clicks cannot exceed the limit.
    claimed = DownloadGrant.objects.filter(pk=grant.pk, revoked=False, download_count__lt=F('max_downloads')).update(
        download_count=F('download_count') + 1, last_downloaded_at=timezone.now())
    if not claimed:
        ctx['state'] = 'limit'
        return _no_cache(render(request, 'marketplace/download.html', ctx, status=403))
    try:
        response = serve_private_file(product)
    except Exception:
        # Storage/signing failures must not charge the buyer a download attempt.
        DownloadGrant.objects.filter(pk=grant.pk, download_count__gt=0).update(download_count=F('download_count') - 1)
        ctx['state'] = 'storage_error'
        return _no_cache(render(request, 'marketplace/download.html', ctx, status=503))
    if response is None:
        DownloadGrant.objects.filter(pk=grant.pk, download_count__gt=0).update(download_count=F('download_count') - 1)
        ctx['state'] = 'missing'
        return _no_cache(render(request, 'marketplace/download.html', ctx, status=404))
    return response


@login_required
def seller_product_file(request, store_id, pk):
    """Let the seller check the file they uploaded (does not use any buyer's allowance)."""
    from .views import _seller_store_for_user
    store = _seller_store_for_user(request, store_id)
    product = get_object_or_404(Product, pk=pk, seller_store=store, kind=Product.Kind.DIGITAL)
    response = serve_private_file(product)
    if response is None:
        raise Http404('The file is missing from storage. Upload it again.')
    return response


@login_required
def seller_new_listing(request, store_id):
    """Step 1 of listing something: choose what it is, so the next form shows only what matters."""
    from .views import _seller_store_for_user
    store = _seller_store_for_user(request, store_id)
    return render(request, 'marketplace/seller_new_listing.html', {'store': store})
