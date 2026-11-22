"""Unit tests for seller social-link validation (no database needed)."""
import unittest
from types import SimpleNamespace

from .social import (PLATFORMS, build_social_links, compute_verified, form_value, normalize_social,
                     parse_social_post, prune_verified, social_form_fields)


class NormalizeSocialTests(unittest.TestCase):
    def ok(self, platform, raw, expected):
        url, err = normalize_social(platform, raw)
        self.assertIsNone(err, f'{raw!r} -> {err}')
        self.assertEqual(url, expected, raw)

    def bad(self, platform, raw):
        url, err = normalize_social(platform, raw)
        self.assertIsNone(url, f'{raw!r} should be rejected, got {url}')
        self.assertTrue(err)

    def test_empty_is_allowed(self):
        for p in PLATFORMS:
            self.assertEqual(normalize_social(p, ''), ('', None))
            self.assertEqual(normalize_social(p, '   '), ('', None))
            self.assertEqual(normalize_social(p, None), ('', None))

    def test_instagram(self):
        want = 'https://www.instagram.com/my.shop_tz/'
        for raw in ('@my.shop_tz', 'my.shop_tz', 'instagram.com/my.shop_tz', 'www.instagram.com/my.shop_tz/',
                    'https://instagram.com/my.shop_tz?igsh=abc123', 'http://m.instagram.com/my.shop_tz/#x',
                    'HTTPS://WWW.INSTAGRAM.COM/my.shop_tz'):
            self.ok('instagram', raw, want)
        self.bad('instagram', 'https://www.instagram.com/p/Cabc123/')       # a post, not a profile
        self.bad('instagram', 'https://www.instagram.com/')
        self.bad('instagram', 'https://evil.com/my.shop')
        self.bad('instagram', 'https://instagram.com.evil.com/shop')
        self.bad('instagram', 'https://evil.com/instagram.com/shop')
        self.bad('instagram', 'a' * 31)

    def test_tiktok(self):
        want = 'https://www.tiktok.com/@duka.tz'
        for raw in ('@duka.tz', 'duka.tz', 'tiktok.com/@duka.tz', 'https://www.tiktok.com/@duka.tz?lang=en'):
            self.ok('tiktok', raw, want)
        self.ok('tiktok', 'https://vm.tiktok.com/ZMabc123/', 'https://vm.tiktok.com/ZMabc123/')
        self.bad('tiktok', 'https://www.tiktok.com/video/123')
        self.bad('tiktok', 'https://www.tiktok.com/')
        self.bad('tiktok', 'https://tiktok.evil.com/@duka')
        self.bad('tiktok', 'x')

    def test_facebook(self):
        self.ok('facebook', 'facebook.com/DukaLangu', 'https://www.facebook.com/DukaLangu')
        self.ok('facebook', 'https://m.facebook.com/DukaLangu/?ref=bookmarks', 'https://www.facebook.com/DukaLangu')
        self.ok('facebook', 'https://web.facebook.com/profile.php?id=100012345678901&sk=about',
                'https://www.facebook.com/profile.php?id=100012345678901')
        self.ok('facebook', 'https://www.facebook.com/pages/Duka-Langu/123456789',
                'https://www.facebook.com/pages/Duka-Langu/123456789')
        self.ok('facebook', 'fb.com/DukaLangu', 'https://www.facebook.com/DukaLangu')
        self.ok('facebook', 'DukaLangu', 'https://www.facebook.com/DukaLangu')
        self.bad('facebook', 'https://www.facebook.com/l.php?u=https://evil.com')   # open redirect
        self.bad('facebook', 'https://www.facebook.com/sharer/sharer.php?u=x')
        self.bad('facebook', 'https://www.facebook.com/profile.php?id=abc')
        self.bad('facebook', 'https://www.facebook.com/')
        self.bad('facebook', 'https://facebook.com.evil.com/DukaLangu')

    def test_whatsapp_numbers(self):
        want = 'https://wa.me/255712345678'
        for raw in ('0712345678', '0712 345 678', '+255712345678', '+255 712-345-678', '255712345678',
                    '00255712345678', '712345678', '(0712) 345 678', 'wa.me/255712345678',
                    'https://wa.me/255712345678?text=hello', 'https://api.whatsapp.com/send?phone=255712345678&text=hi'):
            self.ok('whatsapp', raw, want)
        self.ok('whatsapp', '+254700111222', 'https://wa.me/254700111222')            # other country keeps its code
        self.ok('whatsapp', 'https://wa.me/message/AB12cd34EF', 'https://wa.me/message/AB12cd34EF')
        self.ok('whatsapp', 'https://chat.whatsapp.com/AbCdEf1234567890', 'https://chat.whatsapp.com/AbCdEf1234567890')
        for raw in ('12345', '07123', '0712345678901234567', 'call me 0712345678', 'https://evil.com/255712345678',
                    'https://wa.me.evil.com/255712345678', 'https://wa.me/', 'https://api.whatsapp.com/send?phone=abc',
                    'https://wa.me/message/x', '+0255712345678'):
            self.bad('whatsapp', raw)

    def test_youtube(self):
        want = 'https://www.youtube.com/@dukatv'
        for raw in ('@dukatv', 'dukatv', 'youtube.com/@dukatv', 'https://www.youtube.com/@dukatv/videos',
                    'https://m.youtube.com/@dukatv?si=abc'):
            self.ok('youtube', raw, want)
        self.ok('youtube', 'https://www.youtube.com/channel/UCabc_123-xyz', 'https://www.youtube.com/channel/UCabc_123-xyz')
        self.ok('youtube', 'https://youtube.com/c/DukaTV', 'https://www.youtube.com/c/DukaTV')
        self.bad('youtube', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')           # a video, not a channel
        self.bad('youtube', 'https://youtu.be/dQw4w9WgXcQ')
        self.bad('youtube', 'https://www.youtube.com/')
        self.bad('youtube', 'https://youtube.com.evil.com/@dukatv')
        self.bad('youtube', 'ab')

    def test_dangerous_inputs_rejected_everywhere(self):
        nasty = ('javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html,<script>1</script>',
                 'vbscript:x', 'file:///etc/passwd', 'ftp://instagram.com/shop', 'mailto:a@b.com',
                 '//evil.com/shop', '<script>alert(1)</script>', '"><img src=x onerror=alert(1)>',
                 'https://user:pass@instagram.com/shop', 'https://instagram.com:8080/shop',
                 'two words', 'https://instagram.com/shop\nhttps://evil.com')
        for p in PLATFORMS:
            for raw in nasty:
                self.bad(p, raw)

    def test_result_is_idempotent(self):
        for p, raw in (('instagram', '@my.shop'), ('tiktok', '@duka.tz'), ('facebook', 'DukaLangu'),
                       ('whatsapp', '0712 345 678'), ('youtube', '@dukatv')):
            first, _ = normalize_social(p, raw)
            self.assertEqual(normalize_social(p, first), (first, None))


class ParseAndBuildTests(unittest.TestCase):
    def test_parse_social_post_collects_errors_and_echoes_input(self):
        cleaned, errors, typed = parse_social_post(
            {'instagram_url': '@shop', 'tiktok_url': 'https://evil.com/x', 'facebook_url': ''})
        self.assertEqual(cleaned['instagram_url'], 'https://www.instagram.com/shop/')
        self.assertEqual(cleaned['facebook_url'], '')
        self.assertNotIn('tiktok_url', cleaned)
        self.assertEqual(len(errors), 1)
        self.assertIn('TikTok', errors[0])
        self.assertEqual(typed['tiktok'], 'https://evil.com/x')

    def test_build_links_only_for_filled_valid_fields(self):
        store = SimpleNamespace(instagram_url='https://www.instagram.com/shop/', tiktok_url='',
                                facebook_url='javascript:alert(1)')
        links = build_social_links(store)
        self.assertEqual([l['key'] for l in links], ['instagram'])
        self.assertTrue(links[0]['path'].startswith('M'))

    def test_build_links_empty_store(self):
        self.assertEqual(build_social_links(SimpleNamespace(instagram_url='', tiktok_url='', facebook_url='')), [])

    def test_display_order_puts_whatsapp_first(self):
        store = SimpleNamespace(instagram_url='https://www.instagram.com/s/', tiktok_url='', facebook_url='',
                                whatsapp_url='https://wa.me/255712345678', youtube_url='https://www.youtube.com/@shop1')
        self.assertEqual([l['key'] for l in build_social_links(store)], ['whatsapp', 'instagram', 'youtube'])


class VerificationTests(unittest.TestCase):
    def store(self, **kw):
        base = dict(whatsapp_url='', instagram_url='', tiktok_url='', facebook_url='', youtube_url='', social_verified={})
        base.update(kw)
        return SimpleNamespace(**base)

    def test_badge_only_while_link_unchanged(self):
        ig = 'https://www.instagram.com/duka/'
        s = self.store(instagram_url=ig, social_verified={'instagram': ig})
        self.assertTrue(build_social_links(s)[0]['verified'])
        s.instagram_url = 'https://www.instagram.com/someone_else/'      # seller swaps the link
        self.assertFalse(build_social_links(s)[0]['verified'])
        self.assertEqual(prune_verified(s), {})                          # and the stored proof is dropped

    def test_unverified_by_default_and_garbage_ignored(self):
        s = self.store(instagram_url='https://www.instagram.com/duka/')
        self.assertFalse(build_social_links(s)[0]['verified'])
        for junk in (None, [], 'yes', {'instagram': ''}, {'nope': 'x'}, {'instagram': 123}):
            s.social_verified = junk
            self.assertFalse(build_social_links(s)[0]['verified'], junk)
            self.assertEqual(prune_verified(s), {})

    def test_verification_is_per_platform(self):
        s = self.store(instagram_url='https://www.instagram.com/d/', tiktok_url='https://www.tiktok.com/@d1',
                       social_verified={'tiktok': 'https://www.tiktok.com/@d1'})
        self.assertEqual({l['key']: l['verified'] for l in build_social_links(s)}, {'instagram': False, 'tiktok': True})

    def test_compute_verified(self):
        urls = {'instagram': 'https://www.instagram.com/d/', 'tiktok': '', 'whatsapp': 'https://wa.me/255712345678'}
        self.assertEqual(compute_verified(urls, ['instagram', 'tiktok', 'bogus']), {'instagram': 'https://www.instagram.com/d/'})
        self.assertEqual(compute_verified(urls, []), {})

    def test_form_rows_report_verified_and_stored(self):
        ig = 'https://www.instagram.com/duka/'
        s = self.store(instagram_url=ig, whatsapp_url='https://wa.me/255712345678', social_verified={'instagram': ig})
        rows = {r['key']: r for r in social_form_fields({'instagram': ig}, s)}
        self.assertTrue(rows['instagram']['verified'])
        self.assertFalse(rows['whatsapp']['verified'])
        self.assertEqual(rows['whatsapp']['stored'], '+255712345678')
        self.assertEqual(rows['whatsapp']['inputmode'], 'tel')
        self.assertEqual(rows['instagram']['inputmode'], 'url')

    def test_whatsapp_roundtrips_through_the_form(self):
        url = 'https://wa.me/255712345678'
        shown = form_value('whatsapp', url)
        self.assertEqual(shown, '+255712345678')
        self.assertEqual(normalize_social('whatsapp', shown), (url, None))

    def test_every_platform_roundtrips_through_the_form(self):
        for p, raw in (('instagram', '@my.shop'), ('tiktok', '@duka.tz'), ('facebook', 'DukaLangu'),
                       ('facebook', 'https://www.facebook.com/profile.php?id=100012345678901'),
                       ('facebook', 'https://www.facebook.com/pages/Duka-Langu/123456789'),
                       ('whatsapp', '0712 345 678'), ('youtube', '@dukatv'),
                       ('youtube', 'https://www.youtube.com/channel/UCabc_123-xyz'),
                       ('tiktok', 'https://vm.tiktok.com/ZMabc123/')):
            url, _ = normalize_social(p, raw)
            shown = form_value(p, url)
            self.assertEqual(normalize_social(p, shown), (url, None), (p, raw, shown))
        self.assertEqual(form_value('instagram', 'https://www.instagram.com/d/'), '@d')
        self.assertEqual(form_value('facebook', 'https://www.facebook.com/DukaLangu'), 'facebook.com/DukaLangu')
        self.assertEqual(form_value('tiktok', 'https://www.tiktok.com/@d1'), '@d1')


if __name__ == '__main__':
    unittest.main()
