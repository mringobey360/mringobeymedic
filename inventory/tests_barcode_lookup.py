from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from . import barcode_lookup as bl
from .models import BarcodeCatalog

OFF_OK = (bl._OK, {'name': 'Drinking Water', 'brand': 'Azam', 'quantity': '1.5 L', 'image_url': '', 'source': 'openfoodfacts'})
OBF_OK = (bl._OK, {'name': 'Body Lotion', 'brand': 'Nivea', 'quantity': '400 ml', 'image_url': '', 'source': 'openbeautyfacts'})
UPC_OK = (bl._OK, {'name': 'Cooking Pot', 'brand': 'Tefal', 'quantity': '', 'image_url': '', 'source': 'upcitemdb'})
UNKNOWN = (bl._UNKNOWN, None)
ERROR = (bl._ERROR, None)


class DisplayNameTests(SimpleTestCase):
    def test_brand_and_size_are_added_once(self):
        self.assertEqual(bl.build_display_name('Drinking Water', 'Azam', '1.5 L'), 'Azam Drinking Water 1.5 L')

    def test_no_repeated_words(self):
        self.assertEqual(bl.build_display_name('Azam Drinking Water 1.5 L', 'Azam', '1.5L'), 'Azam Drinking Water 1.5 L')

    def test_empty_name_gives_empty(self):
        self.assertEqual(bl.build_display_name('', 'Azam', '1L'), '')

    def test_only_real_retail_numbers_are_candidates(self):
        self.assertTrue(bl.is_lookup_candidate('6291041500213'))
        self.assertFalse(bl.is_lookup_candidate('PM000100000001'))
        self.assertFalse(bl.is_lookup_candidate('12345'))


class LookupFlowTests(TestCase):
    CODE = '6291041500213'

    def setUp(self):
        cache.clear()
        self.q = {}
        for name in ('_query_open_food_facts', '_query_open_beauty_facts', '_query_open_products_facts', '_query_upcitemdb'):
            patcher = mock.patch.object(bl, name, return_value=UNKNOWN)
            self.q[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.off, self.obf = self.q['_query_open_food_facts'], self.q['_query_open_beauty_facts']
        self.opf, self.upc = self.q['_query_open_products_facts'], self.q['_query_upcitemdb']

    def test_open_food_facts_hit_is_saved_and_reused_without_network(self):
        self.off.return_value = OFF_OK
        first = bl.lookup_barcode(self.CODE)
        self.assertEqual(first['display_name'], 'Azam Drinking Water 1.5 L')
        self.obf.assert_not_called()
        again = bl.lookup_barcode('0' + self.CODE)          # GTIN-14 form of the same pack
        self.assertEqual(again['display_name'], 'Azam Drinking Water 1.5 L')
        self.assertEqual(self.off.call_count, 1)
        self.assertEqual(BarcodeCatalog.objects.count(), 1)

    def test_cosmetics_are_found_in_open_beauty_facts(self):
        self.obf.return_value = OBF_OK
        found = bl.lookup_barcode(self.CODE)
        self.assertEqual(found['source'], 'openbeautyfacts')
        self.assertEqual(found['display_name'], 'Nivea Body Lotion 400 ml')
        self.upc.assert_not_called()

    def test_upcitemdb_is_the_last_backup(self):
        self.upc.return_value = UPC_OK
        found = bl.lookup_barcode(self.CODE)
        self.assertEqual(found['source'], 'upcitemdb')
        self.assertEqual(found['display_name'], 'Tefal Cooking Pot')

    def test_unknown_everywhere_is_remembered(self):
        self.assertIsNone(bl.lookup_barcode(self.CODE))
        self.assertIsNone(bl.lookup_barcode(self.CODE))
        self.assertEqual(self.off.call_count, 1)
        self.assertFalse(BarcodeCatalog.objects.get(barcode=self.CODE).found)

    def test_network_error_is_not_remembered(self):
        self.off.return_value = ERROR
        self.obf.return_value = ERROR
        self.opf.return_value = ERROR
        self.upc.return_value = ERROR
        self.assertIsNone(bl.lookup_barcode(self.CODE))
        self.assertFalse(BarcodeCatalog.objects.exists())
        self.off.return_value = OFF_OK
        self.assertIsNotNone(bl.lookup_barcode(self.CODE))

    def test_one_service_error_means_not_remembered_as_unknown(self):
        self.upc.return_value = ERROR            # quota used up / timeout
        self.assertIsNone(bl.lookup_barcode(self.CODE))
        self.assertFalse(BarcodeCatalog.objects.exists())

    def test_crash_inside_a_service_never_breaks_the_form(self):
        self.off.side_effect = RuntimeError('boom')
        self.upc.return_value = UPC_OK
        self.assertEqual(bl.lookup_barcode(self.CODE)['source'], 'upcitemdb')

    def test_internal_labels_are_never_sent_out(self):
        self.assertIsNone(bl.lookup_barcode('PM000100000001'))
        for q in self.q.values():
            q.assert_not_called()

    @mock.patch.object(bl, 'is_lookup_enabled', return_value=False)
    def test_can_be_switched_off(self, _):
        self.assertIsNone(bl.lookup_barcode(self.CODE))
        self.off.assert_not_called()


class LearnFromRegistrationTests(TestCase):
    CODE = '6291041500213'

    def setUp(self):
        cache.clear()

    def test_registered_name_is_found_next_time_without_network(self):
        bl.remember_registered_name(self.CODE, 'Azam Water 500ml')
        with mock.patch.object(bl, '_query_open_food_facts') as off:
            found = bl.lookup_barcode(self.CODE)
        off.assert_not_called()
        self.assertEqual(found['display_name'], 'Azam Water 500ml')
        self.assertTrue(found['from_shop'])

    def test_it_replaces_an_earlier_unknown_marker(self):
        BarcodeCatalog.objects.create(barcode=self.CODE, found=False)
        bl.remember_registered_name(self.CODE, 'Azam Water 500ml')
        row = BarcodeCatalog.objects.get(barcode=self.CODE)
        self.assertTrue(row.found)
        self.assertEqual(row.name, 'Azam Water 500ml')

    def test_it_never_overwrites_a_catalogue_name(self):
        BarcodeCatalog.objects.create(barcode=self.CODE, found=True, name='Drinking Water', brand='Azam', source='openfoodfacts')
        bl.remember_registered_name(self.CODE, 'my own spelling')
        self.assertEqual(BarcodeCatalog.objects.get(barcode=self.CODE).name, 'Drinking Water')

    def test_internal_labels_and_empty_names_are_ignored(self):
        bl.remember_registered_name('PM000100000001', 'Panadol')
        bl.remember_registered_name(self.CODE, '   ')
        self.assertFalse(BarcodeCatalog.objects.exists())
