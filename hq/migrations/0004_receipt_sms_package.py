from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('hq', '0003_legacy_signup_fields'), ('sms_integration', '0002_premium_receipt_sms')]
    operations = [migrations.AddField(model_name='signuprequest', name='receipt_sms_package', field=models.ForeignKey(blank=True, help_text='Optional Premium receipt-link SMS package purchased together with the account.', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='signup_requests', to='sms_integration.smsreceiptpackage'))]
