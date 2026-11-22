from django import forms
from accounts.phone_utils import normalize_phone_number
from .models import DailyReportSettings

class DailyReportSettingsForm(forms.ModelForm):
    class Meta:
        model = DailyReportSettings
        fields = ['enabled', 'delivery_time', 'phone_number', 'include_pdf_link', 'include_salary_accrual', 'report_retention_days']
        widgets = {'delivery_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time'}), 'report_retention_days': forms.NumberInput(attrs={'min': 7, 'max': 90})}

    def clean_phone_number(self):
        value = self.cleaned_data.get('phone_number', '').strip()
        if not value:
            return ''
        return normalize_phone_number(value)
