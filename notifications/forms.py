from django import forms
from accounts.models import User
from .links import clean_action_url, LABEL_DEFAULT, MAX_LABEL


class AnnouncementForm(forms.Form):
    SCOPE_CHOICES = [
        ('THIS_BRANCH', 'This branch only'),
        ('ALL_BRANCHES', 'All my branches'),
        ('CUSTOMER_ALL', 'All active customers'),
        ('CUSTOMER_SELECTED', 'Selected customers'),
    ]

    title = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Saa za Kufungua Kesho'}),
        label='Announcement title',
        help_text='Required. Every announcement must have a clear title.',
    )
    message = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 4, 'placeholder': 'Write your announcement here...'}),
        label='Message',
    )
    action_url = forms.CharField(
        required=False, max_length=500,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'https://example.com/page', 'inputmode': 'url', 'autocomplete': 'off'}),
        label='Attach a link (optional)',
        help_text='Customers will see an "Open link" button. Use https:// links, or an in-app path such as /sales/history/.',
    )
    action_label = forms.CharField(
        required=False, max_length=MAX_LABEL,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Soma maelekezo'}),
        label='Button text (optional)',
    )
    scope = forms.ChoiceField(choices=SCOPE_CHOICES, widget=forms.RadioSelect, initial='THIS_BRANCH', label='Recipients')
    customer_ids = forms.MultipleChoiceField(required=False, widget=forms.SelectMultiple(attrs={'class':'form-input','size':6}), label='Select customers')

    def __init__(self, *args, **kwargs):
        is_platform_owner = kwargs.pop('is_platform_owner', False)
        super().__init__(*args, **kwargs)
        if not is_platform_owner:
            self.fields['scope'].choices = self.SCOPE_CHOICES[:2]
        else:
            customers = User.objects.filter(role=User.Role.ADMIN, is_active=True, is_superuser=False).order_by('first_name','last_name','username')
            self.fields['customer_ids'].choices = [(u.pk, f"{u.get_full_name() or u.username} · {u.phone_number}") for u in customers]
            self.fields['customer_ids'].widget.attrs['class'] = 'form-input'


    def clean_action_url(self):
        raw = (self.cleaned_data.get('action_url') or '').strip()
        if not raw:
            return ''
        if raw.lower().startswith('www.'):
            raw = 'https://' + raw
        safe = clean_action_url(raw)
        if not safe:
            raise forms.ValidationError('Enter a valid link starting with https:// (or an in-app path starting with /).')
        return safe

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('action_label') and not cleaned.get('action_url'):
            self.add_error('action_url', 'Add the link this button should open, or clear the button text.')
        if cleaned.get('action_url') and not (cleaned.get('action_label') or '').strip():
            cleaned['action_label'] = LABEL_DEFAULT
        if cleaned.get('scope') == 'CUSTOMER_SELECTED' and not cleaned.get('customer_ids'):
            self.add_error('customer_ids', 'Select at least one customer.')
        return cleaned
