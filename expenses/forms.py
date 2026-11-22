from decimal import Decimal

from django import forms
from django.utils import timezone
from .models import Expense, CashDay, ExpenseCategory, CashTransfer

class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['expense_date','category','title','description','amount','payment_source','reference','receipt']
        widgets = {
            'expense_date': forms.DateInput(attrs={'type':'date','class':'form-input'}),
            'category': forms.Select(attrs={'class':'form-input'}),
            'title': forms.TextInput(attrs={'class':'form-input','placeholder':'e.g. LUKU electricity'}),
            'description': forms.Textarea(attrs={'class':'form-input','rows':3,'placeholder':'What was paid and why?'}),
            'amount': forms.NumberInput(attrs={'class':'form-input currency-field','min':'1','step':'0.01','inputmode':'decimal','autocomplete':'off'}),
            'payment_source': forms.Select(attrs={'class':'form-input'}),
            'reference': forms.TextInput(attrs={'class':'form-input','placeholder':'Receipt / transaction reference (optional)'}),
            'receipt': forms.ClearableFileInput(attrs={'class':'form-input'}),
        }
    def __init__(self, *args, pharmacy=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.pharmacy = pharmacy
        self.user = user
        if pharmacy:
            self.fields['category'].queryset = ExpenseCategory.objects.filter(pharmacy__isnull=True, is_active=True) | ExpenseCategory.objects.filter(pharmacy=pharmacy, is_active=True)
        self.fields['expense_date'].initial = timezone.localdate()
        self.fields['category'].empty_label = 'Select a category'
        self.fields['title'].label = 'Title'

class CashOpenForm(forms.ModelForm):
    physical_count_confirmed = forms.BooleanField(
        required=False,
        label='I physically counted the cash in the drawer',
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox h-4 w-4'}),
        help_text='Required when creating the first/manual cash-control anchor. The system will never infer physical cash from sales.',
    )

    class Meta:
        model = CashDay
        fields = ['date','opening_cash','opening_note']
        widgets = {
            'date': forms.DateInput(attrs={'type':'date','class':'form-input'}),
            'opening_cash': forms.NumberInput(attrs={'class':'form-input currency-field','min':'0','step':'0.01','inputmode':'decimal','autocomplete':'off','placeholder':'e.g. 250,000'}),
            'opening_note': forms.TextInput(attrs={'class':'form-input','placeholder':'Optional opening note'}),
        }
    def clean_date(self):
        d=self.cleaned_data['date']
        if d > timezone.localdate(): raise forms.ValidationError('Cash day cannot be opened for a future date.')
        return d

class CashCloseForm(forms.ModelForm):
    physical_count_confirmed = forms.BooleanField(
        required=True,
        label='I physically counted and verified the cash in the drawer',
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox h-4 w-4'}),
        help_text='Enter the cash you actually counted. The system calculates the expected amount and variance for you.',
    )

    class Meta:
        model = CashDay
        fields = ['actual_closing_cash','closing_note']
        widgets = {
            'actual_closing_cash': forms.NumberInput(attrs={'class':'form-input currency-field','min':'0','step':'0.01','required':True,'inputmode':'decimal','autocomplete':'off','placeholder':'e.g. 350,000'}),
            'closing_note': forms.Textarea(attrs={'class':'form-input','rows':3,'placeholder':'Explain any shortage or overage. For an exact match, a short note is optional.'}),
        }

    def clean(self):
        cleaned = super().clean()
        physical = cleaned.get('actual_closing_cash')
        if physical is not None and self.instance and self.instance.pk:
            expected = self.instance.expected_closing_cash
            from .cash_rules import clean_variance
            if clean_variance(physical - expected) != 0 and not (cleaned.get('closing_note') or '').strip():
                self.add_error('closing_note', 'A variance was detected. Please add a short explanation before closing this cash day.')
        return cleaned


class CashOpeningAdjustmentForm(forms.Form):
    opening_cash = forms.DecimalField(
        min_value=Decimal('0'), max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={'class':'form-input currency-field','min':'0','step':'0.01','inputmode':'decimal','autocomplete':'off','placeholder':'e.g. 250,000'}),
        label='Correct opening cash',
    )
    reason = forms.CharField(
        min_length=5,
        widget=forms.Textarea(attrs={'class':'form-input','rows':3,'placeholder':'Explain why the opening cash is being changed.'}),
    )

class ExpenseDecisionForm(forms.Form):
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={'class':'form-input','rows':3,'placeholder':'Approval/rejection note'}))

class CashCountForm(forms.Form):
    physical_cash = forms.DecimalField(
        min_value=Decimal('0'), max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={'class':'form-input currency-field','min':'0','step':'0.01','required':True,'inputmode':'decimal','autocomplete':'off','placeholder':'e.g. 350,000'}),
        label='Physical cash counted (TZS)',
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class':'form-input','rows':3,'placeholder':'Optional note. If there is a shortage or overage, explain it.'}),
        label='Count note',
    )
    physical_count_confirmed = forms.BooleanField(
        required=True,
        label='I physically counted and verified this amount',
        widget=forms.CheckboxInput(attrs={'class':'form-checkbox h-4 w-4'}),
    )


class MobileMoneyCheckForm(forms.Form):
    """Mobile money balance per the shop's statement, and (optionally) a manual opening balance.
    Never touches drawer cash."""
    actual_mobile_balance = forms.DecimalField(
        required=False, min_value=Decimal('0'), max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={'class':'form-input currency-field','min':'0','step':'0.01','inputmode':'decimal','autocomplete':'off','placeholder':'e.g. 450,000'}),
        label='Mobile money balance per statement (TZS)',
    )
    opening_mobile = forms.DecimalField(
        required=False, min_value=Decimal('0'), max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={'class':'form-input currency-field','min':'0','step':'0.01','inputmode':'decimal','autocomplete':'off','placeholder':'Only to set / correct the opening'}),
        label='Opening mobile money balance (TZS)',
    )
    note = forms.CharField(
        required=False, max_length=255,
        widget=forms.TextInput(attrs={'class':'form-input','placeholder':'Optional note, e.g. statement reference or reason for a difference'}),
        label='Note',
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('actual_mobile_balance') is None and cleaned.get('opening_mobile') is None:
            raise forms.ValidationError('Enter the statement balance, the opening balance, or both.')
        return cleaned


class CashTransferForm(forms.Form):
    """Move money between the drawer and the mobile money account."""
    direction = forms.ChoiceField(choices=CashTransfer.Direction.choices, widget=forms.Select(attrs={'class':'form-input'}))
    amount = forms.DecimalField(
        min_value=Decimal('0.01'), max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={'class':'form-input currency-field','min':'0.01','step':'0.01','required':True,'inputmode':'decimal','autocomplete':'off','placeholder':'Amount (TZS)'}),
    )
    reference = forms.CharField(required=False, max_length=100, widget=forms.TextInput(attrs={'class':'form-input','placeholder':'Transaction reference (optional)'}))
    note = forms.CharField(required=False, max_length=255, widget=forms.TextInput(attrs={'class':'form-input','placeholder':'Reason, e.g. banked cash at agent'}))

    def clean(self):
        data = super().clean()
        ref = (data.get('reference') or '').strip()
        if data.get('direction') == CashTransfer.Direction.MOBILE_TO_CASH and len(ref) < 4:
            self.add_error('reference', 'Enter the mobile money transaction ID (at least 4 characters). Every cash-out must be traceable to a statement entry.')
        return data


class CashCountCorrectionForm(forms.Form):
    physical_cash = forms.DecimalField(
        min_value=Decimal('0'), max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={'class':'form-input currency-field','min':'0','step':'0.01','required':True,'inputmode':'decimal','autocomplete':'off','placeholder':'e.g. 350,000'}),
        label='Correct physical cash (TZS)',
    )
    correction_reason = forms.CharField(
        min_length=12,
        widget=forms.Textarea(attrs={'class':'form-input','rows':3,'required':True,'placeholder':'Example: First count was entered incorrectly; drawer recounted and verified.'}),
        label='Reason for correction',
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class':'form-input','rows':3,'placeholder':'Optional note about the recount.'}),
        label='Recount note',
    )
    physical_count_confirmed = forms.BooleanField(
        required=True,
        label='I physically recounted and verified this amount',
        widget=forms.CheckboxInput(attrs={'class':'form-checkbox h-4 w-4'}),
    )
