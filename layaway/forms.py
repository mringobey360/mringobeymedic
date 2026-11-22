from django import forms
from accounts.phone_utils import normalize_phone_number
from inventory.models import Product
from .models import LayawayPlan, LayawayPayment

INPUT = 'spx-input'


class LayawayCreateForm(forms.ModelForm):
    # How the opening deposit was received. Not stored on the plan: it becomes the first payment's method,
    # so the cash ledger and mobile-money reports at handover reflect what really happened.
    deposit_method = forms.ChoiceField(choices=LayawayPayment.Method.choices, initial=LayawayPayment.Method.CASH,
                                       required=False, label='Deposit method')
    deposit_reference = forms.CharField(max_length=100, required=False, label='Deposit reference',
                                        widget=forms.TextInput(attrs={'placeholder': 'e.g. M-Pesa code (optional)', 'autocomplete': 'off'}))

    class Meta:
        model = LayawayPlan
        fields = ['product', 'quantity', 'customer_name', 'customer_phone', 'unit_price', 'deposit', 'due_date', 'notes']
        labels = {
            'product': 'Product',
            'quantity': 'Quantity',
            'customer_name': 'Customer name',
            'customer_phone': 'Customer phone',
            'unit_price': 'Agreed unit price',
            'deposit': 'Initial deposit',
            'due_date': 'Final payment date',
            'notes': 'Notes',
        }
        widgets = {
            'quantity': forms.NumberInput(attrs={'min': 1, 'step': 1, 'inputmode': 'numeric', 'id': 'id_quantity'}),
            'customer_name': forms.TextInput(attrs={'placeholder': 'e.g. Amina Juma', 'autocomplete': 'off', 'maxlength': 150}),
            'customer_phone': forms.TextInput(attrs={'placeholder': '0712 345 678', 'type': 'tel', 'inputmode': 'tel', 'autocomplete': 'off'}),
            'unit_price': forms.NumberInput(attrs={'min': 0, 'step': 'any', 'inputmode': 'decimal', 'placeholder': '0', 'id': 'id_unit_price'}),
            'deposit': forms.NumberInput(attrs={'min': 0, 'step': 'any', 'inputmode': 'decimal', 'placeholder': '0', 'id': 'id_deposit'}),
            'due_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.TextInput(attrs={'maxlength': 255, 'placeholder': 'Optional, e.g. customer will collect on Friday'}),
        }

    def __init__(self, *args, pharmacy=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.pharmacy = pharmacy
        if pharmacy is not None:
            self.fields['product'].queryset = Product.objects.filter(pharmacy=pharmacy, is_active=True).order_by('name')
        # The visible product picker is a searchable combobox (see form.html); the real
        # <select> stays in the DOM as the submitted value, hidden from view.
        self.fields['product'].widget.attrs.update({'id': 'id_product', 'class': 'lp-native'})
        self.fields['product'].empty_label = 'Select a product'
        self.fields['due_date'].required = False
        for name, f in self.fields.items():
            if name == 'product':
                continue
            existing = f.widget.attrs.get('class', '')
            f.widget.attrs['class'] = f'{INPUT} {existing}'.strip()

    def clean_deposit_method(self):
        return self.cleaned_data.get('deposit_method') or LayawayPayment.Method.CASH

    def clean_customer_name(self):
        name = ' '.join((self.cleaned_data.get('customer_name') or '').split())
        if len(name) < 2:
            raise forms.ValidationError('Enter the customer\'s full name.')
        return name.title() if name.isupper() or name.islower() else name

    def clean_customer_phone(self):
        # Same normaliser used across SmartPOS, so 0712..., 255712... and +255712... all match.
        return normalize_phone_number(self.cleaned_data.get('customer_phone') or '')

    def clean(self):
        data = super().clean()
        product, qty = data.get('product'), data.get('quantity')
        if product and qty:
            from .services import reservation_for_product
            reserved = reservation_for_product(product)['qty']
            available = max(0, product.sellable_quantity - reserved)
            if qty > available:
                self.add_error('quantity', f'Only {available} unreserved sellable unit(s) are available for {product.name}; {reserved} unit(s) are already reserved for Layaway. Restock first or reduce the quantity.')
        return data


class LayawayPaymentForm(forms.ModelForm):
    class Meta:
        model = LayawayPayment
        fields = ['amount', 'method', 'transaction_reference', 'note']
        labels = {'amount': 'Amount received (TZS)', 'method': 'Payment method',
                  'transaction_reference': 'Transaction reference', 'note': 'Note'}
        widgets = {
            'amount': forms.NumberInput(attrs={'min': 0, 'step': 'any', 'inputmode': 'decimal', 'placeholder': '0', 'id': 'id_amount'}),
            'transaction_reference': forms.TextInput(attrs={'placeholder': 'Optional', 'autocomplete': 'off'}),
            'note': forms.TextInput(attrs={'placeholder': 'Optional', 'autocomplete': 'off'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs['class'] = f"{INPUT} {f.widget.attrs.get('class', '')}".strip()
