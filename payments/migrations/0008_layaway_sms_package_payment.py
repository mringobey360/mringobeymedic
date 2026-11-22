from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0007_encrypt_sensitive_payment_fields")]

    operations = [
        migrations.AlterField(
            model_name="pesapaltransaction",
            name="purpose",
            field=models.CharField(max_length=30, choices=[
                ("SUBSCRIPTION", "New Subscription"),
                ("SUBSCRIPTION_RENEWAL", "Subscription Renewal"),
                ("BRANCH_ADDON", "Branch Add-on"),
                ("BRANCH_ADDON_RENEWAL", "Branch Add-on Renewal"),
                ("SMS_DEBT_PACKAGE", "Premium Debt SMS Package"),
                ("SMS_RECEIPT_PACKAGE", "Premium Receipt SMS Package"),
                ("SMS_MARKETING_PACKAGE", "Customer Marketing SMS Package"),
                ("DAILY_REPORT_PACKAGE", "Intelligent Report SMS Package"),
                ("LAYAWAY_SMS_PACKAGE", "Layaway SMS Package"),
            ]),
        ),
    ]
