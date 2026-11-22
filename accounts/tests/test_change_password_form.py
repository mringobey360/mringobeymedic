from django.test import SimpleTestCase

from accounts.forms import StyledPasswordChangeForm


class _User:
    def check_password(self, raw):
        return raw == 'OldPass123'


class StyledPasswordChangeFormTests(SimpleTestCase):
    def test_widgets_have_password_manager_hints(self):
        f = StyledPasswordChangeForm(user=_User())
        self.assertEqual(f.fields['old_password'].widget.attrs['autocomplete'], 'current-password')
        self.assertEqual(f.fields['new_password1'].widget.attrs['autocomplete'], 'new-password')
        self.assertIn('form-input', f.fields['new_password2'].widget.attrs['class'])
