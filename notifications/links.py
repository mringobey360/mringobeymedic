"""Safe handling of the optional link attached to an announcement (V54.71)."""
from urllib.parse import urlparse

LABEL_DEFAULT = 'Open link'
MAX_URL = 500
MAX_LABEL = 40


def clean_action_url(raw):
    """Return a safe URL or ''.

    Allowed: absolute http(s) URLs with a host, and in-app paths ("/sales/history/").
    Everything else (javascript:, data:, protocol-relative //host, mailto:, ...) is rejected.
    """
    value = (raw or '').strip()
    if not value or len(value) > MAX_URL or any(c in value for c in '\r\n\t '):
        return ''
    if value.startswith('/') and not value.startswith('//') and '\\' not in value:
        return value
    parsed = urlparse(value)
    if parsed.scheme in ('http', 'https') and parsed.netloc and '@' not in parsed.netloc:
        return value
    return ''


def is_external(url):
    return bool(url) and not url.startswith('/')
