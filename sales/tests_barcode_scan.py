from django.test import SimpleTestCase

from .views import barcode_variants


class BarcodeVariantTests(SimpleTestCase):
    def test_ean13_matches_gtin14_form(self):
        self.assertIn('06291041500213', barcode_variants('6291041500213'))
        self.assertIn('6291041500213', barcode_variants('06291041500213'))

    def test_upca_matches_ean13_form(self):
        self.assertIn('0012345678905', barcode_variants('012345678905'))

    def test_internal_and_text_codes_are_untouched(self):
        self.assertEqual(barcode_variants('PM123456789012'), ['PM123456789012'])
        self.assertEqual(barcode_variants(''), [])


class ExcelBarcodeKeyTests(SimpleTestCase):
    """Excel import and the scan sheet must treat the same pack as the same barcode."""

    def test_canonical_key_is_equal_for_padded_forms(self):
        from inventory.bulk_import import barcode_key
        self.assertEqual(barcode_key('6291041500213'), barcode_key('06291041500213'))
        self.assertEqual(barcode_key('012345678905'), barcode_key('0012345678905'))

    def test_canonical_key_keeps_text_codes(self):
        from inventory.bulk_import import barcode_key
        self.assertEqual(barcode_key('PM123456789012'), 'PM123456789012')
        self.assertNotEqual(barcode_key('PM123456789012'), barcode_key('PM123456789013'))
