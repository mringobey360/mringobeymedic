from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('sms_integration', '0002_premium_receipt_sms')]
    operations = [
        migrations.AddField(
            model_name='smssettings', name='send_payment_receipts',
            field=models.BooleanField(default=True, help_text='Send registration/payment confirmation SMS with a secure subscription receipt link.'),
        ),
        migrations.AddField(
            model_name='smslog', name='character_count',
            field=models.PositiveIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name='smslog', name='sms_segments',
            field=models.PositiveIntegerField(default=1, editable=False),
        ),
        migrations.AlterField(
            model_name='smslog', name='category',
            field=models.CharField(choices=[
                ('CREDENTIALS', 'Employee Credentials'), ('OTP', 'Password Reset OTP'),
                ('DAILY_SUMMARY', 'Daily Summary'), ('LOW_STOCK', 'Low Stock Alert'),
                ('EXPIRY', 'Expiry Alert'), ('DEBT', 'Debt Reminder'),
                ('RECEIPT', 'Receipt Download Link'), ('PAYMENT_RECEIPT', 'Subscription Payment Receipt'),
                ('OTHER', 'Other')], default='OTHER', max_length=20),
        ),
    ]
