from django import forms
from .models import CustomerSMSCredentials

class CustomerSMSCredentialsForm(forms.ModelForm):
    class Meta:
        model = CustomerSMSCredentials
        fields = ['at_username', 'sender_id', 'at_api_key']
        widgets = {
            'at_username': forms.TextInput(attrs={'class':'form-input','autocomplete':'off'}),
            'sender_id': forms.TextInput(attrs={'class':'form-input','placeholder':'Approved Sender ID'}),
            'at_api_key': forms.PasswordInput(render_value=True, attrs={'class':'form-input','autocomplete':'new-password','placeholder':'Enter API key'}),
        }
