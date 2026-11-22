from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm

from . import device_pin
from .models import User, MealAdjustment
from .phone_utils import normalize_phone_number


class LoginForm(forms.Form):
    username = forms.CharField(
        widget=forms.TextInput(attrs={
            'class': 'login-input',
            'placeholder': 'Username',
            'autofocus': True,
            'autocomplete': 'username',
            'autocapitalize': 'none',
            'spellcheck': 'false',
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'login-input',
            'placeholder': 'Password',
            'autocomplete': 'current-password',
        })
    )


def _pin_widget(placeholder=True):
    return forms.PasswordInput(attrs={
        'class': 'form-input tracking-[.5em] text-center', 'inputmode': 'numeric',
        'pattern': '[0-9]*', 'autocomplete': 'new-password', 'maxlength': device_pin.PIN_LENGTH,
        'placeholder': '\u2022' * device_pin.PIN_LENGTH if placeholder else '',
    })


class PinSetupForm(forms.Form):
    """Create a PIN: enter it twice. Used by the mandatory "Create your PIN" page."""
    pin = forms.CharField(label='PIN', min_length=device_pin.PIN_LENGTH, max_length=device_pin.PIN_LENGTH,
                          widget=_pin_widget())
    pin_confirm = forms.CharField(label='Confirm PIN', min_length=device_pin.PIN_LENGTH,
                                  max_length=device_pin.PIN_LENGTH, widget=_pin_widget())
    trust_device = forms.BooleanField(required=False, initial=False, label='Trust this device')

    def clean_pin(self):
        pin = self.cleaned_data['pin']
        error = device_pin.validate_pin(pin)
        if error:
            raise ValidationError(error)
        return pin

    def clean(self):
        cleaned = super().clean()
        pin, confirm = cleaned.get('pin'), cleaned.get('pin_confirm')
        if pin and confirm and pin != confirm:
            self.add_error('pin_confirm', 'The two PINs do not match.')
        return cleaned


class ChangePinForm(PinSetupForm):
    """Change the PIN from the Device PIN page; the account password is required."""
    current_password = forms.CharField(
        label='Your password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-input', 'autocomplete': 'current-password',
            'placeholder': 'Confirm with your account password',
        }),
    )
    field_order = ['current_password', 'pin', 'pin_confirm']

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields['pin'].label = 'New PIN'
        self.fields['pin_confirm'].label = 'Confirm new PIN'

    def clean_current_password(self):
        password = self.cleaned_data['current_password']
        if self.user is None or not self.user.check_password(password):
            raise ValidationError('Incorrect password.')
        return password


class AttendanceDisplayAccountForm(forms.Form):
    """Manager-facing credentials form for a branch attendance display account."""
    username = forms.CharField(
        min_length=4, max_length=150,
        widget=forms.TextInput(attrs={
            'class': 'form-input font-mono',
            'autocomplete': 'off',
            'placeholder': 'e.g. display_pharmacy_12',
            'spellcheck': 'false',
        }),
    )
    password = forms.CharField(
        required=False, min_length=8, max_length=128,
        widget=forms.PasswordInput(attrs={
            'class': 'form-input font-mono pr-11',
            'autocomplete': 'new-password',
            'placeholder': 'Leave blank to keep the current password',
        }),
        help_text='Minimum 8 characters. Leave blank to keep the existing password.',
    )

    def __init__(self, *args, account=None, **kwargs):
        self.account = account
        super().__init__(*args, **kwargs)
        if account is not None and not self.is_bound:
            self.fields['username'].initial = account.username

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        qs = User.objects.filter(username__iexact=username)
        if self.account is not None:
            qs = qs.exclude(pk=self.account.pk)
        if qs.exists():
            raise forms.ValidationError('That username is already in use. Choose another one.')
        return username


class EmployeeCreateForm(forms.ModelForm):
    phone_number = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '0712345678'}),
        help_text="Local (0712345678) or international (+255712345678) format is fine.",
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'username', 'phone_number']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-input'}),
            'last_name': forms.TextInput(attrs={'class': 'form-input'}),
            'username': forms.TextInput(attrs={'class': 'form-input'}),
        }

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("This username is already taken.")
        return username

    def clean_phone_number(self):
        normalized = normalize_phone_number(self.cleaned_data['phone_number'])
        if User.objects.filter(phone_number=normalized).exists():
            raise forms.ValidationError("This phone number is already registered to another account.")
        return normalized


class EmployeeEditForm(forms.ModelForm):
    """Edit an employee's personal details. The username is the login ID and is
    shown read-only; salary/meal settings stay in Salary & Meals."""
    phone_number = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '0712345678'}),
        help_text="Local (0712345678) or international (+255712345678) format is fine.",
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'phone_number']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-input'}),
            'last_name': forms.TextInput(attrs={'class': 'form-input'}),
        }

    def clean_phone_number(self):
        normalized = normalize_phone_number(self.cleaned_data['phone_number'])
        clash = User.objects.filter(phone_number=normalized).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("This phone number is already registered to another account.")
        return normalized


class EmployeeCompensationForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            'monthly_salary',
            'morning_meal_allowance', 'afternoon_meal_allowance', 'evening_meal_allowance',
        ]
        widgets = {
            'monthly_salary': forms.NumberInput(attrs={'class': 'form-input currency-field', 'step': '0.01', 'min': '0', 'placeholder': 'e.g. 350,000'}),
            'morning_meal_allowance': forms.NumberInput(attrs={'class': 'form-input currency-field', 'step': '0.01', 'min': '0', 'placeholder': 'e.g. 2,000'}),
            'afternoon_meal_allowance': forms.NumberInput(attrs={'class': 'form-input currency-field', 'step': '0.01', 'min': '0', 'placeholder': 'e.g. 2,000'}),
            'evening_meal_allowance': forms.NumberInput(attrs={'class': 'form-input currency-field', 'step': '0.01', 'min': '0', 'placeholder': 'e.g. 2,000'}),
        }
        labels = {
            'monthly_salary': 'Monthly Salary (TZS)',
            'morning_meal_allowance': 'Morning Tea Allowance (TZS/day)',
            'afternoon_meal_allowance': 'Lunch Allowance (TZS/day)',
            'evening_meal_allowance': 'Dinner Allowance (TZS/day)',
        }
        help_texts = {
            'monthly_salary': 'Fixed monthly salary. It is never reduced by meal records.',
            'morning_meal_allowance': 'Tracked as a meal operating cost when the weekly policy grants breakfast/tea.',
            'afternoon_meal_allowance': 'Tracked as a meal operating cost when the weekly policy grants lunch.',
            'evening_meal_allowance': 'Tracked as a meal operating cost when the weekly policy grants dinner.',
        }


class ProfileForm(forms.ModelForm):
    phone_number = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '0712345678'}),
        help_text="Local (0712345678) or international (+255712345678) format is fine.",
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'phone_number', 'profile_photo']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-input'}),
            'last_name': forms.TextInput(attrs={'class': 'form-input'}),
            'profile_photo': forms.ClearableFileInput(attrs={'class': 'form-input', 'accept': 'image/*'}),
        }
        help_texts = {
            'profile_photo': 'Any photo works (JPG, PNG or WebP, up to 10 MB). Your whole photo is kept, nothing is cropped, and it is compressed automatically so it loads fast on slow connections.',
        }

    def clean_profile_photo(self):
        photo = self.cleaned_data.get('profile_photo')
        size = getattr(photo, 'size', 0) if photo else 0
        if photo and hasattr(photo, 'content_type') and size > 10 * 1024 * 1024:
            raise forms.ValidationError('This photo is larger than 10 MB. Please choose a smaller one.')
        return photo

    def clean_phone_number(self):
        normalized = normalize_phone_number(self.cleaned_data['phone_number'])
        if User.objects.filter(phone_number=normalized).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This phone number is already in use by another account.")
        return normalized


class ForgotPasswordForm(forms.Form):
    phone_number = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '0712345678 or +255712345678', 'inputmode': 'tel', 'autocomplete': 'tel'})
    )

    def clean_phone_number(self):
        return normalize_phone_number(self.cleaned_data['phone_number'])


class OTPVerifyForm(forms.Form):
    code = forms.CharField(
        max_length=6, min_length=6,
        widget=forms.TextInput(attrs={
            'class': 'auth-field otp-input',
            'placeholder': '------',
            'autofocus': True,
            'inputmode': 'numeric',
            'autocomplete': 'one-time-code',
            'pattern': '[0-9]*',
            'maxlength': '6',
        })
    )


class StyledSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-input'})


class StyledPasswordChangeForm(PasswordChangeForm):
    """Change-password form: consistent styling, browser password-manager hints, and no re-use of the old password."""
    _AUTOCOMPLETE = {'old_password': 'current-password', 'new_password1': 'new-password', 'new_password2': 'new-password'}
    _PLACEHOLDER = {'old_password': 'Enter your current password', 'new_password1': 'Create a new password',
                    'new_password2': 'Repeat the new password'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs.update({
                'class': 'form-input',
                'autocomplete': self._AUTOCOMPLETE.get(name, 'off'),
                'placeholder': self._PLACEHOLDER.get(name, ''),
            })

    def clean_new_password1(self):
        new = self.cleaned_data.get('new_password1')
        old = self.data.get('old_password')
        if new and old and new == old:
            raise forms.ValidationError('Your new password must be different from your current password.')
        return new


class WeeklyMealScheduleForm(forms.Form):
    """Edit the seven default weekday meal entitlements in one screen."""
    DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

    def __init__(self, *args, employee=None, **kwargs):
        self.employee = employee
        super().__init__(*args, **kwargs)
        existing = {row.weekday: row for row in employee.meal_schedules.all()} if employee else {}
        for day, name in enumerate(self.DAY_NAMES):
            row = existing.get(day)
            self.fields[f'day_{day}_breakfast'] = forms.BooleanField(required=False, initial=row.breakfast if row else day < 5)
            self.fields[f'day_{day}_lunch'] = forms.BooleanField(required=False, initial=row.lunch if row else True)
            self.fields[f'day_{day}_dinner'] = forms.BooleanField(required=False, initial=row.dinner if row else day < 5)
            self.fields[f'day_{day}_breakfast'].label = f'{name} — Breakfast / Tea'
            self.fields[f'day_{day}_lunch'].label = f'{name} — Lunch'
            self.fields[f'day_{day}_dinner'].label = f'{name} — Dinner'


class MealAdjustmentForm(forms.ModelForm):
    class Meta:
        model = MealAdjustment
        fields = ['date', 'breakfast', 'lunch', 'dinner', 'reason']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'breakfast': forms.CheckboxInput(attrs={'class': 'h-4 w-4'}),
            'lunch': forms.CheckboxInput(attrs={'class': 'h-4 w-4'}),
            'dinner': forms.CheckboxInput(attrs={'class': 'h-4 w-4'}),
            'reason': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Worked full Saturday shift'}),
        }
        labels = {
            'date': 'Date', 'breakfast': 'Breakfast / Tea', 'lunch': 'Lunch', 'dinner': 'Dinner', 'reason': 'Reason / Note',
        }

    def __init__(self, *args, employee=None, **kwargs):
        self.employee = employee
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields['date'].initial = timezone.localdate()

    def clean_date(self):
        value = self.cleaned_data['date']
        if self.employee and value < self.employee.created_at.date():
            raise ValidationError('The adjustment date cannot be before this employee account was created.')
        return value

class MealTimingSettingsForm(forms.ModelForm):
    class Meta:
        from .models import MealTimingSettings
        model = MealTimingSettings
        fields = ['breakfast_start', 'breakfast_cutoff', 'lunch_start', 'lunch_cutoff', 'dinner_start', 'dinner_cutoff', 'reminder_minutes_before', 'late_requests_per_week']
        widgets = {
            'breakfast_start': forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}),
            'breakfast_cutoff': forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}),
            'lunch_start': forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}),
            'lunch_cutoff': forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}),
            'dinner_start': forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}),
            'dinner_cutoff': forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}),
            'reminder_minutes_before': forms.NumberInput(attrs={'class': 'form-input', 'min': 0, 'max': 60}),
            'late_requests_per_week': forms.NumberInput(attrs={'class': 'form-input', 'min': 0, 'max': 14}),
        }

    def clean(self):
        cleaned = super().clean()
        for start, end, label in [('breakfast_start','breakfast_cutoff','Breakfast'), ('lunch_start','lunch_cutoff','Lunch'), ('dinner_start','dinner_cutoff','Dinner')]:
            if cleaned.get(start) and cleaned.get(end) and cleaned[start] >= cleaned[end]:
                self.add_error(end, f'{label} end time must be after its start time.')
        if (cleaned.get('late_requests_per_week') or 0) > 14:
            self.add_error('late_requests_per_week', 'Use 14 or fewer late requests per week.')
        return cleaned

class ManagerAssistantCreateForm(forms.ModelForm):
    """Create an assistant account without exposing privileged User fields."""
    phone_number = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '0712345678'}),
        help_text='Local or international Tanzanian format is accepted.',
    )
    permissions = forms.MultipleChoiceField(
        choices=(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'username', 'phone_number', 'email']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-input'}),
            'last_name': forms.TextInput(attrs={'class': 'form-input'}),
            'username': forms.TextInput(attrs={'class': 'form-input'}),
            'email': forms.EmailInput(attrs={'class': 'form-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import ManagerAssistant
        self.fields['permissions'].choices = [
            (ManagerAssistant.FULL_ACCESS_PERMISSION, 'Full operational access'),
        ] + list(ManagerAssistant.PERMISSION_CHOICES)
        # New Manager Assistants start with full operational access.
        # Ownership, billing, subscription, Assistant administration and
        # platform-superadmin controls remain server-protected.
        self.fields['permissions'].initial = ['__ALL__']

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('This username is already taken.')
        return username

    def clean_phone_number(self):
        normalized = normalize_phone_number(self.cleaned_data['phone_number'])
        if User.objects.filter(phone_number=normalized).exists():
            raise forms.ValidationError('This phone number is already registered to another account.')
        return normalized


class AttendanceMethodsForm(forms.Form):
    CHOICES = [
        ('GPS', 'GPS location check'),
        ('QR', 'Branch QR code (scan the code shown at the branch)'),
        ('MANUAL', 'Request manager approval'),
    ]
    methods = forms.MultipleChoiceField(
        choices=CHOICES, widget=forms.CheckboxSelectMultiple, label='Methods this employee can see and use',
        error_messages={'required': 'Select at least one attendance method.'})

    def __init__(self, *args, employee=None, **kwargs):
        super().__init__(*args, **kwargs)
        if employee is not None and not self.is_bound:
            self.initial['methods'] = sorted(employee.allowed_attendance_methods)
