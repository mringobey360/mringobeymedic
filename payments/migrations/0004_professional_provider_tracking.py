from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0003_daily_report_package_payment")]

    operations = [
        migrations.AlterField(
            model_name="pesapaltransaction",
            name="status",
            field=models.CharField(choices=[("PENDING", "Pending"), ("SUBMITTED", "Awaiting Payment"), ("COMPLETED", "Completed"), ("FAILED", "Failed"), ("CANCELLED", "Cancelled"), ("INVALID", "Invalid"), ("REVERSED", "Reversed")], default="PENDING", max_length=12),
        ),
        migrations.AddField(model_name="pesapaltransaction", name="payment_account", field=models.CharField(blank=True, editable=False, help_text="Masked payment account returned by Pesapal.", max_length=80)),
        migrations.AddField(model_name="pesapaltransaction", name="provider_created_at", field=models.DateTimeField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="pesapaltransaction", name="provider_status_code", field=models.IntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="pesapaltransaction", name="last_checked_at", field=models.DateTimeField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="pesapaltransaction", name="ipn_count", field=models.PositiveIntegerField(default=0, editable=False)),
    ]
