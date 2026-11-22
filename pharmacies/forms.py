from django import forms

from accounts.phone_utils import normalize_phone_number
from core.receipt_pdf import contrast_ratio, contrast_with_white, normalize_hex, HEX_RE
from .models import Pharmacy, ReceiptSettings
from hq.models import SignupRequest, Subscription, PaymentSettings


class PharmacyForm(forms.ModelForm):
    package_tier = forms.ChoiceField(
        required=False,
        label='Branch package',
        choices=SignupRequest.Tier.choices,
        widget=forms.RadioSelect(attrs={'class': 'branch-package-radio'}),
    )
    billing_plan = forms.ChoiceField(
        required=False,
        label='Billing period',
        choices=Subscription.Plan.choices,
        widget=forms.RadioSelect(attrs={'class': 'branch-billing-radio'}),
    )

    phone_number = forms.CharField(
        required=False,
        label='Office / Customer Service Phone',
        help_text='Used automatically in customer Marketing SMS and Receipt SMS. Use 0712345678 or +255712345678.',
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '+255 712 345 678'}),
    )

    class Meta:
        model = Pharmacy
        fields = ['name', 'location', 'phone_number', 'email', 'latitude', 'longitude', 'geofence_radius_m', 'presence_check_interval_minutes', 'attendance_lunch_rescan_required', 'attendance_lunch_rescan_start', 'attendance_lunch_rescan_end', 'attendance_outside_grace_minutes', 'registration_number', 'logo', 'receipt_header', 'receipt_footer']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Kariakoo Branch'}),
            'location': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Kariakoo, Dar es Salaam'}),
            'email': forms.EmailInput(attrs={'class': 'form-input', 'placeholder': 'pharmacy@example.com'}),
            'latitude': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.0000001', 'placeholder': '-6.8...'}),
            'longitude': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.0000001', 'placeholder': '39.2...'}),
            'geofence_radius_m': forms.NumberInput(attrs={'class': 'form-input', 'min': '30', 'max': '2000'}),
            'presence_check_interval_minutes': forms.NumberInput(attrs={'class': 'form-input', 'min': '5', 'max': '60'}),
            'attendance_lunch_rescan_required': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'attendance_lunch_rescan_start': forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}),
            'attendance_lunch_rescan_end': forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}),
            'attendance_outside_grace_minutes': forms.NumberInput(attrs={'class': 'form-input', 'min': '0', 'max': '60'}),
            'registration_number': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Optional registration / license number'}),
            'logo': forms.ClearableFileInput(attrs={'class': 'form-input', 'accept': 'image/png,image/jpeg,image/webp'}),
            'receipt_header': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Quality medicines and trusted care'}),
            'receipt_footer': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Thank you for choosing us'}),
        }

    # Attendance / geofence settings are not part of the "Add branch" screen — they are
    # configured later from the branch's own settings. They are REQUIRED on the model (each has
    # a default), so leaving them out of the form made every submit silently invalid. Making
    # them optional here means an omitted value simply keeps the model default.
    OPTIONAL_ATTENDANCE_FIELDS = (
        'geofence_radius_m', 'presence_check_interval_minutes', 'attendance_lunch_rescan_required',
        'attendance_lunch_rescan_start', 'attendance_lunch_rescan_end', 'attendance_outside_grace_minutes',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['package_tier'].initial = SignupRequest.Tier.BUSINESS
        self.fields['billing_plan'].initial = Subscription.Plan.MONTHLY
        for name in self.OPTIONAL_ATTENDANCE_FIELDS:
            if name in self.fields:
                self.fields[name].required = False

    def _fallback_value(self, name):
        """The branch's current value (or the model default for a new branch), as a clean Python value."""
        value = getattr(self.instance, name, None)
        if isinstance(value, str) and name.startswith('attendance_lunch_rescan_') and name != 'attendance_lunch_rescan_required':
            from datetime import datetime
            try:
                return datetime.strptime(value[:5], '%H:%M').time()
            except ValueError:
                return None
        return value

    def clean(self):
        cleaned = super().clean()
        # A blank/omitted attendance setting never nulls a required column: keep the current value.
        for name in self.OPTIONAL_ATTENDANCE_FIELDS:
            if name in self.fields and name != 'attendance_lunch_rescan_required' and cleaned.get(name) in (None, ''):
                cleaned[name] = self._fallback_value(name)
        lat = cleaned.get('latitude')
        lon = cleaned.get('longitude')
        if (lat is None) != (lon is None):
            raise forms.ValidationError('Branch latitude and longitude must be provided together.')
        if lat is not None and not (-90 <= float(lat) <= 90):
            self.add_error('latitude', 'Latitude must be between -90 and 90.')
        if lon is not None and not (-180 <= float(lon) <= 180):
            self.add_error('longitude', 'Longitude must be between -180 and 180.')
        radius = cleaned.get('geofence_radius_m')
        if radius is not None and not (30 <= int(radius) <= 2000):
            self.add_error('geofence_radius_m', 'Attendance radius must be between 30m and 2000m.')
        interval = cleaned.get('presence_check_interval_minutes')
        if interval is not None and not (5 <= int(interval) <= 60):
            self.add_error('presence_check_interval_minutes', 'Presence check interval must be between 5 and 60 minutes.')
        grace = cleaned.get('attendance_outside_grace_minutes')
        if grace is not None and not (0 <= int(grace) <= 60):
            self.add_error('attendance_outside_grace_minutes', 'Outside grace period must be between 0 and 60 minutes.')
        lunch_start = cleaned.get('attendance_lunch_rescan_start')
        lunch_end = cleaned.get('attendance_lunch_rescan_end')
        if lunch_start and lunch_end and lunch_start >= lunch_end:
            self.add_error('attendance_lunch_rescan_end', 'Lunch verification end time must be after the start time.')
        return cleaned

    def clean_phone_number(self):
        value = self.cleaned_data.get('phone_number', '').strip()
        if not value:
            return ''
        return normalize_phone_number(value)

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        qs = Pharmacy.objects.filter(name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A branch with this name already exists.")
        return name


    def clean_logo(self):
        logo = self.cleaned_data.get('logo')
        if logo:
            if logo.size > 2 * 1024 * 1024:
                raise forms.ValidationError('Logo must be 2 MB or smaller.')
            content_type = getattr(logo, 'content_type', '')
            if content_type not in {'image/jpeg', 'image/png', 'image/webp'}:
                raise forms.ValidationError('Logo must be JPG, PNG, or WEBP.')
        return logo

class BranchSettingsForm(forms.ModelForm):
    """Safe branch-level settings. Billing, attendance policy and internal counters
    are intentionally excluded so normal managers cannot accidentally break them."""
    class Meta:
        model = Pharmacy
        fields = [
            'name', 'location', 'phone_number', 'email', 'logo',
            'receipt_header', 'receipt_footer',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'autocomplete': 'organization', 'placeholder': 'e.g. Amani Stores'}),
            'location': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Physical address or area'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-input', 'placeholder': '+255 712 345 678', 'inputmode': 'tel'}),
            'email': forms.EmailInput(attrs={'class': 'form-input', 'placeholder': 'branch@example.com', 'inputmode': 'email'}),
            'logo': forms.ClearableFileInput(attrs={'class': 'form-input', 'accept': 'image/png,image/jpeg,image/webp'}),
            'receipt_header': forms.TextInput(attrs={'class': 'form-input', 'maxlength': '180', 'placeholder': 'Optional line under the business name'}),
            'receipt_footer': forms.TextInput(attrs={'class': 'form-input', 'maxlength': '255', 'placeholder': 'e.g. Thank you for choosing us'}),
        }

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        qs = Pharmacy.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('A branch with this name already exists.')
        return name

    def clean_phone_number(self):
        value = self.cleaned_data.get('phone_number', '').strip()
        return normalize_phone_number(value) if value else ''

    def clean_logo(self):
        logo = self.cleaned_data.get('logo')
        if logo:
            if logo.size > 2 * 1024 * 1024:
                raise forms.ValidationError('Logo must be 2 MB or smaller.')
            content_type = getattr(logo, 'content_type', '')
            if content_type not in {'image/jpeg', 'image/png', 'image/webp'}:
                raise forms.ValidationError('Logo must be JPG, PNG, or WEBP.')
        return logo


class WorkplaceLocationForm(forms.ModelForm):
    """Operational workplace controls separated from ordinary branch branding.

    GPS coordinates may be captured from the manager's device; attendance
    policy remains in this dedicated flow so a simple branding edit can never
    accidentally change employee verification behaviour.
    """
    class Meta:
        model = Pharmacy
        fields = [
            'latitude', 'longitude', 'geofence_radius_m',
            'presence_check_interval_minutes',
            'attendance_qr_requires_location',
            'attendance_lunch_rescan_required',
            'attendance_lunch_rescan_start', 'attendance_lunch_rescan_end',
            'attendance_outside_grace_minutes',
            'attendance_sms_mode', 'attendance_sms_work_start', 'attendance_sms_work_end', 'attendance_sms_grace_minutes',
        ]
        widgets = {
            'latitude': forms.NumberInput(attrs={'class': 'form-input bg-ink-50', 'step': '0.0000001', 'inputmode': 'decimal', 'placeholder': '-6.8000000', 'readonly': 'readonly'}),
            'longitude': forms.NumberInput(attrs={'class': 'form-input bg-ink-50', 'step': '0.0000001', 'inputmode': 'decimal', 'placeholder': '39.2500000', 'readonly': 'readonly'}),
            'geofence_radius_m': forms.NumberInput(attrs={'class': 'form-input', 'min': '30', 'max': '2000', 'inputmode': 'numeric'}),
            'presence_check_interval_minutes': forms.NumberInput(attrs={'class': 'form-input', 'min': '5', 'max': '60', 'inputmode': 'numeric'}),
            'attendance_qr_requires_location': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'attendance_lunch_rescan_required': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'attendance_lunch_rescan_start': forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}),
            'attendance_lunch_rescan_end': forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}),
            'attendance_outside_grace_minutes': forms.NumberInput(attrs={'class': 'form-input', 'min': '0', 'max': '60', 'inputmode': 'numeric'}),
            'attendance_sms_mode': forms.Select(attrs={'class': 'form-input'}),
            'attendance_sms_work_start': forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}, format='%H:%M'),
            'attendance_sms_work_end': forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}, format='%H:%M'),
            'attendance_sms_grace_minutes': forms.NumberInput(attrs={'class': 'form-input', 'min': '0', 'max': '120', 'inputmode': 'numeric'}),
        }

    def clean(self):
        cleaned = super().clean()
        lat, lon = cleaned.get('latitude'), cleaned.get('longitude')
        if (lat is None) != (lon is None):
            raise forms.ValidationError('Branch latitude and longitude must be provided together.')
        if lat is not None and not (-90 <= float(lat) <= 90):
            self.add_error('latitude', 'Latitude must be between -90 and 90.')
        if lon is not None and not (-180 <= float(lon) <= 180):
            self.add_error('longitude', 'Longitude must be between -180 and 180.')
        radius = cleaned.get('geofence_radius_m')
        if radius is not None and not (30 <= int(radius) <= 2000):
            self.add_error('geofence_radius_m', 'Use a geofence between 30m and 2000m.')
        interval = cleaned.get('presence_check_interval_minutes')
        if interval is not None and not (5 <= int(interval) <= 60):
            self.add_error('presence_check_interval_minutes', 'Use an interval between 5 and 60 minutes.')
        grace = cleaned.get('attendance_outside_grace_minutes')
        if grace is not None and not (0 <= int(grace) <= 60):
            self.add_error('attendance_outside_grace_minutes', 'Use a grace period between 0 and 60 minutes.')
        start, end = cleaned.get('attendance_lunch_rescan_start'), cleaned.get('attendance_lunch_rescan_end')
        if cleaned.get('attendance_lunch_rescan_required') and (not start or not end):
            raise forms.ValidationError('Set both lunch rescan start and end times when lunch verification is enabled.')
        if start and end and start >= end:
            self.add_error('attendance_lunch_rescan_end', 'Lunch verification end time must be after the start time.')
        # Attendance SMS policy: the working-hours window must make sense, otherwise
        # every check-in would be reported as late (or every check-out as early).
        sms_mode = cleaned.get('attendance_sms_mode')
        work_start, work_end = cleaned.get('attendance_sms_work_start'), cleaned.get('attendance_sms_work_end')
        sms_grace = cleaned.get('attendance_sms_grace_minutes')
        if sms_mode and sms_mode != 'OFF':
            if not work_start or not work_end:
                raise forms.ValidationError('Set both the work start and closing time to use attendance SMS.')
            if work_start >= work_end:
                self.add_error('attendance_sms_work_end', 'Closing time must be after the start time (overnight shifts are not supported).')
        if sms_grace is not None and not (0 <= int(sms_grace) <= 120):
            self.add_error('attendance_sms_grace_minutes', 'Use a tolerance between 0 and 120 minutes.')
        return cleaned


class ReceiptDesignForm(forms.ModelForm):
    """Manager-facing receipt design: colour, logo, layout, texts and visible details.

    Besides the ReceiptSettings fields it edits three branch-level values that
    already live on Pharmacy (logo, header line, footer message) so everything a
    customer sees on the receipt is managed from one page.
    """
    primary_color = forms.CharField(
        max_length=7, label='Brand colour',
        widget=forms.TextInput(attrs={'class': 'form-input font-mono uppercase', 'maxlength': '7', 'placeholder': '#2F8F3B', 'autocomplete': 'off', 'spellcheck': 'false'}),
    )
    text_color = forms.CharField(
        max_length=7, label='Text colour',
        widget=forms.TextInput(attrs={'class': 'form-input font-mono uppercase', 'maxlength': '7', 'placeholder': '#172033', 'autocomplete': 'off', 'spellcheck': 'false'}),
    )
    background_color = forms.CharField(
        max_length=7, label='Paper colour',
        widget=forms.TextInput(attrs={'class': 'form-input font-mono uppercase', 'maxlength': '7', 'placeholder': '#FFFFFF', 'autocomplete': 'off', 'spellcheck': 'false'}),
    )
    receipt_header = forms.CharField(
        max_length=180, required=False, label='Line under the branch name',
        widget=forms.TextInput(attrs={'class': 'form-input', 'maxlength': '180', 'placeholder': 'e.g. Quality medicines and trusted care'}),
    )
    receipt_footer = forms.CharField(
        max_length=255, required=False, label='Thank-you message',
        widget=forms.TextInput(attrs={'class': 'form-input', 'maxlength': '255', 'placeholder': 'e.g. Thank you for choosing us'}),
    )
    logo = forms.ImageField(
        required=False, label='Receipt logo',
        widget=forms.FileInput(attrs={'class': 'form-input', 'accept': 'image/png,image/jpeg,image/webp'}),
    )
    remove_logo = forms.BooleanField(required=False, label='Remove my logo and use the SmartPOS logo')

    class Meta:
        model = ReceiptSettings
        fields = [
            'primary_color', 'text_color', 'background_color', 'panel_style', 'title_style', 'font_family', 'paper_width_mm', 'header_layout', 'logo_size', 'show_logo',
            'title_text', 'return_policy', 'watermark_text', 'show_address', 'show_contacts',
            'show_registration', 'show_customer', 'show_served_by', 'show_batch_details',
            'show_barcode', 'show_watermark',
        ]
        widgets = {
            'font_family': forms.RadioSelect(attrs={'class': 'peer sr-only'}),
            'panel_style': forms.RadioSelect(attrs={'class': 'peer sr-only'}),
            'title_style': forms.RadioSelect(attrs={'class': 'peer sr-only'}),
            'paper_width_mm': forms.RadioSelect(attrs={'class': 'peer sr-only'}),
            'header_layout': forms.RadioSelect(attrs={'class': 'peer sr-only'}),
            'logo_size': forms.RadioSelect(attrs={'class': 'peer sr-only'}),
            'title_text': forms.TextInput(attrs={'class': 'form-input', 'maxlength': '40', 'placeholder': 'OFFICIAL SALES RECEIPT'}),
            'return_policy': forms.TextInput(attrs={'class': 'form-input', 'maxlength': '255', 'placeholder': 'e.g. Goods once sold cannot be returned after 24 hours'}),
            'watermark_text': forms.TextInput(attrs={'class': 'form-input', 'maxlength': '30', 'placeholder': 'Leave blank to use the branch name'}),
        }
        labels = {
            'title_text': 'Receipt title',
            'return_policy': 'Terms / return policy',
            'watermark_text': 'Watermark text',
            'show_logo': 'Show logo',
            'show_address': 'Branch address',
            'show_contacts': 'Phone and email',
            'show_registration': 'FIN / licence and permit',
            'show_customer': 'Customer name and phone',
            'show_served_by': 'Served by (staff name)',
            'show_batch_details': 'Batch number and expiry date',
            'show_barcode': 'Receipt barcode',
            'show_watermark': 'Watermark',
        }

    def __init__(self, *args, pharmacy, **kwargs):
        self.pharmacy = pharmacy
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields['receipt_header'].initial = pharmacy.receipt_header
            self.fields['receipt_footer'].initial = pharmacy.receipt_footer

    def clean_primary_color(self):
        value = normalize_hex(self.cleaned_data.get('primary_color'), fallback='')
        if not value or not HEX_RE.match(value):
            raise forms.ValidationError('Enter a colour like #2F8F3B.')
        if contrast_with_white(value) < 3.0:
            raise forms.ValidationError('This colour is too light to read on a white receipt. Choose a darker shade.')
        return value

    def clean_text_color(self):
        value = normalize_hex(self.cleaned_data.get('text_color'), fallback='')
        if not value or not HEX_RE.match(value):
            raise forms.ValidationError('Enter a colour like #172033.')
        if contrast_with_white(value) < 4.5:
            raise forms.ValidationError('This text colour is too light to read clearly. Choose a darker shade.')
        return value

    def clean_background_color(self):
        value = normalize_hex(self.cleaned_data.get('background_color'), fallback='')
        if not value or not HEX_RE.match(value):
            raise forms.ValidationError('Enter a colour like #FFFFFF.')
        if contrast_with_white(value) > 1.3:
            raise forms.ValidationError('The paper colour must be a light shade so the receipt stays readable.')
        return value

    def clean(self):
        cleaned = super().clean()
        bg, text, brand = cleaned.get('background_color'), cleaned.get('text_color'), cleaned.get('primary_color')
        if bg and text and contrast_ratio(text, bg) < 4.5:
            self.add_error('text_color', 'This text colour does not stand out enough on the chosen paper colour.')
        if bg and brand and contrast_ratio(brand, bg) < 3.0:
            self.add_error('primary_color', 'This brand colour does not stand out enough on the chosen paper colour.')
        return cleaned

    def clean_title_text(self):
        return (self.cleaned_data.get('title_text') or '').strip()

    def clean_logo(self):
        logo = self.cleaned_data.get('logo')
        if logo:
            if logo.size > 2 * 1024 * 1024:
                raise forms.ValidationError('Logo must be 2 MB or smaller.')
            content_type = getattr(logo, 'content_type', '')
            if content_type not in {'image/jpeg', 'image/png', 'image/webp'}:
                raise forms.ValidationError('Logo must be JPG, PNG, or WEBP.')
        return logo

    def apply_branch_fields(self):
        """Write logo / header / footer onto the Pharmacy record (call after is_valid())."""
        data = self.cleaned_data
        pharmacy = self.pharmacy
        pharmacy.receipt_header = (data.get('receipt_header') or '').strip()
        pharmacy.receipt_footer = (data.get('receipt_footer') or '').strip()
        update = ['receipt_header', 'receipt_footer']
        if data.get('logo'):
            pharmacy.logo = data['logo']
            update.append('logo')
        elif data.get('remove_logo') and pharmacy.logo:
            pharmacy.logo = None
            update.append('logo')
        pharmacy.save(update_fields=update)
        return pharmacy
