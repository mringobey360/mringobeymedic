from django import forms
from django.core.exceptions import ValidationError
from accounts.phone_utils import normalize_phone_number
from .models import Debt


class DebtEditForm(forms.ModelForm):
    """Edit the customer's details only.

    The amount owed and amount paid are NOT editable here: money changes only through a recorded payment
    (Debts screen / POS deposit) or an approved return, so every shilling stays traceable on the debt's
    timeline and in the cash ledger. A hand-edited "amount paid" would settle a debt without any cash
    ever reaching the drawer or the mobile money account."""

    class Meta:
        model = Debt
        fields = ['customer_name', 'customer_phone']
        widgets = {
            'customer_name': forms.TextInput(attrs={'class': 'form-input'}),
            'customer_phone': forms.TextInput(attrs={'class': 'form-input', 'placeholder': '0712345678 or +255712345678', 'inputmode': 'tel', 'autocomplete': 'tel'}),
        }

    def clean_customer_phone(self):
        raw = self.cleaned_data.get('customer_phone', '').strip()
        if not raw:
            return ''
        try:
            return normalize_phone_number(raw)
        except Exception as exc:
            raise forms.ValidationError(str(exc))
