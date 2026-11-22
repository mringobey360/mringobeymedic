from django import forms
from django.db.models import Q

from accounts.phone_utils import normalize_phone_number
from accounts.models import User
from .models import SignupRequest, PaymentSettings
from sms_integration.models import SMSDebtPackage, SMSReceiptPackage, SMSMarketingPackage


class SignupForm(forms.ModelForm):
    """Public registration form. Open to any kind of business.

    Registration sells ONE thing: the core subscription (plan + billing period).
    SMS / reporting add-ons are bought later from "Subscription & Packages"
    inside the system, so a new customer is never overwhelmed at sign-up.

    Pharmacy Council details (FIN) are required only when the business type is Pharmacy.
    Every other business type may optionally provide a licence / permit.
    """

    class Meta:
        model = SignupRequest
        fields = [
            'business_type', 'business_name', 'owner_first_name', 'owner_last_name',
            'phone_number', 'email', 'location', 'pharmacy_council_fin', 'permit_number', 'permit_expiry_date', 'tier', 'plan',
        ]
        widgets = {
            'business_type': forms.RadioSelect(),
            'business_name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Neema Stores', 'autocomplete': 'organization'}),
            'owner_first_name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'First name', 'autocomplete': 'given-name'}),
            'owner_last_name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Last name', 'autocomplete': 'family-name'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. 0712345678', 'inputmode': 'tel', 'autocomplete': 'tel'}),
            'email': forms.EmailInput(attrs={'class': 'form-input', 'placeholder': 'name@example.com', 'autocomplete': 'email'}),
            'location': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Dodoma, Area D'}),
            'pharmacy_council_fin': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. 0101726', 'inputmode': 'numeric', 'maxlength': '7', 'pattern': r'\d{7}'}),
            'permit_number': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'As printed on the permit'}),
            'permit_expiry_date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}, format='%Y-%m-%d'),
            'tier': forms.RadioSelect(),
            'plan': forms.RadioSelect(),
        }
        labels = {
            'business_type': 'What type of business do you run?',
            'business_name': 'Business name',
            'owner_first_name': 'Owner first name',
            'owner_last_name': 'Owner last name',
            'phone_number': 'Phone number',
            'email': 'Email',
            'location': 'Business location',
            'pharmacy_council_fin': 'Pharmacy Council FIN',
            'permit_number': 'Licence / permit number',
            'permit_expiry_date': 'Licence / permit expiry',
            'tier': 'Choose your plan',
            'plan': 'Billing period',
        }

    ACCOUNT_PAID, ACCOUNT_TRIAL = 'PAID', 'TRIAL'
    account_type = forms.ChoiceField(
        choices=[(ACCOUNT_PAID, 'Paid subscription'), (ACCOUNT_TRIAL, 'Free trial')],
        widget=forms.RadioSelect(), required=False, initial=ACCOUNT_PAID, label='Account type',
    )

    referral_code = forms.CharField(
        required=False, max_length=12, label='Referral code (optional)',
        widget=forms.TextInput(attrs={'class': 'form-input uppercase', 'placeholder': 'e.g. SPK7M2QX', 'autocomplete': 'off'}),
    )

    def clean_referral_code(self):
        from . import referrals
        raw = referrals.normalize(self.cleaned_data.get('referral_code'))
        if not raw:
            return ''
        if not referrals.enabled():
            return ''
        if referrals.find_code(raw) is None:
            raise forms.ValidationError('This referral code was not found. Check it, or leave it empty.')
        return raw

    @property
    def is_trial(self):
        return (self.data.get('account_type') if self.is_bound else None) == self.ACCOUNT_TRIAL

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # No pre-selected business type: the visitor must choose one on purpose.
        bt = self.fields['business_type']
        bt.choices = list(SignupRequest.BusinessType.choices)
        bt.initial = None
        bt.error_messages['required'] = 'Choose the type of business you run.'
        self.fields['permit_expiry_date'].input_formats = ['%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y']

    def clean_pharmacy_council_fin(self):
        value = (self.cleaned_data.get('pharmacy_council_fin') or '').strip()
        if value and (not value.isdigit() or len(value) != 7):
            raise forms.ValidationError('Pharmacy Council FIN must be exactly 7 digits, including leading zeroes where applicable (example: 0101726).')
        return value

    def clean_permit_number(self):
        return (self.cleaned_data.get('permit_number') or '').strip()

    def clean_permit_expiry_date(self):
        from django.utils import timezone
        value = self.cleaned_data.get('permit_expiry_date')
        if value and value < timezone.localdate():
            raise forms.ValidationError('This licence / permit has already expired. Enter the current expiry date, or leave it empty.')
        return value

    def clean(self):
        cleaned = super().clean()
        business_type = cleaned.get('business_type')
        fin = cleaned.get('pharmacy_council_fin')

        if business_type == SignupRequest.BusinessType.PHARMACY:
            # Pharmacies are regulated: the Pharmacy Council FIN is mandatory.
            if not fin and 'pharmacy_council_fin' not in self.errors:
                self.add_error('pharmacy_council_fin', 'Pharmacies must enter their Pharmacy Council FIN (7 digits, e.g. 0101726).')
        elif business_type:
            # Other businesses never carry a Pharmacy Council FIN.
            cleaned['pharmacy_council_fin'] = ''
            fin = ''
            self.errors.pop('pharmacy_council_fin', None)

        # Free trial: Starter features, fixed server-side. Whatever tier/billing the browser sent is ignored.
        if cleaned.get('account_type') == self.ACCOUNT_TRIAL:
            cleaned['tier'] = SignupRequest.Tier.STARTER
            cleaned['plan'] = SignupRequest.Plan.MONTHLY
            self.errors.pop('tier', None)
            self.errors.pop('plan', None)
        else:
            cleaned['account_type'] = self.ACCOUNT_PAID

        if fin:
            from pharmacies.models import Pharmacy
            if Pharmacy.objects.filter(pharmacy_council_fin=fin, is_active=True).exists():
                self.add_error('pharmacy_council_fin', 'This Pharmacy Council FIN is already registered in the system. Use the FIN belonging to this pharmacy.')
            if SignupRequest.objects.filter(pharmacy_council_fin=fin).exclude(status=SignupRequest.Status.REJECTED).exists():
                self.add_error('pharmacy_council_fin', 'This Pharmacy Council FIN already has a pending or active registration request.')
        return cleaned

    def save(self, commit=True):
        self.instance.is_trial = self.cleaned_data.get('account_type') == self.ACCOUNT_TRIAL
        signup = super().save(commit=commit)
        if commit:
            from . import referrals
            code = referrals.find_code(self.cleaned_data.get('referral_code'))
            if code is not None:
                referrals.register_referral(signup, code)
        return signup

    def clean_phone_number(self):
        phone = normalize_phone_number(self.cleaned_data['phone_number'])
        if User.objects.filter(phone_number=phone, is_active=True).exists():
            raise forms.ValidationError('This phone number is already registered. Please use the existing account or another number.')
        if SignupRequest.objects.filter(phone_number=phone).exclude(status=SignupRequest.Status.REJECTED).exists():
            raise forms.ValidationError('This phone number already has an active/pending registration. Please wait for that request to be processed.')
        return phone


class PaymentSettingsForm(forms.ModelForm):
    class Meta:
        model = PaymentSettings
        fields = [
            'enable_lipa_namba', 'lipa_namba', 'payee_name', 'support_phone', 'owner_notification_phone',
            'instructions_text',
            'starter_monthly_price', 'starter_yearly_price',
            'business_monthly_price', 'business_yearly_price',
            'professional_monthly_price', 'professional_yearly_price',
            'enterprise_monthly_price', 'enterprise_yearly_price',
            'branch_addon_price',
            'referral_enabled', 'referral_percent_per_invite', 'referral_max_percent',
        ]
        widgets = {
            'referral_enabled': forms.CheckboxInput(attrs={'class': 'h-4 w-4 rounded border-ink-300 text-brand-600 focus:ring-brand-500'}),
            'referral_percent_per_invite': forms.NumberInput(attrs={'class': 'form-input', 'min': '0', 'max': '100'}),
            'referral_max_percent': forms.NumberInput(attrs={'class': 'form-input', 'min': '0', 'max': '100'}),
            'enable_lipa_namba': forms.CheckboxInput(attrs={'class': 'h-4 w-4 rounded border-ink-300 text-brand-600 focus:ring-brand-500'}),
            'lipa_namba': forms.TextInput(attrs={'class': 'form-input'}),
            'payee_name': forms.TextInput(attrs={'class': 'form-input'}),
            'support_phone': forms.TextInput(attrs={'class': 'form-input'}),
            'owner_notification_phone': forms.TextInput(attrs={'class': 'form-input'}),
            'instructions_text': forms.Textarea(attrs={'class': 'form-input', 'rows': 4}),
            'starter_monthly_price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'starter_yearly_price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'business_monthly_price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'business_yearly_price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'professional_monthly_price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'professional_yearly_price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'enterprise_monthly_price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'enterprise_yearly_price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'branch_addon_price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
        }


class SMSDebtPackageForm(forms.ModelForm):
    class Meta:
        model = SMSDebtPackage
        fields = ['code', 'name', 'price', 'sms_credits', 'duration_days', 'is_active', 'sort_order']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-input'}),
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'sms_credits': forms.NumberInput(attrs={'class': 'form-input', 'min': '1'}),
            'duration_days': forms.NumberInput(attrs={'class': 'form-input', 'min': '1'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'h-4 w-4'}),
            'sort_order': forms.NumberInput(attrs={'class': 'form-input', 'min': '0'}),
        }

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()


class SMSReceiptPackageForm(forms.ModelForm):
    class Meta:
        model = SMSReceiptPackage
        fields = ['code', 'name', 'price', 'sms_credits', 'duration_days', 'is_active', 'sort_order']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-input'}),
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01'}),
            'sms_credits': forms.NumberInput(attrs={'class': 'form-input', 'min': '1'}),
            'duration_days': forms.NumberInput(attrs={'class': 'form-input', 'min': '1'}),
            'is_active': forms.CheckboxInput(),
            'sort_order': forms.NumberInput(attrs={'class': 'form-input', 'min': '0'}),
        }

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()


class SMSMarketingPackageForm(forms.ModelForm):
    class Meta:
        model = SMSMarketingPackage
        fields = ['code', 'name', 'price', 'sms_credits', 'duration_days', 'is_active', 'sort_order']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-input'}),
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'price': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0'}),
            'sms_credits': forms.NumberInput(attrs={'class': 'form-input', 'min': '1'}),
            'duration_days': forms.NumberInput(attrs={'class': 'form-input', 'min': '1'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'h-4 w-4'}),
            'sort_order': forms.NumberInput(attrs={'class': 'form-input', 'min': '0'}),
        }

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()
