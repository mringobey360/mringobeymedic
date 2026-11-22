from django import forms
from django.utils import timezone

from . import perf_core as pc
from .models import DashboardPreference

_LIMITS = {
    pc.MODE_LAST_DAYS: (1, pc.MAX_RANGE_DAYS, 'days'),
    pc.MODE_LAST_WEEKS: (1, pc.MAX_RANGE_DAYS // 7, 'weeks'),
    pc.MODE_LAST_MONTHS: (1, 24, 'months'),
}


class PerformancePreferenceForm(forms.ModelForm):
    class Meta:
        model = DashboardPreference
        fields = ['mode', 'last_n', 'custom_start', 'custom_end', 'interval']
        widgets = {
            'mode': forms.Select(attrs={'class': 'form-input'}),
            'last_n': forms.NumberInput(attrs={'class': 'form-input', 'min': 1, 'inputmode': 'numeric'}),
            'custom_start': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-input'}),
            'custom_end': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-input'}),
            'interval': forms.Select(attrs={'class': 'form-input'}),
        }
        labels = {
            'mode': 'Period',
            'last_n': 'How many',
            'custom_start': 'From',
            'custom_end': 'To',
            'interval': 'Break down by',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['last_n'].required = False
        self.fields['custom_start'].required = False
        self.fields['custom_end'].required = False

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get('mode')
        today = timezone.localdate()
        if mode in _LIMITS:
            low, high, unit = _LIMITS[mode]
            n = cleaned.get('last_n')
            if n is None or not (low <= n <= high):
                self.add_error('last_n', f'Enter a number of {unit} between {low} and {high}.')
            cleaned['custom_start'] = cleaned['custom_end'] = None
        elif mode == pc.MODE_CUSTOM:
            start, end = cleaned.get('custom_start'), cleaned.get('custom_end')
            if not start:
                self.add_error('custom_start', 'Choose the first day.')
            if not end:
                self.add_error('custom_end', 'Choose the last day.')
            if start and end:
                if start > end:
                    self.add_error('custom_end', 'The last day must be on or after the first day.')
                elif end > today:
                    self.add_error('custom_end', 'The last day cannot be in the future.')
                elif (end - start).days + 1 > pc.MAX_RANGE_DAYS:
                    self.add_error('custom_end', f'Choose at most {pc.MAX_RANGE_DAYS} days (about 2 years).')
        else:  # default monthly view
            cleaned['custom_start'] = cleaned['custom_end'] = None
        if cleaned.get('last_n') is None:
            cleaned['last_n'] = self.instance.last_n or 14
        return cleaned
