"""Pure-python tests for marketplace.movies (no database needed)."""
import io
from django.test import SimpleTestCase

from . import movies


class _F(io.BytesIO):
    def __init__(self, name, data):
        super().__init__(data)
        self.name = name
        self.size = len(data)


MP4 = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 40
WEBM = b'\x1aE\xdf\xa3' + b'\x00' * 40


class TrailerValidationTests(SimpleTestCase):
    def test_valid_mp4_and_webm(self):
        self.assertIsNone(movies.validate_trailer_upload(_F('t.mp4', MP4)))
        self.assertIsNone(movies.validate_trailer_upload(_F('t.webm', WEBM)))

    def test_rejects_wrong_type_fake_and_empty(self):
        self.assertIn('MP4 or WebM', movies.validate_trailer_upload(_F('t.mkv', MP4)))
        self.assertIn('valid MP4', movies.validate_trailer_upload(_F('t.mp4', b'x' * 40)))
        self.assertIn('valid WebM', movies.validate_trailer_upload(_F('t.webm', b'x' * 40)))
        self.assertIn('empty', movies.validate_trailer_upload(_F('t.mp4', b'')))

    def test_rejects_oversize(self):
        f = _F('t.mp4', MP4)
        f.size = movies.TRAILER_MAX_BYTES + 1
        self.assertIn('MB', movies.validate_trailer_upload(f))

    def test_content_type(self):
        self.assertEqual(movies.trailer_content_type('a.MP4'), 'video/mp4')
        self.assertEqual(movies.trailer_content_type('a.webm'), 'video/webm')


class RangeTests(SimpleTestCase):
    def test_no_header_or_garbage_serves_whole_file(self):
        for h in (None, '', 'items=0-1', 'bytes=0-1,5-6', 'bytes=-', 'bytes=9-3'):
            self.assertIsNone(movies.parse_range(h, 1000), h)

    def test_open_ended_and_bounded(self):
        self.assertEqual(movies.parse_range('bytes=0-', 1000), (0, 999))
        self.assertEqual(movies.parse_range('bytes=100-199', 1000), (100, 199))
        self.assertEqual(movies.parse_range('bytes=900-5000', 1000), (900, 999))

    def test_suffix(self):
        self.assertEqual(movies.parse_range('bytes=-100', 1000), (900, 999))
        self.assertEqual(movies.parse_range('bytes=-5000', 1000), (0, 999))
        self.assertEqual(movies.parse_range('bytes=-0', 1000), 'unsatisfiable')

    def test_unsatisfiable(self):
        self.assertEqual(movies.parse_range('bytes=1000-', 1000), 'unsatisfiable')
        self.assertEqual(movies.parse_range('bytes=5000-6000', 1000), 'unsatisfiable')

    def test_safari_probe(self):
        # Safari/iOS first asks for bytes=0-1 to learn the size.
        self.assertEqual(movies.parse_range('bytes=0-1', 1000), (0, 1))


class MetaTests(SimpleTestCase):
    def test_year(self):
        self.assertEqual(movies.clean_year('2024'), 2024)
        self.assertIsNone(movies.clean_year('1800'))
        self.assertIsNone(movies.clean_year('abc'))
        self.assertIsNone(movies.clean_year(None))

    def test_duration(self):
        self.assertEqual(movies.format_duration(90), '1h 30m')
        self.assertEqual(movies.format_duration(45), '45m')
        self.assertEqual(movies.format_duration(120), '2h')
        self.assertEqual(movies.format_duration(0), '')
        self.assertEqual(movies.format_duration('x'), '')

    def test_genres(self):
        self.assertEqual(movies.split_genres('Action, Drama ,, action, Comedy'), ['Action', 'Drama', 'Comedy'])
        self.assertEqual(len(movies.split_genres('a,b,c,d,e,f')), 4)
        self.assertEqual(movies.split_genres(''), [])
