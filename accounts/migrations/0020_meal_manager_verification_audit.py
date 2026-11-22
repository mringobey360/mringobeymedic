from django.db import migrations, models


class Migration(migrations.Migration):
    """Audit fields for manager-verified meals (network / power / system outage recovery)."""

    dependencies = [("accounts", "0019_meal_scan_claims_and_issue_reports")]

    operations = [
        migrations.AddField(
            model_name="mealledger", name="manual_reason",
            field=models.CharField(blank=True, choices=[("NETWORK_OUTAGE", "Network outage"), ("POWER_OUTAGE", "Power outage"), ("SYSTEM_OUTAGE", "System unavailable"), ("QR_UNAVAILABLE", "Branch QR unavailable"), ("OTHER", "Other (note required)")], max_length=20),
        ),
        migrations.AddField(model_name="mealledger", name="manual_note", field=models.CharField(blank=True, max_length=500)),
    ]
