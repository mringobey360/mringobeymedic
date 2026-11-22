from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0004_professional_provider_tracking")]

    operations = [
        migrations.AddField(
            model_name="pesapalsettings",
            name="ipn_url",
            field=models.CharField(
                blank=True, editable=False, max_length=300,
                help_text="The IPN URL that ipn_id was registered for. If the site address changes, the IPN is re-registered automatically.",
            ),
        ),
        migrations.AlterField(
            model_name="pesapaltransaction",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("SUBSCRIPTION", "New Subscription"),
                    ("SUBSCRIPTION_RENEWAL", "Subscription Renewal"),
                    ("BRANCH_ADDON", "Branch Add-on"),
                    ("BRANCH_ADDON_RENEWAL", "Branch Add-on Renewal"),
                    ("SMS_DEBT_PACKAGE", "Premium Debt SMS Package"),
                    ("SMS_RECEIPT_PACKAGE", "Premium Receipt SMS Package"),
                    ("SMS_MARKETING_PACKAGE", "Customer Marketing SMS Package"),
                    ("DAILY_REPORT_PACKAGE", "Intelligent Report SMS Package"),
                ],
                max_length=30,
            ),
        ),
    ]
