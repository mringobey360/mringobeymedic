from django.db import migrations, models


class Migration(migrations.Migration):
    """Late meal requests: manager-approved requests made after the window, with a weekly limit."""

    dependencies = [("accounts", "0020_meal_manager_verification_audit")]

    operations = [
        migrations.AddField(
            model_name="mealtimingsettings", name="late_requests_per_week",
            field=models.PositiveSmallIntegerField(default=3, help_text="Maximum late meal requests an employee may submit per week (0 turns late requests off).", verbose_name="Late meal requests per week"),
        ),
        migrations.AddField(
            model_name="mealissuereport", name="is_late",
            field=models.BooleanField(default=False, help_text="Submitted after the meal window ended. Needs manager approval and counts against the weekly limit."),
        ),
        migrations.AlterField(
            model_name="mealissuereport", name="reason",
            field=models.CharField(choices=[("QR_NOT_SCANNING", "QR not scanning"), ("CAMERA_UNAVAILABLE", "Camera unavailable"), ("NETWORK_PROBLEM", "Network problem"), ("QR_EXPIRED", "QR expired"), ("DEVICE_PROBLEM", "Device problem"), ("BUSY_AT_WORK", "Busy at work (late request)"), ("OTHER", "Other")], max_length=24),
        ),
    ]
