from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sms_integration', '0012_prospect_english_labels'),
    ]

    operations = [
        migrations.AddField(
            model_name='smssettings',
            name='send_attendance_alerts',
            field=models.BooleanField(default=True, help_text='Allow attendance SMS (employee late / check-in / check-out) to branch owners. Each branch still chooses its own mode.'),
        ),
        migrations.AlterField(
            model_name='smslog',
            name='category',
            field=models.CharField(choices=[('CREDENTIALS', 'Employee Credentials'), ('OTP', 'Password Reset OTP'), ('DAILY_SUMMARY', 'Daily Summary'), ('LOW_STOCK', 'Low Stock Alert'), ('EXPIRY', 'Expiry Alert'), ('DEBT', 'Debt Reminder'), ('RECEIPT', 'Receipt Download Link'), ('PAYMENT_RECEIPT', 'Subscription Payment Receipt'), ('CUSTOMER_MARKETING', 'Customer Marketing Campaign'), ('ANNOUNCEMENT', 'Platform Announcement'), ('ATTENDANCE', 'Attendance Alert'), ('OTHER', 'Other')], default='OTHER', max_length=20),
        ),
    ]
