"""Role-aware destinations for notification clicks.

A notification is stored once, but the person who opens it may be an owner, a
Manager Assistant, an employee or a branch display account. This module makes
sure a click always lands on a page that account can actually open, instead of
the "Access restricted" screen.
"""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.urls import Resolver404, resolve, reverse

from accounts.decorators import user_can_access_view


def role_home_url(user):
    """The landing page that is always valid for this account type."""
    try:
        if getattr(user, 'is_branch_display_role', False):
            return reverse('accounts:attendance_qr_kiosk')
        return reverse('dashboard:home')
    except Exception:
        return '/'


def _internal_path(raw):
    raw = (raw or '').strip()
    if not raw.startswith('/') or raw.startswith('//'):
        return None
    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        return None
    return parts


def accessible_destination(user, raw, mark_click=True):
    """Return ``raw`` (kept inside the app) if ``user`` may open it, else ``None``.

    ``None`` means: the URL is empty, external, no longer exists, or is gated to
    a role the user does not have.
    """
    parts = _internal_path(raw)
    if parts is None:
        return None
    try:
        match = resolve(parts.path)
    except Resolver404:
        return None
    if not user_can_access_view(user, match.func):
        return None
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if mark_click:
        # The global 403 handler uses this to return the user to their
        # workspace if a gate that cannot be pre-checked still refuses them.
        query['notification_click'] = '1'
    return urlunsplit(('', '', parts.path, urlencode(query), parts.fragment))


def first_accessible(user, candidates):
    """First candidate the user can open, else their role home."""
    for candidate in candidates:
        url = accessible_destination(user, candidate)
        if url:
            return url
    return role_home_url(user)
