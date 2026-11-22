from django import forms
from django.test import SimpleTestCase

from . import bulk_import as bi
from .forms import ADD_NEW_VALUE, UnitChoiceField, normalize_unit_name
from .models import Product, unit_label


class NormalizeUnitNameTests(SimpleTestCase):
    def test_standard_units_map_to_their_code(self):
        self.assertEqual(normalize_unit_name('box'), 'BOX')
        self.assertEqual(normalize_unit_name(' Tablet '), 'TABLET')
        self.assertEqual(normalize_unit_name('CARTON'), 'CARTON')

    def test_custom_units_are_tidied(self):
        self.assertEqual(normalize_unit_name('crate  of 24'), 'Crate of 24')
        self.assertEqual(normalize_unit_name('Litre Jug'), 'Litre Jug')
        self.assertEqual(normalize_unit_name(''), '')

    def test_new_standard_units_exist(self):
        codes = {c for c, _ in Product.Unit.choices}
        self.assertTrue({'PACKET', 'BAG', 'DOZEN', 'CRATE', 'SET', 'BUNDLE'} <= codes)


class UnitFieldTests(SimpleTestCase):
    def field(self, **kw):
        f = UnitChoiceField(**kw)
        f.choices = [('', '---'), ('BOX', 'Box')]
        return f

    def test_standard_value_is_accepted(self):
        self.assertEqual(self.field().clean('BOX'), 'BOX')

    def test_a_new_custom_unit_is_accepted_and_tidied(self):
        self.assertEqual(self.field().clean('jerrycan'), 'Jerrycan')

    def test_blank_is_allowed_only_when_optional(self):
        self.assertEqual(self.field(required=False).clean(''), '')
        with self.assertRaises(forms.ValidationError):
            self.field().clean('')

    def test_bad_names_are_rejected(self):
        for bad in ('<script>', 'x' * 31, ADD_NEW_VALUE):
            with self.assertRaises(forms.ValidationError):
                self.field().clean(bad)


class UnitLabelTests(SimpleTestCase):
    def test_standard_code_shows_its_label_and_custom_shows_as_typed(self):
        self.assertEqual(unit_label('BOX'), 'Box')
        self.assertEqual(unit_label('Crate of 24'), 'Crate of 24')
        self.assertEqual(Product(unit='CARTON').get_unit_display(), 'Carton')
        self.assertEqual(Product(unit='Jerrycan').get_unit_display(), 'Jerrycan')


class ExcelCustomUnitTests(SimpleTestCase):
    UNITS = ['TABLET', 'BOX', 'Crate of 24']

    def test_custom_unit_matches_ignoring_case(self):
        self.assertEqual(bi.normalise_unit('crate of 24', self.UNITS, 'Base unit'), 'Crate of 24')

    def test_standard_units_still_work(self):
        self.assertEqual(bi.normalise_unit('tablets', self.UNITS, 'Base unit'), 'TABLET')

    def test_unknown_unit_is_still_rejected(self):
        with self.assertRaises(ValueError):
            bi.normalise_unit('zzzz', self.UNITS, 'Base unit')
