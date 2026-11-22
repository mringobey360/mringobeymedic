"""Seller social-media links: input cleaning, validation and display data.

Sellers may type a handle ("@mystore") or paste a link. Whatever they enter is
validated against a strict per-platform whitelist and rebuilt as a canonical
https URL, so the stored value can never be a javascript:/data: URL, an open
redirect, or a link to some other website dressed up as a social profile.

Icon artwork: Simple Icons (CC0 1.0). Brand colours are the platforms' own.
This module deliberately has no Django imports so it is trivial to test.
"""
import re
from urllib.parse import parse_qs, urlsplit

_SEG = re.compile(r'^[A-Za-z0-9._\-]{1,100}$')
_SCHEME = re.compile(r'^([A-Za-z][A-Za-z0-9+.\-]*):')

ICON_PATHS = {
    'whatsapp': 'M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52s.198-.298.298-.497c.099-.198.05-.371-.025-.52s-.669-1.612-.916-2.207c-.242-.579-.487-.5-.669-.51a13 13 0 0 0-.57-.01c-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074s2.096 3.2 5.077 4.487c.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413s.248-1.289.173-1.413c-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 0 1-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 0 1-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.82 9.82 0 0 1 2.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.82 11.82 0 0 0 12.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.9 11.9 0 0 0 5.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.82 11.82 0 0 0-3.48-8.413',
    'youtube': 'M23.498 6.186a3.02 3.02 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.02 3.02 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.02 3.02 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.02 3.02 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814M9.545 15.568V8.432L15.818 12z',
    'instagram': 'M7.03.084c-1.277.06-2.149.264-2.91.563a5.9 5.9 0 0 0-2.124 1.388 5.9 5.9 0 0 0-1.38 2.127C.321 4.926.12 5.8.064 7.076s-.069 1.688-.063 4.947.021 3.667.083 4.947c.061 1.277.264 2.149.563 2.911.308.789.72 1.457 1.388 2.123a5.9 5.9 0 0 0 2.129 1.38c.763.295 1.636.496 2.913.552 1.278.056 1.689.069 4.947.063s3.668-.021 4.947-.082c1.28-.06 2.147-.265 2.91-.563a5.9 5.9 0 0 0 2.123-1.388 5.9 5.9 0 0 0 1.38-2.129c.295-.763.496-1.636.551-2.912.056-1.28.07-1.69.063-4.948-.006-3.258-.02-3.667-.081-4.947-.06-1.28-.264-2.148-.564-2.911a5.9 5.9 0 0 0-1.387-2.123 5.86 5.86 0 0 0-2.128-1.38C19.074.322 18.202.12 16.924.066 15.647.009 15.236-.006 11.977 0S8.31.021 7.03.084m.14 21.693c-1.17-.05-1.805-.245-2.228-.408a3.7 3.7 0 0 1-1.382-.895 3.7 3.7 0 0 1-.9-1.378c-.165-.423-.363-1.058-.417-2.228-.06-1.264-.072-1.644-.08-4.848-.006-3.204.006-3.583.061-4.848.05-1.169.246-1.805.408-2.228.216-.561.477-.96.895-1.382a3.7 3.7 0 0 1 1.379-.9c.423-.165 1.057-.361 2.227-.417 1.265-.06 1.644-.072 4.848-.08 3.203-.006 3.583.006 4.85.062 1.168.05 1.804.244 2.227.408.56.216.96.475 1.382.895s.681.817.9 1.378c.165.422.362 1.056.417 2.227.06 1.265.074 1.645.08 4.848.005 3.203-.006 3.583-.061 4.848-.051 1.17-.245 1.805-.408 2.23-.216.56-.477.96-.896 1.38a3.7 3.7 0 0 1-1.378.9c-.422.165-1.058.362-2.226.418-1.266.06-1.645.072-4.85.079s-3.582-.006-4.848-.06m9.783-16.192a1.44 1.44 0 1 0 1.437-1.442 1.44 1.44 0 0 0-1.437 1.442M5.839 12.012a6.161 6.161 0 1 0 12.323-.024 6.162 6.162 0 0 0-12.323.024M8 12.008A4 4 0 1 1 12.008 16 4 4 0 0 1 8 12.008',
    'tiktok': 'M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07',
    'facebook': 'M9.101 23.691v-7.98H6.627v-3.667h2.474v-1.58c0-4.085 1.848-5.978 5.858-5.978.401 0 .955.042 1.468.103a9 9 0 0 1 1.141.195v3.325a9 9 0 0 0-.653-.036 27 27 0 0 0-.733-.009c-.707 0-1.259.096-1.675.309a1.7 1.7 0 0 0-.679.622c-.258.42-.374.995-.374 1.752v1.297h3.919l-.386 2.103-.287 1.564h-3.246v8.245C19.396 23.238 24 18.179 24 12.044c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.628 3.874 10.35 9.101 11.647',
}

# key -> display data. `field` is the SellerStore model field and the POST name.
PLATFORMS = {
    'whatsapp': {
        'label': 'WhatsApp', 'field': 'whatsapp_url', 'color': '#25D366',
        'placeholder': '0712 345 678  or  +255712345678',
        'help': 'Your WhatsApp number. Customers tap the icon to chat with you directly.',
        'hosts': {'wa.me', 'api.whatsapp.com', 'chat.whatsapp.com', 'whatsapp.com', 'www.whatsapp.com'},
    },
    'instagram': {
        'label': 'Instagram', 'field': 'instagram_url', 'color': '#E4405F',
        'placeholder': '@yourshop  or  instagram.com/yourshop',
        'help': 'Your Instagram username or profile link.',
        'hosts': {'instagram.com', 'www.instagram.com', 'm.instagram.com', 'instagr.am'},
        'handle': re.compile(r'^[A-Za-z0-9._]{1,30}$'),
        'reserved': {'p', 'reel', 'reels', 'tv', 'stories', 'explore', 'accounts', 'direct', 'about',
                     'developer', 'legal', 'web', 'challenge', 'oauth', 'privacy', 'directory'},
    },
    'tiktok': {
        'label': 'TikTok', 'field': 'tiktok_url', 'color': '#000000',
        'placeholder': '@yourshop  or  tiktok.com/@yourshop',
        'help': 'Your TikTok username or profile link.',
        'hosts': {'tiktok.com', 'www.tiktok.com', 'm.tiktok.com'},
        'short_hosts': {'vm.tiktok.com', 'vt.tiktok.com'},
        'handle': re.compile(r'^[A-Za-z0-9._]{2,24}$'),
    },
    'facebook': {
        'label': 'Facebook', 'field': 'facebook_url', 'color': '#1877F2',
        'placeholder': 'facebook.com/yourshop',
        'help': 'The link to your Facebook page or profile.',
        'hosts': {'facebook.com', 'www.facebook.com', 'm.facebook.com', 'web.facebook.com',
                  'mbasic.facebook.com', 'fb.com', 'www.fb.com'},
        'handle': re.compile(r'^[A-Za-z0-9.\-]{5,100}$'),
        'blocked': {'sharer', 'sharer.php', 'share.php', 'dialog', 'login', 'login.php', 'plugins', 'tr',
                    'l', 'l.php', 'recover', 'ads', 'help', 'policies', 'privacy', 'watch', 'groups',
                    'events', 'marketplace', 'gaming', 'hashtag', 'photo', 'photo.php', 'permalink.php',
                    'story.php', 'video.php', 'reel', 'reels'},
    },
    'youtube': {
        'label': 'YouTube', 'field': 'youtube_url', 'color': '#FF0000',
        'placeholder': '@yourchannel  or  youtube.com/@yourchannel',
        'help': 'Your YouTube channel username or link (not a single video).',
        'hosts': {'youtube.com', 'www.youtube.com', 'm.youtube.com'},
        'handle': re.compile(r'^[A-Za-z0-9._\-]{3,30}$'),
    },
}


def _err(label, kind):
    if kind == 'host':
        return f"That doesn't look like a {label} link. Enter your {label} username or profile link."
    if kind == 'post':
        return f"That is a {label} post or system link. Paste your profile link or username instead."
    return f"Enter a valid {label} username or profile link."


_WA_DIGITS = re.compile(r'^[1-9]\d{7,14}$')
_WA_CODE = re.compile(r'^[A-Za-z0-9_\-]{6,60}$')


def _wa_number(text):
    """Digits-only international number, or None. A leading 0 means Tanzania (+255)."""
    t = re.sub(r'[\s\-().]', '', text)
    if t.startswith('+'):
        t = t[1:]
    elif t.startswith('00'):
        t = t[2:]
    elif t.startswith('0') and len(t) == 10:
        t = '255' + t[1:]
    elif len(t) == 9 and t[0] in '67':      # 712345678 -> Tanzanian mobile without the 0
        t = '255' + t
    return t if _WA_DIGITS.match(t) else None


def _normalize_whatsapp(cfg, value):
    label = cfg['label']
    if len(value) > 200 or '\n' in value or '\r' in value:
        return None, _err(label, 'invalid')
    has_scheme = value.lower().startswith(('http://', 'https://'))
    candidate = value[value.index('//') + 2:] if has_scheme else value
    host_part = candidate.split('/', 1)[0].split('?', 1)[0].lower()
    if not (has_scheme or host_part in cfg['hosts']):
        if re.search(r'[A-Za-z/?#:@]', value):
            return None, ('Enter your WhatsApp number, e.g. 0712 345 678 or +255712345678.')
        number = _wa_number(value)
        if not number:
            return None, ('Enter a valid WhatsApp number, e.g. 0712 345 678 or +255712345678.')
        return f'https://wa.me/{number}', None
    try:
        parts = urlsplit('https://' + candidate)
        host, port = (parts.hostname or '').lower(), parts.port
    except ValueError:
        return None, _err(label, 'invalid')
    if port is not None or parts.username or parts.password or host not in cfg['hosts']:
        return None, _err(label, 'host')
    segs = [s for s in parts.path.split('/') if s]
    if host == 'wa.me':
        if len(segs) == 1 and _WA_DIGITS.match(segs[0]):
            return f'https://wa.me/{segs[0]}', None
        if len(segs) == 2 and segs[0] == 'message' and _WA_CODE.match(segs[1]):
            return f'https://wa.me/message/{segs[1]}', None
    elif host == 'api.whatsapp.com' and segs == ['send']:
        phone = parse_qs(parts.query).get('phone', [''])[0]
        if _WA_DIGITS.match(phone):
            return f'https://wa.me/{phone}', None
    elif host == 'chat.whatsapp.com' and len(segs) == 1 and _WA_CODE.match(segs[0]):
        return f'https://chat.whatsapp.com/{segs[0]}', None
    elif host in ('whatsapp.com', 'www.whatsapp.com') and len(segs) == 2 and segs[0] == 'channel' and _WA_CODE.match(segs[1]):
        return f'https://whatsapp.com/channel/{segs[1]}', None
    return None, ('Enter your WhatsApp number, e.g. 0712 345 678 or +255712345678.')


def normalize_social(platform, raw):
    """Return (clean_url, error). Empty input -> ('', None). Invalid -> (None, message)."""
    cfg = PLATFORMS[platform]
    label = cfg['label']
    value = (raw or '').strip()
    if not value:
        return '', None
    if platform == 'whatsapp':
        return _normalize_whatsapp(cfg, value)
    if len(value) > 200 or re.search(r'\s', value):
        return None, _err(label, 'invalid')

    scheme = _SCHEME.match(value)
    has_slashes = value.lower().startswith(('http://', 'https://'))
    if scheme and not has_slashes:
        # "javascript:...", "data:...", "mailto:..." and friends are never a profile link.
        return None, _err(label, 'host')

    candidate = value[value.index('//') + 2:] if has_slashes else value
    host_part = candidate.split('/', 1)[0].split('?', 1)[0].split('#', 1)[0].lower()
    all_hosts = cfg['hosts'] | cfg.get('short_hosts', set())
    looks_like_url = has_slashes or host_part in all_hosts

    if not looks_like_url:
        if '/' in value or '?' in value or '#' in value:
            return None, _err(label, 'host')
        return _from_handle(platform, value.lstrip('@'))

    try:
        parts = urlsplit('https://' + candidate)
        host, port = (parts.hostname or '').lower(), parts.port
    except ValueError:
        return None, _err(label, 'invalid')
    if port is not None or parts.username or parts.password or host not in all_hosts:
        return None, _err(label, 'host')
    segments = [s for s in parts.path.split('/') if s]

    if platform == 'instagram':
        if not segments:
            return None, _err(label, 'invalid')
        first = segments[0]
        if first.lower() in cfg['reserved']:
            return None, _err(label, 'post')
        return _from_handle(platform, first)

    if platform == 'tiktok':
        if host in cfg['short_hosts']:
            if len(segments) == 1 and re.match(r'^[A-Za-z0-9_\-]{4,30}$', segments[0]):
                return f'https://{host}/{segments[0]}/', None
            return None, _err(label, 'invalid')
        if segments and segments[0].startswith('@'):
            return _from_handle(platform, segments[0][1:])
        return None, _err(label, 'post' if segments else 'invalid')

    if platform == 'youtube':
        if segments and segments[0].startswith('@'):
            return _from_handle(platform, segments[0][1:])
        if len(segments) >= 2 and segments[0].lower() in ('channel', 'c', 'user') and re.match(r'^[A-Za-z0-9_\-]{1,100}$', segments[1]):
            return f'https://www.youtube.com/{segments[0].lower()}/{segments[1]}', None
        return None, _err(label, 'post' if segments else 'invalid')

    # facebook
    if not segments:
        return None, _err(label, 'invalid')
    first = segments[0].lower()
    if first in cfg['blocked']:
        return None, _err(label, 'post')
    if first == 'profile.php':
        ids = parse_qs(parts.query).get('id', [''])
        if len(segments) == 1 and re.match(r'^\d{5,20}$', ids[0]):
            return f'https://www.facebook.com/profile.php?id={ids[0]}', None
        return None, _err(label, 'invalid')
    segs = segments[:3]
    if not all(_SEG.match(s) for s in segs):
        return None, _err(label, 'invalid')
    return 'https://www.facebook.com/' + '/'.join(segs), None


def _from_handle(platform, handle):
    cfg = PLATFORMS[platform]
    if not cfg['handle'].match(handle):
        return None, _err(cfg['label'], 'invalid')
    if platform == 'instagram':
        return f'https://www.instagram.com/{handle}/', None
    if platform == 'tiktok':
        return f'https://www.tiktok.com/@{handle}', None
    if platform == 'youtube':
        return f'https://www.youtube.com/@{handle}', None
    return f'https://www.facebook.com/{handle}', None


def parse_social_post(data):
    """Clean the three social fields of a submitted form.

    Returns (cleaned, errors, typed): `cleaned` maps model field -> canonical URL,
    `errors` is a list of messages, `typed` echoes what the seller typed so a failed
    save can re-show it.
    """
    cleaned, errors, typed = {}, [], {}
    for key, cfg in PLATFORMS.items():
        raw = (data.get(cfg['field']) or '').strip()[:200]
        typed[key] = raw
        url, err = normalize_social(key, raw)
        if err:
            errors.append(err)
        else:
            cleaned[cfg['field']] = url
    return cleaned, errors, typed


def form_value(key, url):
    """How a stored link is shown back in the form (WhatsApp as a number, the rest as the link)."""
    url = url or ''
    if key == 'whatsapp':
        m = re.match(r'^https://wa\.me/(\d{8,15})$', url)
        return f'+{m.group(1)}' if m else url
    m = {'instagram': r'^https://www\.instagram\.com/([A-Za-z0-9._]+)/$',
         'tiktok': r'^https://www\.tiktok\.com/@([A-Za-z0-9._]+)$',
         'youtube': r'^https://www\.youtube\.com/@([A-Za-z0-9._\-]+)$'}.get(key)
    m = re.match(m, url) if m else None
    if m:
        return '@' + m.group(1)
    if key == 'facebook' and url.startswith('https://www.facebook.com/'):
        return url[len('https://www.'):]                      # facebook.com/YourPage
    return url


def prune_verified(store):
    """Verification only counts while the link is still exactly the one that was verified."""
    raw = getattr(store, 'social_verified', None)
    if not isinstance(raw, dict):
        return {}
    return {k: u for k, u in raw.items()
            if k in PLATFORMS and u and u == (getattr(store, PLATFORMS[k]['field'], '') or '')}


def compute_verified(urls, wanted):
    """Verified map {platform: url} for the platforms in `wanted` that currently have a link."""
    return {k: urls[k] for k in wanted if k in PLATFORMS and urls.get(k)}


def social_form_fields(values, store=None):
    """Rows for the setup form. `values` maps platform key -> text to show; `store` (if any)
    supplies the saved link and its verified state."""
    verified = {l['key'] for l in build_social_links(store) if l['verified']} if store is not None else set()
    return [{'key': k, 'label': c['label'], 'field': c['field'], 'placeholder': c['placeholder'],
             'help': c['help'], 'path': ICON_PATHS[k], 'value': values.get(k, ''),
             'inputmode': 'tel' if k == 'whatsapp' else 'url',
             'verified': k in verified,
             'stored': form_value(k, getattr(store, c['field'], '')) if store is not None else ''}
            for k, c in PLATFORMS.items()]


def build_social_links(store):
    """Links to display for a store. Re-validates stored values so a bad database
    value (e.g. edited in the admin) can never become an unsafe href."""
    links = []
    verified = prune_verified(store)
    for key, cfg in PLATFORMS.items():
        url, err = normalize_social(key, getattr(store, cfg['field'], '') or '')
        if url and not err:
            links.append({'key': key, 'label': cfg['label'], 'url': url, 'path': ICON_PATHS[key],
                          'verified': verified.get(key) == url})
    return links
