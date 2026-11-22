from django import forms
from django.utils import timezone

from .models import SystemNotice

_DT_FORMAT = '%Y-%m-%dT%H:%M'
_DT_INPUTS = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S']


class SystemNoticeForm(forms.ModelForm):
    starts_at = forms.DateTimeField(
        required=False, label='Show from',
        help_text='Leave empty to show it right now.',
        input_formats=_DT_INPUTS,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-input'}, format=_DT_FORMAT),
    )
    ends_at = forms.DateTimeField(
        required=False, label='Hide after',
        help_text='Optional. The banner disappears by itself at this time.',
        input_formats=_DT_INPUTS,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-input'}, format=_DT_FORMAT),
    )

    class Meta:
        model = SystemNotice
        # No "is_active" here: a notice only goes live when the owner publishes it from the review page.
        fields = ['kind', 'title', 'message', 'audience', 'link_url', 'link_label',
                  'show_on_login', 'dismissible', 'notify_in_app', 'starts_at', 'ends_at']
        widgets = {
            'kind': forms.Select(attrs={'class': 'form-input', 'id': 'id_kind'}),
            'title': forms.TextInput(attrs={'class': 'form-input', 'maxlength': 100, 'id': 'id_title',
                                            'placeholder': 'e.g. Tunashughulikia changamoto ya muda'}),
            'message': forms.Textarea(attrs={'class': 'form-input', 'rows': 4, 'maxlength': 500, 'id': 'id_message',
                                             'placeholder': 'What happened, what to expect, and what you are doing about it.'}),
            'audience': forms.Select(attrs={'class': 'form-input'}),
            'link_url': forms.TextInput(attrs={'class': 'form-input', 'id': 'id_link_url', 'placeholder': 'https://... (optional)'}),
            'link_label': forms.TextInput(attrs={'class': 'form-input', 'id': 'id_link_label', 'maxlength': 40, 'placeholder': 'Soma zaidi'}),
        }

    def clean_title(self):
        return self.cleaned_data['title'].strip()

    def clean_message(self):
        text = self.cleaned_data['message'].strip()
        if not text:
            raise forms.ValidationError('Write the message customers will read.')
        return text

    def clean_link_url(self):
        url = (self.cleaned_data.get('link_url') or '').strip()
        if url and not (url.startswith('https://') or url.startswith('http://') or (url.startswith('/') and not url.startswith('//'))):
            raise forms.ValidationError('Use a full link starting with https:// or a page path starting with /.')
        return url

    def clean(self):
        data = super().clean()
        starts = data.get('starts_at')
        ends = data.get('ends_at')
        if not starts:
            # Keep the original start when editing; otherwise start now.
            starts = (self.instance.starts_at if self.instance and self.instance.pk else None) or timezone.now()
            data['starts_at'] = starts
        if ends and ends <= starts:
            self.add_error('ends_at', 'The end time must be after the start time.')
        return data
