"""Public movie pages: the Movies catalogue and the trailer stream.

Only the TRAILER is public. The full movie is a private digital file that buyers reach through
`digital_views.download_file` after a verified payment.
"""
from collections import Counter

from django.core.files.storage import FileSystemStorage
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseRedirect, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from .models import Product
from .movies import parse_range, split_genres, trailer_content_type

CHUNK = 64 * 1024


def _iter_range(handle, start, length):
    """Yield ``length`` bytes of ``handle`` beginning at ``start``; always closes the file."""
    try:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        handle.close()


def _public_movies():
    from .views import _public_marketplace_products
    return (_public_marketplace_products()
            .filter(is_movie=True, kind=Product.Kind.DIGITAL)
            .exclude(digital_file=''))


@require_http_methods(['GET', 'HEAD'])
def trailer_stream(request, slug):
    """Stream a movie trailer with HTTP Range support (needed by Safari/iPhone and for seeking)."""
    product = get_object_or_404(_public_movies().exclude(trailer_file=''), slug=slug)
    stored = product.trailer_file
    storage = stored.storage
    if not isinstance(storage, FileSystemStorage):
        # S3 / R2 already honour Range requests; let the bucket serve the bytes.
        return HttpResponseRedirect(storage.url(stored.name))
    try:
        size = storage.size(stored.name)
        handle = storage.open(stored.name, 'rb')
    except (FileNotFoundError, OSError):
        raise Http404('Trailer not found.')
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
    content_type = trailer_content_type(stored.name)
    if request.method == 'HEAD':
        handle.close()
        response = HttpResponse(status=status, content_type=content_type)
    else:
        response = StreamingHttpResponse(_iter_range(handle, start, length), status=status, content_type=content_type)
    response['Content-Length'] = str(length)
    response['Accept-Ranges'] = 'bytes'
    if status == 206:
        response['Content-Range'] = 'bytes %d-%d/%d' % (start, end, size)
    response['Cache-Control'] = 'public, max-age=3600'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def movies_page(request):
    """The Movies shop window: search, genre filter and cinema-style cards."""
    from .views import _LIST_DEFER, cart_data
    base = _public_movies()
    q = request.GET.get('q', '').strip()[:100]
    genre = request.GET.get('genre', '').strip()[:30]
    qs = base.select_related('category', 'seller_store', 'seller_store__pharmacy').defer(*_LIST_DEFER)
    if q:
        for word in q.split()[:5]:
            qs = qs.filter(Q(name__icontains=word) | Q(movie_genres__icontains=word))
    if genre:
        qs = qs.filter(movie_genres__icontains=genre)
    counter = Counter()
    for text in base.values_list('movie_genres', flat=True)[:500]:
        counter.update(g for g in split_genres(text, limit=6))
    genres = [name for name, _ in counter.most_common(12)]
    page = Paginator(qs.order_by('-featured', '-created_at'), 24).get_page(request.GET.get('page'))
    cart = cart_data(request)
    return render(request, 'marketplace/movies.html', {
        'products': page, 'genres': genres, 'q': q, 'selected_genre': genre,
        'cart_count': sum(cart.values()), 'cart': cart})
