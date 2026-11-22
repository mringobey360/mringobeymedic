"""Search-engine presentation (SEO) helpers for the public SmartPOS pages.

What Google shows for a site is built from: the page title, the meta description, the favicon,
the site name, the canonical URL, the social/preview image and schema.org structured data.
Everything here keeps those consistent and independent of whichever host served the request
(for example the temporary ``*.up.railway.app`` domain), so only the real domain gets indexed.

Configure with environment variables (all optional):

* ``SITE_URL``                 canonical public address, default ``https://smartbiashara.store``
* ``SITE_NAME``                brand shown by Google, default ``SmartPOS``
* ``SITE_ALTERNATE_NAMES``     comma-separated extra names, default ``SmartBiashara``
* ``GOOGLE_SITE_VERIFICATION`` token for the Google Search Console meta tag (optional)
"""
import json
from urllib.parse import urlparse

from django.conf import settings

DEFAULT_SITE_URL = 'https://smartbiashara.store'
OG_IMAGE_STATIC_PATH = 'img/og-smartpos.png'
LOGO_STATIC_PATH = 'img/icons/icon-512.png'

# Hosts that must never be indexed (temporary platform domains) - the canonical domain is.
NOINDEX_HOST_SUFFIXES = ('.up.railway.app', '.railway.app')

# Public pages that belong in the sitemap (reverse() names are resolved lazily in the view).
PUBLIC_PATHS = (
    ('/', '1.0', 'weekly'),
    ('/jiunge/', '0.9', 'monthly'),
    ('/accounts/login/', '0.5', 'yearly'),
    ('/marketplace/properties/', '0.6', 'daily'),
    ('/marketplace/properties/rent/', '0.5', 'daily'),
    ('/marketplace/properties/sale/', '0.5', 'daily'),
)


def site_url():
    return (getattr(settings, 'SITE_URL', '') or DEFAULT_SITE_URL).rstrip('/')


def site_name():
    return getattr(settings, 'SITE_NAME', '') or 'SmartPOS'


def alternate_names():
    raw = getattr(settings, 'SITE_ALTERNATE_NAMES', 'SmartBiashara')
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(',')]
    return [name for name in raw if name and name != site_name()]


def absolute_url(path):
    return site_url() + '/' + str(path or '').lstrip('/')


def static_url(path):
    from django.templatetags.static import static
    return absolute_url(static(path))


def og_image_url():
    return static_url(OG_IMAGE_STATIC_PATH)


def is_noindex_host(host):
    hostname = (host or '').split(':')[0].lower()
    return hostname.endswith(NOINDEX_HOST_SUFFIXES)


def seo_context(request):
    """Template context processor: ``SITE_URL``, ``SEO_CANONICAL``, ``SEO_OG_IMAGE`` ..."""
    path = getattr(request, 'path', '/') or '/'
    return {
        'SITE_URL': site_url(),
        'SEO_SITE_NAME': site_name(),
        'SEO_CANONICAL': absolute_url(path),  # query strings are deliberately dropped
        'SEO_OG_IMAGE': og_image_url(),
        'SEO_GOOGLE_VERIFICATION': getattr(settings, 'GOOGLE_SITE_VERIFICATION', ''),
    }


class SearchVisibilityMiddleware:
    """Keep private and duplicate pages out of search results.

    * Responses for signed-in users (the application itself) get ``X-Robots-Tag: noindex``.
    * Everything served from a temporary platform host (``*.up.railway.app``) is noindex, so
      Google keeps only the real domain.
    Public pages for anonymous visitors on the real domain are untouched.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            if 'X-Robots-Tag' in response:
                return response
            user = getattr(request, 'user', None)
            if getattr(user, 'is_authenticated', False) or is_noindex_host(request.get_host()):
                response['X-Robots-Tag'] = 'noindex, nofollow'
        except Exception:  # never let SEO headers break a page
            pass
        return response


def _json_script_safe(data):
    """JSON for a <script type="application/ld+json"> block (no premature </script>)."""
    return json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')


def landing_jsonld(tiers=(), free_users=2, free_sales=100, trial_days=30, description=''):
    """schema.org graph for the landing page: Organization, WebSite and SoftwareApplication.

    Only facts that are visible on the page are included (name, prices from HQ settings, free
    plan). No review ratings are emitted: Google forbids marking up ratings that are not real.
    """
    site = site_url()
    org_id, web_id, app_id = site + '/#organization', site + '/#website', site + '/#software'
    offers = [{
        '@type': 'Offer', 'name': 'Free plan', 'price': '0', 'priceCurrency': 'TZS',
        'description': f'{trial_days}-day free start: {free_users} users and {free_sales} sales a month.',
        'url': site + '/jiunge/',
    }]
    for tier in tiers:
        try:
            price = str(int(tier['monthly']))
        except (KeyError, TypeError, ValueError):
            continue
        offers.append({
            '@type': 'Offer', 'name': f"{tier['name']} (monthly)", 'price': price, 'priceCurrency': 'TZS',
            'description': f"{tier.get('branches', '')}, {tier.get('users', '')}".strip(', '),
            'url': site + '/jiunge/',
        })
    graph = [
        {
            '@type': 'Organization', '@id': org_id, 'name': 'Mutech Apex Innovations',
            'legalName': 'Mutech Apex Innovations', 'url': site,
            'logo': {'@type': 'ImageObject', 'url': static_url(LOGO_STATIC_PATH), 'width': 512, 'height': 512},
            'brand': {'@type': 'Brand', 'name': site_name()},
            'address': {'@type': 'PostalAddress', 'addressLocality': 'Chemba', 'addressRegion': 'Dodoma', 'addressCountry': 'TZ'},
            'identifier': {'@type': 'PropertyValue', 'propertyID': 'BRELA registration number', 'value': '717397'},
        },
        {
            '@type': 'WebSite', '@id': web_id, 'url': site + '/', 'name': site_name(),
            'alternateName': alternate_names(), 'inLanguage': 'en',
            'publisher': {'@id': org_id},
        },
        {
            '@type': 'SoftwareApplication', '@id': app_id, 'name': site_name(), 'url': site + '/',
            'applicationCategory': 'BusinessApplication', 'operatingSystem': 'Web, Android, iOS',
            'description': description, 'image': og_image_url(), 'inLanguage': 'en',
            'publisher': {'@id': org_id}, 'offers': offers,
            'areaServed': {'@type': 'Country', 'name': 'Tanzania'},
        },
    ]
    return _json_script_safe({'@context': 'https://schema.org', '@graph': graph})


def robots_lines():
    return [
        'User-agent: *',
        'Allow: /$',
        'Allow: /jiunge/',
        'Allow: /accounts/login/',
        'Allow: /marketplace/properties/',
        'Allow: /favicon.ico',
        'Allow: /static/',
        'Disallow: /admin/',
        'Disallow: /accounts/',
        'Disallow: /sales/',
        'Disallow: /inventory/',
        'Disallow: /pharmacies/',
        'Disallow: /payments/',
        'Disallow: /expenses/',
        'Disallow: /layaway/',
        'Disallow: /daily-reports/',
        'Disallow: /performance/',
        'Disallow: /sms/',
        'Disallow: /hq/',
        'Disallow: /data-protection/',
        'Disallow: /marketplace/seller/',
        'Disallow: /marketplace/manage/',
        'Disallow: /marketplace/cart/',
        'Disallow: /marketplace/checkout/',
        'Disallow: /marketplace/payment/',
        '',
        f'Sitemap: {site_url()}/sitemap.xml',
    ]


def sitemap_xml(extra_urls=()):
    """Build sitemap XML. ``extra_urls`` is an iterable of (path, lastmod|None, priority, changefreq)."""
    from xml.sax.saxutils import escape
    rows = [(path, None, priority, freq) for path, priority, freq in PUBLIC_PATHS]
    rows.extend(extra_urls)
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    seen = set()
    for path, lastmod, priority, freq in rows:
        loc = absolute_url(path)
        if loc in seen:
            continue
        seen.add(loc)
        out.append('<url><loc>%s</loc>%s<changefreq>%s</changefreq><priority>%s</priority></url>' % (
            escape(loc), f'<lastmod>{lastmod}</lastmod>' if lastmod else '', freq, priority))
    out.append('</urlset>')
    return '\n'.join(out) + '\n'
