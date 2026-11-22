import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image

from accounts.models import User
from core.image_signals import optimize_new_uploads


def _png(size):
    buf = io.BytesIO()
    Image.effect_noise(size, 40).convert('RGB').save(buf, 'PNG')
    return buf.getvalue()


class UploadOptimisationTests(SimpleTestCase):
    def test_profile_photo_is_stored_whole_on_a_square_jpeg(self):
        user = User(username='img-test')
        user.profile_photo = SimpleUploadedFile('me.png', _png((1200, 1800)), content_type='image/png')
        optimize_new_uploads(User, user)
        photo = user.profile_photo
        self.assertTrue(photo.name.endswith('.jpg'))
        photo.file.seek(0)
        img = Image.open(io.BytesIO(photo.file.read()))
        self.assertEqual(img.size, (640, 640))

    def test_unreadable_upload_is_kept_untouched(self):
        user = User(username='img-test2')
        user.profile_photo = SimpleUploadedFile('bad.jpg', b'not really an image', content_type='image/jpeg')
        optimize_new_uploads(User, user)
        self.assertEqual(user.profile_photo.name, 'bad.jpg')
