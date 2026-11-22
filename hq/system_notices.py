"""Which system notices (status banners) should this visitor see right now?

Called from the global context processor on every page, so it must be cheap and must
never raise: the active notices are cached for a few seconds (and the cache is cleared
the moment a notice is saved or deleted), and any failure simply means "no banner".

Only PUBLISHED notices ever reach customers. Drafts are visible to the platform owner
on the review page only.
"""
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from .models import NOTICE_CACHE_KEY, SystemNotice

CACHE_SECONDS = 30
MAX_VISIBLE = 3


_ICON_WORDS = (
    ('rocket', ('anza', 'start', 'jaribu', 'try', 'fungua', 'open', 'sasa hivi')),
    ('cart', ('nunua', 'buy', 'agiza', 'order', 'duka', 'shop', 'marketplace', 'lipa', 'pay')),
    ('phone', ('piga', 'wasiliana', 'simu', 'call', 'contact', 'whatsapp')),
    ('book', ('soma', 'zaidi', 'read', 'more', 'maelezo', 'details', 'jifunze', 'learn')),
)


def link_icon_for(label):
    """Pick a friendly button icon from the button text (default: arrow)."""
    text = (label or 'Soma zaidi').strip().lower()
    for icon, words in _ICON_WORDS:
        if any(w in text for w in words):
            return icon
    return 'arrow'


def notice_to_row(n):
    """The exact dict the banner template renders (used for live notices and for previews)."""
    return {
        'id': n.pk, 'kind': n.kind, 'tone': n.tone, 'badge': n.badge,
        'title': n.title, 'message': n.message, 'audience': n.audience,
        'link_url': n.link_url, 'link_label': n.link_label, 'link_icon': link_icon_for(n.link_label),
        'show_on_login': n.show_on_login, 'dismissible': n.dismissible,
        'version': n.version, 'starts_at': n.starts_at, 'ends_at': n.ends_at,
        'published_at': n.published_at,
    }


def _active_rows():
    rows = cache.get(NOTICE_CACHE_KEY)
    if rows is not None:
        return rows
    now = timezone.now()
    qs = (SystemNotice.objects
          .filter(is_active=True, publish_state=SystemNotice.Workflow.PUBLISHED)
          .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
          .order_by('-created_at')[:20])
    rows = [notice_to_row(n) for n in qs]
    cache.set(NOTICE_CACHE_KEY, rows, CACHE_SECONDS)
    return rows


def notices_for_user(user):
    try:
        now = timezone.now()
        signed_in = bool(user is not None and getattr(user, 'is_authenticated', False))
        is_manager = bool(signed_in and (user.is_superuser or user.is_management_user()))
        visible = []
        for row in _active_rows():
            if row['starts_at'] and row['starts_at'] > now:
                continue
            if row['ends_at'] and row['ends_at'] <= now:
                continue
            if signed_in:
                if row['audience'] == SystemNotice.Audience.MANAGERS and not is_manager:
                    continue
            elif not row['show_on_login']:
                continue
            visible.append(row)
        visible.sort(key=lambda r: SystemNotice.PRIORITY.get(r['kind'], 9))
        return visible[:MAX_VISIBLE]
    except Exception:
        return []
