import re

from django import forms
from django.urls import reverse_lazy
from django.utils import timezone
from django.db.models.functions import Lower
from decimal import Decimal

from .barcodes import barcode_variants
from .models import Product, ProductBatch, Category, UnitCatalog, UNIT_MAX_LENGTH


# --------------------------------------------------------------------------- #
# Units: the standard list plus any unit the branch adds itself ("Add new unit")
# --------------------------------------------------------------------------- #
UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .'/&()+-]*$")
ADD_NEW_VALUE = '__new__'


def normalize_unit_name(value):
    """'crate' -> 'Crate'; 'box' / 'BOX' -> the standard 'BOX'; 'carton of 24' -> 'Carton of 24'."""
    value = ' '.join(str(value or '').split())
    if not value:
        return ''
    for code, label in Product.Unit.choices:
        if value.upper() == code or value.casefold() == label.casefold():
            return code
    return value[:1].upper() + value[1:] if value.islower() else value


def unit_choices(pharmacy, blank=False, extra=''):
    """Standard units + the custom units this branch already uses (+ one just typed, when re-showing a form)."""
    known = {code for code, _ in Product.Unit.choices}
    custom = {}
    if pharmacy is not None:
        used = list(UnitCatalog.objects.filter(pharmacy=pharmacy).values_list('name', flat=True))
        used += list(Product.objects.filter(pharmacy=pharmacy).exclude(unit__in=known).values_list('unit', flat=True).distinct())
        used += list(ProductBatch.objects.filter(product__pharmacy=pharmacy).exclude(purchase_unit__in=known)
                     .exclude(purchase_unit='').values_list('purchase_unit', flat=True).distinct())
        for name in used:
            custom.setdefault(name.casefold(), name)
    extra = normalize_unit_name(extra)
    if extra and extra not in known and extra != ADD_NEW_VALUE:
        custom.setdefault(extra.casefold(), extra)
    choices = [('', '---------')] if blank else []
    choices += list(Product.Unit.choices)
    choices += [(name, name) for name in sorted(custom.values(), key=str.casefold)]
    return choices


class UnitChoiceField(forms.ChoiceField):
    """A unit dropdown that also accepts a new unit typed through "Add new unit"."""
    widget = forms.Select

    def validate(self, value):
        if value in self.empty_values and self.required:
            raise forms.ValidationError(self.error_messages['required'], code='required')

    def clean(self, value):
        value = self.to_python(value)
        if value == ADD_NEW_VALUE:
            raise forms.ValidationError('Enter the name of the new unit, or choose a unit from the list.')
        value = normalize_unit_name(value)
        self.validate(value)
        if not value:
            return ''
        if len(value) > UNIT_MAX_LENGTH or not UNIT_NAME_RE.match(value):
            raise forms.ValidationError(f'Unit names may use letters, numbers and spaces only, up to {UNIT_MAX_LENGTH} characters.')
        return value


def _unit_widget(label_kind='unit'):
    attrs = {'class': 'form-input', 'data-inline-add': label_kind}
    if label_kind == 'unit':
        attrs['data-inline-url'] = reverse_lazy('inventory:unit_quick_add')
    return forms.Select(attrs=attrs)


def _posted(form, name):
    return form.data.get(form.add_prefix(name), '') if form.is_bound else ''


PRODUCT_IMAGE_MAX_BYTES = 5 * 1024 * 1024
PRODUCT_IMAGE_FORMATS = {'JPEG', 'PNG', 'WEBP'}


class ProductForm(forms.ModelForm):
    """Catalog information only. Stock and pricing are recorded on batches."""

    image = forms.ImageField(
        required=False, label='Product photo (optional)',
        widget=forms.FileInput(attrs={'accept': 'image/jpeg,image/png,image/webp', 'class': 'sr-only', 'data-product-image-input': '1'}),
        help_text='JPG, PNG or WebP, up to 5 MB. You can add or change it later.',
    )
    remove_image = forms.BooleanField(required=False, widget=forms.HiddenInput(attrs={'data-product-image-remove': '1'}))

    unit = UnitChoiceField(
        label='Unit', initial=Product.Unit.TABLET, widget=_unit_widget(),
        help_text='Smallest unit used for stock and sales. Not listed? Choose "Add new unit".',
    )

    class Meta:
        model = Product
        fields = ['name', 'sku', 'barcode', 'category', 'unit', 'image', 'reorder_level', 'requires_expiry', 'discount_enabled', 'discount_type', 'min_discount_percent', 'max_discount_percent', 'min_discount_amount', 'max_discount_amount']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Panadol 500mg'}),
            'sku': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Leave blank to auto-generate'}),
            'barcode': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Leave blank to auto-generate'}),
            'category': forms.Select(attrs={'class': 'form-input', 'data-inline-add': 'category', 'data-inline-url': reverse_lazy('inventory:category_quick_add')}),
            'reorder_level': forms.NumberInput(attrs={'class': 'form-input', 'min': '0'}),
            'requires_expiry': forms.CheckboxInput(attrs={'class': 'h-4 w-4'}),
            'discount_enabled': forms.CheckboxInput(attrs={'class': 'h-4 w-4'}),
            'discount_type': forms.Select(attrs={'class': 'form-input'}),
            'min_discount_amount': forms.NumberInput(attrs={'class': 'form-input currency-field', 'min': '0', 'step': '1', 'placeholder': 'e.g. 0'}),
            'max_discount_amount': forms.NumberInput(attrs={'class': 'form-input currency-field', 'min': '0', 'step': '1', 'placeholder': 'e.g. 2,000'}),
            'min_discount_percent': forms.NumberInput(attrs={'class': 'form-input', 'min': '0', 'max': '100', 'step': '0.01'}),
            'max_discount_percent': forms.NumberInput(attrs={'class': 'form-input', 'min': '0', 'max': '100', 'step': '0.01'}),
        }
        labels = {
            'requires_expiry': 'Has expiry date (e.g. medicines)',
            'discount_type': 'Discount type',
            'min_discount_percent': 'Minimum discount (%)',
            'max_discount_percent': 'Maximum discount (%)',
            'min_discount_amount': 'Minimum discount (TZS per unit)',
            'max_discount_amount': 'Maximum discount (TZS per unit)',
        }
        help_texts = {
            'requires_expiry': 'When on, every new batch of this product must have an expiry date. Turn off for items that do not expire, such as cosmetics and accessories.',
        }

    def __init__(self, *args, pharmacy=None, **kwargs):
        self.pharmacy = pharmacy
        super().__init__(*args, **kwargs)
        # Never expose another pharmacy's categories in a product form.
        if pharmacy is not None:
            self.fields['category'].queryset = Category.objects.filter(
                pharmacy=pharmacy
            ).order_by(Lower('name'), 'name')
        else:
            self.fields['category'].queryset = Category.objects.none()
        self.fields['unit'].choices = unit_choices(pharmacy, extra=_posted(self, 'unit'))
        self._previous_image_name = (self.instance.image.name if self.instance.pk and self.instance.image else '')
        if self.instance.pk and self.instance.unit:
            self.initial.setdefault('unit', self.instance.unit)

    def clean_image(self):
        upload = self.cleaned_data.get('image')
        # No new file (None) or an unchanged existing file: nothing to validate.
        if not upload or not hasattr(upload, 'content_type'):
            return upload
        if upload.size > PRODUCT_IMAGE_MAX_BYTES:
            raise forms.ValidationError('This photo is larger than 5 MB. Choose a smaller photo or take a new one.')
        fmt = getattr(getattr(upload, 'image', None), 'format', None)
        if fmt not in PRODUCT_IMAGE_FORMATS:
            raise forms.ValidationError('Use a JPG, PNG or WebP photo.')
        return upload

    def save(self, commit=True):
        obj = super().save(commit=False)
        new_upload = bool(self.files.get(self.add_prefix('image')))
        if self.cleaned_data.get('remove_image') and not new_upload:
            obj.image = None
        if commit:
            obj.save()
            self.save_m2m()
            self._discard_replaced_image(obj)
        return obj

    def _discard_replaced_image(self, obj):
        """Delete the old file once the new state is saved (replace or remove)."""
        old = self._previous_image_name
        current = obj.image.name if obj.image else ''
        if old and old != current:
            from django.db import transaction
            storage = obj._meta.get_field('image').storage

            def _delete():
                try:
                    storage.delete(old)
                except Exception:  # pragma: no cover
                    pass
            transaction.on_commit(_delete)

    def clean_category(self):
        category = self.cleaned_data.get('category')
        if category is not None and self.pharmacy is not None and category.pharmacy_id != self.pharmacy.id:
            raise forms.ValidationError('Selected category does not belong to this branch.')
        return category

    def clean_sku(self):
        value = (self.cleaned_data.get('sku') or '').strip().upper()
        if value:
            qs = Product.objects.filter(pharmacy=self.pharmacy, sku=value)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError('This SKU is already used by another product at this branch.')
        return value

    def clean_barcode(self):
        value = (self.cleaned_data.get('barcode') or '').strip()
        if value:
            qs = Product.objects.filter(pharmacy=self.pharmacy, barcode__in=barcode_variants(value))
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError('This barcode is already used by another product at this branch.')
        return value

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        qs = Product.objects.filter(pharmacy=self.pharmacy, name__iexact=name, is_active=True)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A product with this name already exists at this branch.")
        return name

    def clean(self):
        cleaned = super().clean()
        enabled = cleaned.get('discount_enabled')
        dtype = cleaned.get('discount_type') or Product.DiscountType.PERCENT
        if dtype == Product.DiscountType.AMOUNT:
            minimum = cleaned.get('min_discount_amount') or Decimal('0')
            maximum = cleaned.get('max_discount_amount') or Decimal('0')
            if minimum < 0:
                self.add_error('min_discount_amount', 'Minimum discount cannot be negative.')
            if maximum < 0:
                self.add_error('max_discount_amount', 'Maximum discount cannot be negative.')
            if minimum > maximum:
                self.add_error('max_discount_amount', 'Maximum discount cannot be less than minimum discount.')
            if enabled and maximum <= 0:
                self.add_error('max_discount_amount', 'Set a maximum discount amount greater than 0 when discounts are enabled.')
            return cleaned
        minimum = cleaned.get('min_discount_percent') or Decimal('0')
        maximum = cleaned.get('max_discount_percent') or Decimal('0')
        if minimum < 0 or minimum > 100:
            self.add_error('min_discount_percent', 'Minimum discount must be between 0% and 100%.')
        if maximum < 0 or maximum > 100:
            self.add_error('max_discount_percent', 'Maximum discount must be between 0% and 100%.')
        if minimum > maximum:
            self.add_error('max_discount_percent', 'Maximum discount cannot be less than minimum discount.')
        if enabled and maximum <= 0:
            self.add_error('max_discount_percent', 'Set a maximum discount greater than 0% when discounts are enabled.')
        return cleaned


class ProductQuickForm(ProductForm):
    """The essentials for registering at the counter (scan sheet).

    Same model, same validation as ProductForm; discount policy is left at its defaults
    and can be set later from Edit Product. SKU is auto-generated.
    """

    class Meta(ProductForm.Meta):
        fields = ['name', 'barcode', 'category', 'unit', 'reorder_level', 'requires_expiry']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['barcode'].widget.attrs['readonly'] = True
        self.fields['barcode'].required = False


class ProductBatchForm(forms.ModelForm):
    """Receive stock using either the base unit or an optional purchase package."""

    purchase_unit = UnitChoiceField(
        required=False, label='Purchase Unit (Optional)', widget=_unit_widget(),
        help_text='Example: Box. Leave blank when buying directly in the base unit.',
    )

    class Meta:
        model = ProductBatch
        fields = ['batch_number', 'purchase_unit', 'purchase_quantity', 'units_per_purchase_unit',
                  'purchase_unit_price', 'selling_price', 'discount_cap_amount', 'expiry_date', 'note']
        widgets = {
            'batch_number': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. BATCH-2026-014 (optional)'}),
            'discount_cap_amount': forms.NumberInput(attrs={'class': 'form-input currency-field', 'step': '0.01', 'min': '0', 'placeholder': 'Blank = product policy · 0 = no discount'}),
            'purchase_quantity': forms.NumberInput(attrs={'class': 'form-input', 'min': '1', 'step': '1'}),
            'units_per_purchase_unit': forms.NumberInput(attrs={'class': 'form-input', 'min': '1', 'step': '1'}),
            'purchase_unit_price': forms.NumberInput(attrs={'class': 'form-input currency-field', 'step': '0.01', 'min': '0', 'placeholder': 'e.g. 15,000'}),
            'selling_price': forms.NumberInput(attrs={'class': 'form-input currency-field', 'step': '0.01', 'min': '0', 'placeholder': 'e.g. 20,000'}),
            'expiry_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'note': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Delivered by ABC Distributors'}),
        }
        labels = {
            'purchase_unit': 'Purchase Unit (Optional)',
            'purchase_quantity': 'Quantity Purchased',
            'units_per_purchase_unit': 'Base Units per Purchase Unit',
            'purchase_unit_price': 'Purchase Price per Purchase Unit',
            'selling_price': 'Selling Price per Base Unit',
            'discount_cap_amount': 'Maximum Discount for this Batch (TZS per unit, optional)',
            'expiry_date': 'Expiry Date (Optional)',
        }
        help_texts = {
            'expiry_date': 'Leave blank for products that do not expire. Medicines and other perishables should always have one.',
            'purchase_unit': 'Example: Box. Leave blank when buying directly in the base unit.',
            'units_per_purchase_unit': 'Example: 100 if 1 Box contains 100 Tablets. Use 1 when no conversion is needed.',
            'purchase_unit_price': 'Example: TSh 4,200 for one Box. The system calculates the base-unit cost automatically.',
            'selling_price': 'The price charged for one base unit in the POS.',
        }

    def __init__(self, *args, require_expiry=True, pharmacy=None, **kwargs):
        # require_expiry mirrors the owning Product.requires_expiry flag: pass
        # False for products explicitly marked as non-expiring (e.g. cosmetics)
        # so this batch's expiry_date can stay blank.
        self.require_expiry = require_expiry
        super().__init__(*args, **kwargs)
        self.fields['purchase_unit'].choices = unit_choices(pharmacy, blank=True, extra=_posted(self, 'purchase_unit'))

    def clean(self):
        cleaned = super().clean()
        qty = cleaned.get('purchase_quantity')
        conversion = cleaned.get('units_per_purchase_unit')
        purchase_unit = cleaned.get('purchase_unit')
        purchase_unit_price = cleaned.get('purchase_unit_price')
        selling = cleaned.get('selling_price')
        if qty is not None and qty < 1:
            self.add_error('purchase_quantity', 'Quantity must be at least 1.')
        if conversion is not None and conversion < 1:
            self.add_error('units_per_purchase_unit', 'Conversion must be at least 1.')
        if purchase_unit_price is None or purchase_unit_price <= 0:
            self.add_error('purchase_unit_price', 'Purchase price must be greater than zero.')
        if selling is not None and purchase_unit_price and conversion:
            base_cost = (Decimal(purchase_unit_price) / Decimal(conversion)).quantize(Decimal('0.01'))
            if selling < base_cost:
                self.add_error('selling_price', f'Selling price is below the calculated base-unit cost of TSh {base_cost}. Please confirm the price.')
        if not purchase_unit and conversion and conversion != 1:
            self.add_error('purchase_unit', 'Select the purchase unit when using a conversion greater than 1.')
        if self.require_expiry and not cleaned.get('expiry_date'):
            self.add_error(
                'expiry_date',
                'This product requires an expiry date. Enter one, or turn off "Has expiry date" on the product.'
            )
        return cleaned

    def save(self, commit=True):
        batch = super().save(commit=False)
        units = int(batch.units_per_purchase_unit or 1)
        batch.purchase_price = (Decimal(batch.purchase_unit_price) / Decimal(units)).quantize(Decimal('0.01'))
        batch.initial_quantity = int(batch.purchase_quantity or 1) * units
        batch.quantity = batch.initial_quantity
        if commit:
            batch.save()
        return batch


class ProductBatchEditForm(forms.ModelForm):
    """Correct an existing batch without rewriting its historical receipt quantity."""

    purchase_unit = UnitChoiceField(required=False, label='Purchase Unit', widget=_unit_widget())

    class Meta:
        model = ProductBatch
        fields = ['batch_number', 'quantity', 'purchase_unit', 'purchase_quantity', 'units_per_purchase_unit',
                  'purchase_unit_price', 'purchase_price', 'selling_price', 'discount_cap_amount', 'expiry_date', 'note']
        widgets = {
            'batch_number': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. BATCH-2026-014 (optional)'}),
            'discount_cap_amount': forms.NumberInput(attrs={'class': 'form-input currency-field', 'step': '0.01', 'min': '0', 'placeholder': 'Blank = product policy · 0 = no discount'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-input', 'min': '0'}),
            'purchase_quantity': forms.NumberInput(attrs={'class': 'form-input', 'min': '1'}),
            'units_per_purchase_unit': forms.NumberInput(attrs={'class': 'form-input', 'min': '1'}),
            'purchase_unit_price': forms.NumberInput(attrs={'class': 'form-input currency-field', 'step': '0.01', 'min': '0','placeholder':'e.g. 15,000'}),
            'purchase_price': forms.NumberInput(attrs={'class': 'form-input currency-field', 'step': '0.01', 'min': '0','placeholder':'e.g. 15,000'}),
            'selling_price': forms.NumberInput(attrs={'class': 'form-input currency-field', 'step': '0.01', 'min': '0','placeholder':'e.g. 15,000'}),
            'expiry_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'note': forms.TextInput(attrs={'class': 'form-input'}),
        }
        labels = {
            'quantity': 'Quantity Remaining (Base Units)',
            'purchase_unit': 'Purchase Unit',
            'purchase_quantity': 'Original Purchase Quantity',
            'units_per_purchase_unit': 'Base Units per Purchase Unit',
            'purchase_unit_price': 'Purchase Price per Purchase Unit',
            'purchase_price': 'Cost per Base Unit',
            'selling_price': 'Selling Price per Base Unit',
            'discount_cap_amount': 'Maximum Discount for this Batch (TZS per unit, optional)',
            'expiry_date': 'Expiry Date (Optional)',
        }
        help_texts = {
            'expiry_date': 'Leave blank for products that do not expire.',
        }

    def __init__(self, *args, require_expiry=True, **kwargs):
        # Same convention as ProductBatchForm — pass the owning product's
        # requires_expiry flag so edits can't be saved into a blank expiry
        # for products that are supposed to always carry one.
        self.require_expiry = require_expiry
        super().__init__(*args, **kwargs)
        pharmacy = self.instance.product.pharmacy if getattr(self.instance, 'product_id', None) else None
        self.fields['purchase_unit'].choices = unit_choices(pharmacy, blank=True, extra=_posted(self, 'purchase_unit'))

    def clean(self):
        cleaned = super().clean()
        purchase = cleaned.get('purchase_price')
        selling = cleaned.get('selling_price')
        purchase_unit_price = cleaned.get('purchase_unit_price')
        conversion = cleaned.get('units_per_purchase_unit')
        if purchase_unit_price is not None and conversion:
            purchase = (Decimal(purchase_unit_price) / Decimal(conversion)).quantize(Decimal('0.01'))
        if purchase is not None and selling is not None and selling < purchase:
            self.add_error('selling_price', 'Selling price is lower than cost per base unit — please confirm this is intentional.')
        if self.require_expiry and not cleaned.get('expiry_date'):
            self.add_error(
                'expiry_date',
                'This product requires an expiry date. Enter one, or turn off "Has expiry date" on the product.'
            )
        return cleaned

    def save(self, commit=True):
        batch = super().save(commit=False)
        if batch.purchase_unit_price is not None and batch.units_per_purchase_unit:
            batch.purchase_price = (Decimal(batch.purchase_unit_price) / Decimal(batch.units_per_purchase_unit)).quantize(Decimal('0.01'))
        if commit:
            batch.save()
        return batch


class BulkUploadForm(forms.Form):
    import_mode = forms.ChoiceField(
        choices=[
            ('OPENING', 'Opening Stock — never top up an existing batch'),
            ('RESTOCK', 'Restock / Purchase — top up only an exact matching batch'),
        ],
        initial='OPENING',
        widget=forms.Select(attrs={'class': 'form-input'}),
        label='Import Mode',
    )
    excel_file = forms.FileField(
        widget=forms.ClearableFileInput(attrs={'class': 'form-input', 'accept': '.xlsx'})
    )

    def clean_excel_file(self):
        f = self.cleaned_data['excel_file']
        if not f.name.lower().endswith('.xlsx'):
            raise forms.ValidationError("Please upload a valid .xlsx Excel file.")
        if f.size > 5 * 1024 * 1024:
            raise forms.ValidationError("File is too large (max 5MB).")
        return f


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name']
        widgets = {'name': forms.TextInput(attrs={'class': 'form-input'})}
