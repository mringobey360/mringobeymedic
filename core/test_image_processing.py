import io
import unittest

from PIL import Image

from core import image_processing as ip


def _jpeg(size, quality=95):
    img = Image.effect_noise(size, 40).convert('RGB')
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=quality)
    return buf.getvalue()


class ImageProcessingTests(unittest.TestCase):
    def test_large_product_photo_is_shrunk_not_cropped(self):
        src = _jpeg((4000, 3000))
        res = ip.optimize(src, ip.PRODUCT)
        self.assertIsNotNone(res)
        self.assertEqual((res.width, res.height), (1600, 1200))  # same 4:3 ratio, nothing cut
        self.assertLess(len(res.data), len(src) / 5)
        self.assertTrue(Image.open(io.BytesIO(res.data)).info.get('progressive'))

    def test_never_upscales(self):
        res = ip.optimize(_jpeg((300, 200)), ip.PRODUCT)
        self.assertEqual((res.width, res.height), (300, 200))

    def test_avatar_portrait_becomes_square_with_whole_photo(self):
        res = ip.optimize(_jpeg((1200, 1800)), ip.AVATAR)
        self.assertEqual((res.width, res.height), (640, 640))

    def test_avatar_is_not_upscaled(self):
        res = ip.optimize(_jpeg((100, 100)), ip.AVATAR)
        self.assertEqual((res.width, res.height), (100, 100))

    def test_exif_rotation_is_applied(self):
        img = Image.open(io.BytesIO(_jpeg((1000, 500))))
        exif = img.getexif()
        exif[0x0112] = 6
        buf = io.BytesIO()
        img.save(buf, 'JPEG', exif=exif)
        res = ip.optimize(buf.getvalue(), ip.PRODUCT)
        self.assertEqual((res.width, res.height), (500, 1000))
        self.assertFalse(Image.open(io.BytesIO(res.data)).getexif())  # metadata stripped

    def test_transparent_logo_stays_png(self):
        logo = Image.new('RGBA', (300, 120), (0, 0, 0, 0))
        logo.paste((200, 30, 30, 255), (10, 10, 110, 110))
        buf = io.BytesIO()
        logo.save(buf, 'PNG')
        res = ip.optimize(buf.getvalue(), ip.LOGO)
        if res is not None:
            self.assertEqual(res.ext, '.png')
            self.assertEqual(Image.open(io.BytesIO(res.data)).mode, 'RGBA')

    def test_garbage_is_left_alone(self):
        self.assertIsNone(ip.optimize(b'not an image', ip.PRODUCT))

    def test_animated_gif_is_left_alone(self):
        frames = [Image.new('P', (20, 20), i) for i in (0, 1)]
        buf = io.BytesIO()
        frames[0].save(buf, 'GIF', save_all=True, append_images=frames[1:])
        self.assertIsNone(ip.optimize(buf.getvalue(), ip.PRODUCT))

    def test_profiles_are_mapped(self):
        self.assertIs(ip.profile_for('accounts.User', 'profile_photo'), ip.AVATAR)
        self.assertIs(ip.profile_for('marketplace.ListingImage', 'image'), ip.LISTING)
        self.assertIs(ip.profile_for('something.Else', 'photo'), ip.DEFAULT_PROFILE)


if __name__ == '__main__':
    unittest.main()
