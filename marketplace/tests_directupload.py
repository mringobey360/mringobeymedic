"""Pure-logic tests for direct uploads (no Django needed): python -m unittest marketplace.tests_directupload"""
import unittest

from . import directupload as du


class FakeS3:
    def __init__(self):
        self.parts, self.calls = {}, []

    def create_multipart_upload(self, **kw):
        return {'UploadId': 'U1'}

    def generate_presigned_url(self, op, ExpiresIn, Params):
        return 'https://b/%s?p=%d' % (Params['Key'], Params['PartNumber'])

    def list_parts(self, **kw):
        marker = kw.get('PartNumberMarker', 0)
        nums = sorted(n for n in self.parts if n > marker)[:2]          # tiny pages to exercise pagination
        more = len([n for n in self.parts if n > marker]) > 2
        return {'Parts': [{'PartNumber': n, 'ETag': '"e%d"' % n, 'Size': self.parts[n]} for n in nums],
                'IsTruncated': more, 'NextPartNumberMarker': nums[-1] if nums else 0}

    def complete_multipart_upload(self, **kw):
        self.calls.append(kw['MultipartUpload']['Parts'])

    def head_object(self, **kw):
        return {'ContentLength': sum(self.parts.values())}

    def get_object(self, **kw):
        class B:
            def read(self_inner):
                return b'\x00\x00\x00\x18ftypmp42'
        return {'Body': B()}


class DirectUploadTests(unittest.TestCase):
    def test_part_size_grows_for_huge_files(self):
        self.assertEqual(du.part_size_for(1024 * du.MB), 8 * du.MB)
        self.assertGreater(du.part_size_for(200 * 1024 * du.MB), 8 * du.MB)
        self.assertLessEqual(du.total_parts(200 * 1024 * du.MB, du.part_size_for(200 * 1024 * du.MB)), 10000)

    def test_total_parts(self):
        self.assertEqual(du.total_parts(1, 8 * du.MB), 1)
        self.assertEqual(du.total_parts(8 * du.MB + 1, 8 * du.MB), 2)

    def test_storage_name_matches_private_layout(self):
        n = du.new_storage_name(7, 'My Film.MP4')
        self.assertTrue(n.startswith('digital/store-7/') and n.endswith('.mp4'))
        self.assertEqual(du.bucket_key(n), 'private/' + n)
        self.assertNotEqual(n, du.new_storage_name(7, 'My Film.MP4'))

    def test_list_parts_paginates_and_verify(self):
        size = 8 * du.MB * 4 + 100
        fake = FakeS3()
        fake.parts = {1: 8 * du.MB, 2: 8 * du.MB, 3: 8 * du.MB, 4: 8 * du.MB}
        c = du.MultipartClient(fake, 'b')
        parts = c.list_parts('k', 'U1')
        self.assertEqual(sorted(parts), [1, 2, 3, 4])
        self.assertIn('missing', du.verify_parts(parts, size, 8 * du.MB))
        fake.parts[5] = 100
        parts = c.list_parts('k', 'U1')
        self.assertIsNone(du.verify_parts(parts, size, 8 * du.MB))
        fake.parts[2] = 5
        self.assertIn('Part 2', du.verify_parts(c.list_parts('k', 'U1'), size, 8 * du.MB))

    def test_complete_orders_parts_and_headfile_validates(self):
        from .digital import validate_digital_upload
        fake = FakeS3()
        fake.parts = {2: 10, 1: 10}
        c = du.MultipartClient(fake, 'b')
        c.complete('k', 'U1', c.list_parts('k', 'U1'))
        self.assertEqual([p['PartNumber'] for p in fake.calls[0]], [1, 2])
        self.assertIsNone(validate_digital_upload(du.HeadFile('a.mp4', 20, c.head_bytes('k'))))
        self.assertIsNotNone(validate_digital_upload(du.HeadFile('a.mp4', 20, b'not a video at all')))

    def test_no_such_upload_detection(self):
        e = Exception(); e.response = {'Error': {'Code': 'NoSuchUpload'}}
        self.assertTrue(du.is_no_such_upload(e))
        self.assertFalse(du.is_no_such_upload(Exception('x')))


if __name__ == '__main__':
    unittest.main()
