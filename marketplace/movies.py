"""Movie products: trailer validation, HTTP Range support and card metadata helpers.

This module deliberately does not import Django so it can be unit-tested on its own.

A movie is a normal DIGITAL product (private file + DownloadGrant after payment) with extra
presentation fields. The TRAILER is a different file: it is public on purpose, small, and is
streamed with HTTP Range support so it plays (and can be seeked) on Safari / iPhone, which
refuse to play video from servers that ignore Range requests.
"""
import os
import re

TRAILER_MAX_BYTES = 80 * 1024 * 1024          # 80 MB: a trailer, never the full movie
TRAILER_TYPES = {'.mp4': 'video/mp4', '.m4v': 'video/mp4', '.webm': 'video/webm'}
TRAILER_ACCEPT = ','.join(TRAILER_TYPES)
# The paid file of a movie must be a video.
MOVIE_FILE_EXT = ('.mp4', '.m4v', '.mov', '.webm', '.mkv', '.avi', '.3gp', '.mpeg', '.mpg')

QUALITY_CHOICES = [('', 'Not specified'), ('SD', 'SD'), ('HD', 'HD 720p'), ('FHD', 'Full HD 1080p'), ('UHD', '4K')]

MOVIE_YEAR_MIN = 1900
MOVIE_YEAR_MAX = 2100
MOVIE_MAX_MINUTES = 1000


def validate_trailer_upload(f):
    """Return an error message, or None when the file really is a short MP4/WebM trailer."""
    name = getattr(f, 'name', '') or ''
    ext = os.path.splitext(name)[1].lower()
    if ext not in TRAILER_TYPES:
        return 'The trailer must be an MP4 or WebM video.'
    size = getattr(f, 'size', 0) or 0
    if size <= 0:
        return 'The trailer file is empty.'
    if size > TRAILER_MAX_BYTES:
        return 'The trailer is larger than %d MB. Upload a short trailer, not the full movie.' % (TRAILER_MAX_BYTES // (1024 * 1024))
    f.seek(0)
    head = f.read(16)
    f.seek(0)
    if ext in ('.mp4', '.m4v') and head[4:8] != b'ftyp':
        return 'This does not look like a valid MP4 video.'
    if ext == '.webm' and not head.startswith(b'\x1aE\xdf\xa3'):
        return 'This does not look like a valid WebM video.'
    return None


def trailer_content_type(name):
    return TRAILER_TYPES.get(os.path.splitext(name or '')[1].lower(), 'application/octet-stream')


_RANGE_RE = re.compile(r'^bytes=(\d*)-(\d*)$')


def parse_range(header, size):
    """Parse a single-range ``Range`` header.

    Returns ``None`` when there is no usable header (serve the whole file with 200),
    ``(start, end)`` inclusive for a satisfiable range (serve 206),
    or ``'unsatisfiable'`` (serve 416). Multi-range requests are ignored (whole file).
    """
    if not header or size <= 0:
        return None
    m = _RANGE_RE.match(header.strip())
    if not m:
        return None
    first, last = m.group(1), m.group(2)
    if not first and not last:
        return None
    if not first:                      # suffix range: the last N bytes
        n = int(last)
        if n == 0:
            return 'unsatisfiable'
        return (max(0, size - n), size - 1)
    start = int(first)
    if start >= size:
        return 'unsatisfiable'
    end = int(last) if last else size - 1
    if end < start:
        return None
    return (start, min(end, size - 1))


def clean_year(value):
    """Return a sane release year or None."""
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if MOVIE_YEAR_MIN <= year <= MOVIE_YEAR_MAX else None


def format_duration(minutes):
    """90 -> '1h 30m', 45 -> '45m', 120 -> '2h'. Empty for missing/invalid values."""
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return ''
    if minutes <= 0:
        return ''
    h, m = divmod(minutes, 60)
    if h and m:
        return '%dh %dm' % (h, m)
    return '%dh' % h if h else '%dm' % m


def split_genres(text, limit=4):
    """'Action, Drama ,, Comedy' -> ['Action', 'Drama', 'Comedy'] (max ``limit``)."""
    seen, out = set(), []
    for part in (text or '').split(','):
        g = ' '.join(part.split())[:30]
        if g and g.lower() not in seen:
            seen.add(g.lower())
            out.append(g)
    return out[:limit]
