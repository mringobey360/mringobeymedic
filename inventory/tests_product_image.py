from django.test import SimpleTestCase


class ProductImageRulesTests(SimpleTestCase):
    def test_upload_path_is_branch_scoped_and_unguessable(self):
        from types import SimpleNamespace
        from .models import product_image_path
        a = product_image_path(SimpleNamespace(pharmacy_id=7), 'My Photo.PNG')
        b = product_image_path(SimpleNamespace(pharmacy_id=7), 'My Photo.PNG')
        self.assertTrue(a.startswith('product_images/7/') and a.endswith('.png'))
        self.assertNotEqual(a, b)
        self.assertTrue(product_image_path(SimpleNamespace(pharmacy_id=7), 'x.exe').endswith('.jpg'))

    def test_product_photos_use_the_light_stock_profile(self):
        from core.image_processing import profile_for
        self.assertEqual(profile_for('inventory.Product', 'image').max_side, 900)
