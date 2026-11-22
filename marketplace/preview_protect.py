"""Protection for the PUBLIC free sample of a digital product.

A PDF that a browser can display can also be saved, so the only real protection is to make sure that
what the browser receives is *only* a limited, watermarked sample, never the file the seller uploaded.
The paid file is never touched: after payment the buyer receives the seller's original, with no watermark.

For every PDF sample the server builds a separate copy that:
  * keeps only the first PREVIEW_PDF_MAX_PAGES pages (default 5),
  * carries a subtle SmartPOS Marketplace brand pattern, a small "FREE SAMPLE" pill and a slim footer on every page,
  * has no links, scripts, attachments, forms or original metadata,
  * is AES-256 encrypted with a random owner password and no print / copy / edit permissions.

The built copy is cached in PRIVATE storage and is the only thing `preview_views.preview_stream` ever serves.
If a copy cannot be built (broken or unsupported PDF) nothing is served: the view fails closed.
"""
import hashlib
import io
import logging
import secrets

from django.conf import settings
from django.core.files.base import ContentFile

from .storage import private_storage

log = logging.getLogger(__name__)

CACHE_VERSION = 'v2'   # bump when the watermark design changes so cached samples are rebuilt


class PreviewUnavailable(Exception):
    """The protected sample could not be produced."""


def max_pages():
    try:
        return max(1, int(getattr(settings, 'PREVIEW_PDF_MAX_PAGES', 5)))
    except (TypeError, ValueError):
        return 5


def _brand_mark(c, x, y, r):
    """Small SmartPOS dot-ring logo (8 dots around a centre dot) drawn at (x, y) with outer radius r."""
    import math
    for i in range(8):
        a = math.radians(i * 45)
        c.circle(x + math.cos(a) * r, y + math.sin(a) * r, r * 0.2, stroke=0, fill=1)
    c.circle(x, y, r * 0.26, stroke=0, fill=1)


def _watermark_page(width, height, footer, number=1, total=1):
    """One transparent PDF page carrying the SmartPOS Marketplace brand pattern.

    * a fine, staggered, rotated tile pattern (logo mark + "SmartPOS Marketplace") at very low opacity, so the
      page stays comfortably readable but the mark cannot be cropped out or cloned away;
    * a small "FREE SAMPLE" pill in the top-right corner;
    * a slim footer strip with the brand on the left and the unlock note on the right.
    """
    from pypdf import PdfReader
    from reportlab.pdfgen import canvas

    teal = (0.059, 0.463, 0.431)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height), pageCompression=1)

    def alpha(v):
        try:
            c.setFillAlpha(v)
            c.setStrokeAlpha(v)
        except Exception:
            pass

    # 1) tiled brand pattern: small, even, rotated -30 degrees, every other row shifted by half a tile
    label = 'SmartPOS Marketplace'
    size = max(9.0, min(width, height) * 0.021)
    tile_w, tile_h = size * 17.0, size * 9.5
    c.saveState()
    c.translate(width / 2, height / 2)
    c.rotate(30)
    c.setFillColorRGB(*teal)
    alpha(0.09)
    c.setFont('Helvetica-Bold', size)
    import math
    span = math.hypot(width, height) / 2 + tile_w
    cos_a, sin_a = math.cos(math.radians(30)), math.sin(math.radians(30))
    margin = tile_w * 0.6
    row, y = 0, -span
    while y < span:
        x = -span + (tile_w / 2 if row % 2 else 0)
        while x < span:
            # position on the page after the rotation: draw only tiles that can actually be seen
            px = width / 2 + x * cos_a - y * sin_a
            py = height / 2 + x * sin_a + y * cos_a
            if -margin <= px <= width + margin and -margin <= py <= height + margin:
                _brand_mark(c, x + size * 0.6, y + size * 0.34, size * 0.62)
                c.drawString(x + size * 1.7, y, label)
            x += tile_w
        y += tile_h
        row += 1
    c.restoreState()

    # 2) corner pill (top right) and a hairline ribbon across the top
    c.saveState()
    c.setFillColorRGB(*teal)
    c.rect(0, height - 3, width, 3, stroke=0, fill=1)
    pill_w, pill_h = 74.0, 17.0
    px, py = width - pill_w - 14, height - pill_h - 14
    alpha(0.92)
    c.roundRect(px, py, pill_w, pill_h, pill_h / 2, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    alpha(1)
    c.setFont('Helvetica-Bold', 7.2)
    c.drawCentredString(px + pill_w / 2, py + 5.6, 'FREE SAMPLE')
    c.restoreState()

    # 3) slim footer strip
    c.saveState()
    c.setFillColorRGB(0.965, 0.98, 0.977)
    alpha(0.96)
    c.rect(0, 0, width, 20, stroke=0, fill=1)
    alpha(1)
    c.setFillColorRGB(*teal)
    c.rect(0, 20, width, 0.8, stroke=0, fill=1)
    _brand_mark(c, 20, 10, 4.4)
    c.setFont('Helvetica-Bold', 7.6)
    c.drawString(29, 7.6, 'SmartPOS Marketplace')
    c.setFillColorRGB(0.28, 0.33, 0.38)
    c.setFont('Helvetica', 7.6)
    c.drawRightString(width - 16, 7.6, '%s   |   Preview page %d of %d' % (footer, number, total))
    c.restoreState()

    c.showPage()
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]


def build_sample_pdf(raw, title='Free sample'):
    """Return the protected sample (bytes) built from the seller's uploaded PDF bytes."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.constants import UserAccessPermissions

    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted and not reader.decrypt(''):
            raise PreviewUnavailable('encrypted sample')
        total = len(reader.pages)
        if total < 1:
            raise PreviewUnavailable('empty pdf')
        writer = PdfWriter()
        footer = 'The full, watermark-free file unlocks after payment'
        pages = list(reader.pages)[:max_pages()]
        for number, page in enumerate(pages, 1):
            for key in ('/Annots', '/AA', '/Metadata'):         # links, page actions, embedded metadata
                if key in page:
                    del page[key]
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            page.merge_page(_watermark_page(width, height, footer, number, len(pages)))
            writer.add_page(page)
            writer.pages[-1].compress_content_streams()
        writer.add_metadata({'/Title': (title or 'Free sample')[:120], '/Producer': 'SmartPOS', '/Creator': 'SmartPOS'})
        writer.encrypt(user_password='', owner_password=secrets.token_hex(24),
                       permissions_flag=UserAccessPermissions(0), algorithm='AES-256')
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except PreviewUnavailable:
        raise
    except Exception as exc:                                    # malformed / unsupported PDF
        log.warning('Could not build protected sample: %s', exc)
        raise PreviewUnavailable(str(exc))


def protected_sample(product):
    """Return the bytes of the protected sample of `product`, building and caching it when needed."""
    stored = product.preview_file
    if not stored:
        raise PreviewUnavailable('no sample')
    key = hashlib.sha1(('%s|%s|%s|%s' % (CACHE_VERSION, product.pk, stored.name, max_pages())).encode()).hexdigest()
    name = 'preview-cache/%s.pdf' % key
    cache = private_storage()
    try:
        if cache.exists(name):
            with cache.open(name, 'rb') as fh:
                return fh.read()
    except Exception as exc:
        log.warning('Sample cache read failed: %s', exc)
    try:
        with stored.storage.open(stored.name, 'rb') as fh:
            raw = fh.read()
    except (FileNotFoundError, OSError) as exc:
        raise PreviewUnavailable(str(exc))
    data = build_sample_pdf(raw, product.name)
    try:
        cache.save(name, ContentFile(data))
    except Exception as exc:                                    # caching is an optimisation only
        log.warning('Sample cache write failed: %s', exc)
    return data
