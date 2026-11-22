"""Validation helpers for digital products and listing photos."""
import os

DIGITAL_MAX_BYTES = 1024 * 1024 * 1024  # 1 GB per digital file; hosting/proxy upload limits may be lower
# extension -> label shown to buyers
DIGITAL_TYPES = {
    '.pdf': 'PDF', '.epub': 'EPUB', '.mp4': 'MP4 video', '.m4v': 'MP4 video',
    '.mov': 'MOV video', '.webm': 'WebM video', '.mkv': 'Matroska video', '.avi': 'AVI video',
    '.3gp': '3GP video', '.mpeg': 'MPEG video', '.mpg': 'MPEG video', '.mp3': 'MP3 audio',
    '.zip': 'ZIP', '.doc': 'Word', '.docx': 'Word', '.ppt': 'PowerPoint', '.pptx': 'PowerPoint',
    '.xls': 'Excel', '.xlsx': 'Excel',
}
DIGITAL_ACCEPT = ','.join(DIGITAL_TYPES)

IMAGE_EXT = ('.jpg', '.jpeg', '.jfif', '.jpe', '.png', '.webp')


def _head(f, n=16):
    f.seek(0)
    data = f.read(n)
    f.seek(0)
    return data


def validate_digital_upload(f):
    """Return an error message, or None when the upload looks like what its extension claims.

    The extension alone is never trusted: the first bytes (magic number) must match the format.
    """
    name = (getattr(f, 'name', '') or '')
    ext = os.path.splitext(name)[1].lower()
    if ext not in DIGITAL_TYPES:
        return 'Allowed file types: ' + ', '.join(sorted({v for v in DIGITAL_TYPES.values()})) + '.'
    size = getattr(f, 'size', 0) or 0
    if size <= 0:
        return 'The file is empty.'
    if size > DIGITAL_MAX_BYTES:
        return 'The file is larger than %d MB.' % (DIGITAL_MAX_BYTES // (1024 * 1024))
    head = _head(f, 16)
    if ext == '.pdf' and not head.startswith(b'%PDF'):
        return 'This does not look like a real PDF file.'
    if ext in ('.zip', '.epub', '.docx', '.pptx', '.xlsx') and not head.startswith(b'PK'):
        return 'This file is not a valid %s file.' % DIGITAL_TYPES[ext]
    if ext in ('.doc', '.ppt', '.xls') and not head.startswith(b'\xd0\xcf\x11\xe0'):
        return 'This file is not a valid %s file.' % DIGITAL_TYPES[ext]
    if ext in ('.mp4', '.m4v', '.mov', '.3gp') and head[4:8] != b'ftyp':
        return 'This does not look like a valid MP4/MOV/3GP video.'
    if ext in ('.webm', '.mkv') and not head.startswith(b'\x1aE\xdf\xa3'):
        return 'This does not look like a valid WebM/Matroska video.'
    if ext == '.avi' and not (head.startswith(b'RIFF') and head[8:12] == b'AVI '):
        return 'This does not look like a valid AVI video.'
    if ext in ('.mpeg', '.mpg') and not (head.startswith(b'\x00\x00\x01\xba') or head.startswith(b'\x00\x00\x01\xb3')):
        return 'This does not look like a valid MPEG video.'
    if ext == '.mp3' and not (head.startswith(b'ID3') or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)):
        return 'This does not look like a real MP3 file.'
    return None


def normalise_image_name(f):
    """Treat .jfif / .jpe as the JPEG files they are, so storage and browsers serve them as image/jpeg."""
    name = (getattr(f, 'name', '') or '')
    base, ext = os.path.splitext(name)
    if ext.lower() in ('.jfif', '.jpe'):
        try:
            f.name = base + '.jpg'
        except Exception:
            pass
    return f


# --------------------------------------------------------------------------------------------
# Free preview (sample) of a digital product. The sample is PUBLIC on purpose, so it must be a
# short extract (a few pages, a few minutes), never the paid file.
# --------------------------------------------------------------------------------------------
PREVIEW_TYPES = {'.pdf': 'application/pdf', '.mp3': 'audio/mpeg', '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.webm': 'video/webm'}
PREVIEW_ACCEPT = ','.join(PREVIEW_TYPES)
PREVIEW_MAX_BYTES = {'pdf': 15 * 1024 * 1024, 'audio': 15 * 1024 * 1024, 'video': 80 * 1024 * 1024}
WHATS_INSIDE_MAX_ITEMS = 12
WHATS_INSIDE_MAX_LEN = 110


def preview_kind(name):
    """'pdf', 'audio', 'video' or '' for a preview file name."""
    ext = os.path.splitext(name or '')[1].lower()
    if ext == '.pdf':
        return 'pdf'
    if ext == '.mp3':
        return 'audio'
    if ext in ('.mp4', '.m4v', '.webm'):
        return 'video'
    return ''


def preview_content_type(name):
    return PREVIEW_TYPES.get(os.path.splitext(name or '')[1].lower(), 'application/octet-stream')


def validate_preview_upload(f, full_size=None):
    """Return an error message, or None when the file is a believable free sample.

    `full_size` is the size in bytes of the paid file when known: a sample that is not smaller
    than the paid file is rejected, so the full product is not given away by mistake.
    """
    name = getattr(f, 'name', '') or ''
    ext = os.path.splitext(name)[1].lower()
    if ext not in PREVIEW_TYPES:
        return 'The free preview must be a PDF, MP3, MP4 or WebM file.'
    size = getattr(f, 'size', 0) or 0
    if size <= 0:
        return 'The preview file is empty.'
    kind = preview_kind(name)
    limit = PREVIEW_MAX_BYTES[kind]
    if size > limit:
        return 'The preview is larger than %d MB. Upload a short sample, not the full product.' % (limit // (1024 * 1024))
    if full_size and size >= full_size:
        return 'The preview must be smaller than the paid file. Upload a short sample, not the full product.'
    if full_size and kind != 'pdf' and size > full_size * 0.4:
        return 'The preview must be a short extract (under 40% of the paid file). Upload a short clip, not the full product.'
    head = _head(f, 16)
    if ext == '.pdf' and not head.startswith(b'%PDF'):
        return 'This does not look like a real PDF file.'
    if ext in ('.mp4', '.m4v') and head[4:8] != b'ftyp':
        return 'This does not look like a valid MP4 video.'
    if ext == '.webm' and not head.startswith(b'\x1aE\xdf\xa3'):
        return 'This does not look like a valid WebM video.'
    if ext == '.mp3' and not (head.startswith(b'ID3') or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)):
        return 'This does not look like a real MP3 file.'
    return None


def parse_whats_inside(text, limit=WHATS_INSIDE_MAX_ITEMS, max_len=WHATS_INSIDE_MAX_LEN):
    """Turn the seller's "one item per line" text into a clean list for the checklist."""
    items, seen = [], set()
    for raw in (text or '').replace('\r', '\n').split('\n'):
        line = ' '.join(raw.split()).lstrip('-*\u2022\u2713\u2714\u00b7 ').strip()
        if not line:
            continue
        line = line[:max_len].rstrip()
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(line)
        if len(items) >= limit:
            break
    return items
