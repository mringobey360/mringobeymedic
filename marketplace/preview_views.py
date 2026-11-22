"""Free preview (sample) of digital products.

Only a protected SAMPLE is public. The paid file stays private and is reached through
`digital_views.download_file` after a verified payment.

PDF samples:
* are never served as uploaded: `preview_protect` builds a short, watermarked, restricted copy;
* are shown in the SmartPOS viewer (PDF.js on a canvas): no browser PDF toolbar, so no Download / Print button;
* the PDF bytes are reachable only through `preview_file`, which needs a short-lived signed token AND a custom
  request header that only the viewer script sends. A link, a new tab, an <iframe> or a service-worker
  navigation can therefore never load the PDF directly (this does not depend on Sec-Fetch-* headers,
  which the site's service worker strips).
Requests are rate-limited per IP.
"""
import os
from urllib.parse import quote

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.http import Http404, HttpResponse, HttpResponseRedirect, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_http_methods

from .digital import preview_content_type
from .models import Product
from .movie_views import _iter_range
from .movies import parse_range
from .preview_protect import PreviewUnavailable, max_pages, protected_sample

TOKEN_SALT = 'marketplace.preview.file'
TOKEN_MAX_AGE = 600       # seconds a viewer page may keep asking for its PDF
VIEWER_HEADER = 'X-PV-Viewer'
PDFJS_BASE = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174'
RATE_LIMIT = 90          # requests ...
RATE_WINDOW = 60         # ... per minute, per IP


def _public_previews():
    from .views import _public_marketplace_products
    return (_public_marketplace_products()
            .filter(kind=Product.Kind.DIGITAL, is_movie=False)
            .exclude(preview_file=''))


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return (forwarded.split(',')[0].strip() or request.META.get('REMOTE_ADDR', '') or 'unknown')[:64]


def _rate_limited(request):
    key = 'pv-rate:%s' % _client_ip(request)
    try:
        cache.add(key, 0, RATE_WINDOW)
        return cache.incr(key) > RATE_LIMIT
    except Exception:                    # a broken cache must never block real buyers
        return False


def _private(response):
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['X-Content-Type-Options'] = 'nosniff'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def make_token(slug):
    return signing.dumps({'s': slug}, salt=TOKEN_SALT, compress=False)


def _token_ok(token, slug):
    try:
        return signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE).get('s') == slug
    except signing.BadSignature:
        return False


@xframe_options_sameorigin   # the site sends X-Frame-Options: DENY; the preview viewer embeds the PDF from the same site
@require_http_methods(['GET', 'HEAD'])
def preview_stream(request, slug):
    """Serve the free sample inline. PDFs are served only as the protected copy."""
    product = get_object_or_404(_public_previews(), slug=slug)
    if _rate_limited(request):
        response = HttpResponse('Too many requests. Please wait a minute and try again.', status=429, content_type='text/plain')
        response['Retry-After'] = str(RATE_WINDOW)
        return _private(response)
    stored = product.preview_file
    content_type = preview_content_type(stored.name)

    if content_type == 'application/pdf':
        # PDFs are never sent from this URL: it returns the SmartPOS viewer page, which then fetches the protected copy.
        if request.method == 'HEAD':
            return _private(HttpResponse(content_type='text/html; charset=utf-8'))
        return _private(render(request, 'marketplace/preview_page.html', {
            'product': product, 'sample_pages': max_pages(), 'embed': request.GET.get('embed') == '1',
            'file_url': reverse('marketplace:preview_file', args=[product.slug, make_token(product.slug)]),
            'pdfjs_base': getattr(settings, 'PREVIEW_PDFJS_BASE', PDFJS_BASE).rstrip('/'),
        }))

    # Audio / video samples: short clips, streamed with Range support (needed by Safari/iPhone).
    storage = stored.storage
    if not isinstance(storage, FileSystemStorage):
        return HttpResponseRedirect(storage.url(stored.name))   # S3 / R2 already honour Range requests
    try:
        size = storage.size(stored.name)
        handle = storage.open(stored.name, 'rb')
    except (FileNotFoundError, OSError):
        raise Http404('Preview not found.')
    rng = parse_range(request.headers.get('Range'), size)
    if rng == 'unsatisfiable':
        handle.close()
        response = HttpResponse(status=416)
        response['Content-Range'] = 'bytes */%d' % size
        response['Accept-Ranges'] = 'bytes'
        return response
    if rng is None:
        start, end, status = 0, size - 1, 200
    else:
        (start, end), status = rng, 206
    length = max(0, end - start + 1)
    if request.method == 'HEAD':
        handle.close()
        response = HttpResponse(status=status, content_type=content_type)
    else:
        response = StreamingHttpResponse(_iter_range(handle, start, length), status=status, content_type=content_type)
    response['Content-Disposition'] = "inline; filename*=UTF-8''" + quote('preview' + os.path.splitext(stored.name)[1].lower())
    response['Content-Length'] = str(length)
    response['Accept-Ranges'] = 'bytes'
    if status == 206:
        response['Content-Range'] = 'bytes %d-%d/%d' % (start, end, size)
    return _private(response)


@xframe_options_sameorigin
@require_http_methods(['GET'])
def preview_file(request, slug, token):
    """The protected sample PDF, for the viewer script only (signed token + custom header, never a plain navigation)."""
    product = get_object_or_404(_public_previews(), slug=slug)
    if (request.headers.get(VIEWER_HEADER) != '1' or not _token_ok(token, slug)
            or request.headers.get('Sec-Fetch-Dest', 'empty') in ('document', 'iframe', 'frame', 'embed', 'object')):
        return _private(HttpResponse('Forbidden', status=403, content_type='text/plain'))
    if _rate_limited(request):
        response = HttpResponse('Too many requests. Please wait a minute and try again.', status=429, content_type='text/plain')
        response['Retry-After'] = str(RATE_WINDOW)
        return _private(response)
    if preview_content_type(product.preview_file.name) != 'application/pdf':
        raise Http404('Preview not available.')
    try:
        data = protected_sample(product)
    except PreviewUnavailable:
        raise Http404('Preview not available.')
    response = HttpResponse(data, content_type='application/pdf')
    response['Content-Length'] = str(len(data))
    response['Content-Disposition'] = 'inline'
    return _private(response)
