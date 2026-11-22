from django import forms

from accounts.models import User
from sms_integration.announcements import (
    MAX_TEMPLATE_CHARS, customers_queryset, find_unsupported_placeholders,
)
from sms_integration.models import SMSAnnouncement


class SMSAnnouncementForm(forms.Form):
    title = forms.CharField(
        max_length=120, label='Announcement title',
        help_text='Required. This title is shown at the top of every customer SMS and in-app notification.',
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. October system update'}),
    )
    message_type = forms.ChoiceField(
        choices=SMSAnnouncement.MessageType.choices, label='Message type',
        widget=forms.Select(attrs={'class': 'form-input'}),
    )
    audience = forms.ChoiceField(
        choices=[c for c in SMSAnnouncement.Audience.choices if c[0] != SMSAnnouncement.Audience.PROSPECTS],
        label='Send to',
        widget=forms.RadioSelect,
    )
    customer_ids = forms.ModelMultipleChoiceField(
        queryset=customers_queryset(), required=False,
        # Rendered by hand in the template (searchable checkbox list).
    )
    message = forms.CharField(
        label='SMS message', max_length=MAX_TEMPLATE_CHARS,
        widget=forms.Textarea(attrs={
            'class': 'form-input', 'rows': 6, 'maxlength': MAX_TEMPLATE_CHARS, 'id': 'id_message',
            'placeholder': 'Habari {name}, ...',
        }),
    )
    link_url = forms.URLField(
        required=False, label='Link destination (hiari)',
        help_text='Weka wapi {link} kwenye ujumbe unapaswa kupeleka mteja — mfano ukurasa wa malipo. '
                   'Link hii itatumwa kwa wateja kama ulivyoiandika.',
        widget=forms.URLInput(attrs={'class': 'form-input', 'placeholder': 'https://...'}),
    )
    include_assistants = forms.BooleanField(
        required=False, initial=True,
        label="Also send to each customer's assistant managers (SMS to their own phone numbers)",
    )
    also_in_app = forms.BooleanField(
        required=False, initial=True, label='Also post an in-app notification (bell) for each customer',
    )

    def clean_title(self):
        return self.cleaned_data['title'].strip()

    def clean_message(self):
        text = self.cleaned_data['message'].strip()
        if not text:
            raise forms.ValidationError('Write the SMS message.')
        bad = find_unsupported_placeholders(text)
        if bad:
            raise forms.ValidationError(
                'Unsupported placeholder: ' + ', '.join('{%s}' % b for b in bad) + '. Only {name} and {link} are available.')
        return text

    def clean(self):
        data = super().clean()
        if data.get('audience') == SMSAnnouncement.Audience.SELECTED and not data.get('customer_ids'):
            self.add_error('customer_ids', 'Select at least one customer.')
        message = data.get('message') or ''
        if '{link}' in message and not data.get('link_url'):
            self.add_error('link_url', 'Umetumia {link} kwenye ujumbe — weka URL ambayo link hiyo itafungua.')
        return data
