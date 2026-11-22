import csv
import io
from django import forms
from accounts.phone_utils import normalize_phone_number
from .models import CustomerContact

class CustomerContactForm(forms.ModelForm):
    class Meta:
        model = CustomerContact
        fields = ['name','phone','notes','is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class':'form-input','placeholder':'Customer full name','autocomplete':'name'}),
            'phone': forms.TextInput(attrs={'class':'form-input','placeholder':'0712 345 678 or +255712345678','inputmode':'tel','autocomplete':'tel'}),
            'notes': forms.TextInput(attrs={'class':'form-input','placeholder':'Optional note'}),
            'is_active': forms.CheckboxInput(attrs={'class':'mt-1 accent-teal-700'}),
        }
    def clean_phone(self):
        try:
            return normalize_phone_number(self.cleaned_data['phone'])
        except Exception as exc:
            raise forms.ValidationError(str(exc))

class CustomerBulkUploadForm(forms.Form):
    file = forms.FileField(label='Customer file', help_text='Use the official template or a CSV with Customer Name, Phone Number and optional Notes.', widget=forms.ClearableFileInput(attrs={'class':'form-input','accept':'.xlsx,.csv'}))
    update_existing = forms.BooleanField(required=False, initial=True, label='Update existing customers when the phone number already exists',
                                         widget=forms.CheckboxInput(attrs={'class':'mt-1 accent-teal-700'}))

    def clean_file(self):
        f = self.cleaned_data['file']
        name = f.name.lower()
        if not (name.endswith('.xlsx') or name.endswith('.csv')):
            raise forms.ValidationError('Only .xlsx and .csv files are supported.')
        if f.size > 5 * 1024 * 1024:
            raise forms.ValidationError('Maximum file size is 5 MB.')
        return f

def parse_customer_file(upload):
    rows=[]
    if upload.name.lower().endswith('.csv'):
        raw=upload.read().decode('utf-8-sig')
        reader=csv.DictReader(io.StringIO(raw))
        for r in reader:
            rows.append({str(k).strip().lower(): (v or '').strip() for k,v in r.items() if k})
    else:
        from openpyxl import load_workbook
        wb=load_workbook(upload, read_only=True, data_only=True)
        ws=wb.active
        data=list(ws.iter_rows(values_only=True))
        if not data: return []
        headers=[str(x or '').strip().lower() for x in data[0]]
        for values in data[1:]:
            rows.append({headers[i]: str(values[i] or '').strip() for i in range(min(len(headers),len(values))) if headers[i]})
        wb.close()
    return rows

def row_value(row, *keys):
    for key in keys:
        if key in row and row[key]:
            return row[key]
    return ''
