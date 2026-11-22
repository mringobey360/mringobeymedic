"""Search-engine presentation: robots, sitemap, structured data and noindex rules."""
import json
import xml.etree.ElementTree as ET
from types import SimpleNamespace as NS

from django.test import SimpleTestCase, override_settings

from core import seo


@override_settings(SITE_URL='https://smartbiashara.store', SITE_NAME='SmartPOS', SITE_ALTERNATE_NAMES='SmartBiashara')
class SeoTests(SimpleTestCase):
    def test_robots_lists_sitemap_and_keeps_app_private(self):
        text = '\n'.join(seo.robots_lines())
        self.assertIn('Sitemap: https://smartbiashara.store/sitemap.xml', text)
        for private in ('/admin/', '/sales/', '/inventory/', '/hq/', '/accounts/'):
            self.assertIn(f'Disallow: {private}', text)
        self.assertIn('Allow: /jiunge/', text)
        self.assertIn('Allow: /$', text)

    def test_sitemap_is_valid_xml_with_absolute_https_urls(self):
        xml = seo.sitemap_xml([('/marketplace/properties/nice-house/', '2026-11-01', '0.4', 'weekly')])
        root = ET.fromstring(xml)
        ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        locs = [e.text for e in root.findall('s:url/s:loc', ns)]
        self.assertIn('https://smartbiashara.store/', locs)
        self.assertIn('https://smartbiashara.store/jiunge/', locs)
        self.assertIn('https://smartbiashara.store/marketplace/properties/nice-house/', locs)
        self.assertTrue(all(loc.startswith('https://') for loc in locs))

    def test_sitemap_escapes_and_deduplicates(self):
        xml = seo.sitemap_xml([('/a?x=1&y=2', None, '0.1', 'weekly'), ('/', None, '1.0', 'weekly')])
        ET.fromstring(xml)  # must still parse
        self.assertEqual(xml.count('<loc>https://smartbiashara.store/</loc>'), 1)

    def test_jsonld_is_valid_and_contains_prices_in_tzs(self):
        tiers = [{'name': 'Starter', 'monthly': 15000, 'branches': '1 branch', 'users': 'Up to 3 users'}]
        data = json.loads(seo.landing_jsonld(tiers, 2, 100, 30, 'Desc </script>'))
        graph = {node['@type']: node for node in data['@graph']}
        self.assertEqual(set(graph), {'Organization', 'WebSite', 'SoftwareApplication'})
        self.assertEqual(graph['WebSite']['name'], 'SmartPOS')
        self.assertEqual(graph['WebSite']['alternateName'], ['SmartBiashara'])
        offers = graph['SoftwareApplication']['offers']
        self.assertEqual(offers[0]['price'], '0')
        self.assertEqual(offers[1]['price'], '15000')
        self.assertEqual(offers[1]['priceCurrency'], 'TZS')

    def test_jsonld_never_closes_script_tag_early(self):
        self.assertNotIn('</script>', seo.landing_jsonld([], description='x </script><b>'))

    def test_jsonld_has_no_fake_ratings(self):
        self.assertNotIn('aggregateRating', seo.landing_jsonld([]))

    def test_temporary_hosts_are_noindex(self):
        self.assertTrue(seo.is_noindex_host('smartbiashara-production.up.railway.app'))
        self.assertTrue(seo.is_noindex_host('x.up.railway.app:443'))
        self.assertFalse(seo.is_noindex_host('smartbiashara.store'))

    def test_middleware_noindex_rules(self):
        class Resp(dict):
            pass

        def run(user, host):
            mw = seo.SearchVisibilityMiddleware(lambda request: Resp())
            return mw(NS(user=user, get_host=lambda: host))

        self.assertEqual(run(NS(is_authenticated=True), 'smartbiashara.store').get('X-Robots-Tag'), 'noindex, nofollow')
        self.assertEqual(run(NS(is_authenticated=False), 'abc.up.railway.app').get('X-Robots-Tag'), 'noindex, nofollow')
        self.assertNotIn('X-Robots-Tag', run(NS(is_authenticated=False), 'smartbiashara.store'))
