from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Scan-to-claim meals: MISSED status, claim audit fields and MealIssueReport.

    Existing ledger rows keep the old behaviour (scan_required=False) so history
    is never re-classified as Missed. Rows created from now on default to True.
    """

    dependencies = [
        ("pharmacies", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("accounts", "0018_professional_qr_meal_checkpoints"),
    ]

    operations = [
        migrations.AddField(model_name="mealledger", name="scan_required", field=models.BooleanField(default=False)),
        migrations.AlterField(model_name="mealledger", name="scan_required", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="mealledger", name="claimed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="mealledger", name="claim_method", field=models.CharField(blank=True, choices=[("QR", "Scanned"), ("MANUAL", "Manually verified")], max_length=10)),
        migrations.AddField(model_name="mealledger", name="missed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(
            model_name="mealledger", name="verified_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="verified_meal_claims", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name="mealledger", name="status",
            field=models.CharField(choices=[("PENDING", "Pending"), ("EARNED", "Earned / Funded"), ("DEBT", "Meal Debt"), ("WAIVED", "Waived"), ("MISSED", "Missed (not scanned)")], default="PENDING", max_length=10),
        ),
        migrations.CreateModel(
            name="MealIssueReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField()),
                ("meal_type", models.CharField(choices=[("BREAKFAST", "Breakfast / Tea"), ("LUNCH", "Lunch"), ("DINNER", "Dinner")], max_length=12)),
                ("window_start_at", models.DateTimeField()),
                ("window_end_at", models.DateTimeField()),
                ("reason", models.CharField(choices=[("QR_NOT_SCANNING", "QR not scanning"), ("CAMERA_UNAVAILABLE", "Camera unavailable"), ("NETWORK_PROBLEM", "Network problem"), ("QR_EXPIRED", "QR expired"), ("DEVICE_PROBLEM", "Device problem"), ("OTHER", "Other")], max_length=24)),
                ("details", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("PENDING", "Pending verification"), ("APPROVED", "Manually verified"), ("REJECTED", "Rejected"), ("RESOLVED", "Resolved by scan")], default="PENDING", max_length=10)),
                ("reported_at", models.DateTimeField(auto_now_add=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("decision_note", models.CharField(blank=True, max_length=500)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="decided_meal_issues", to=settings.AUTH_USER_MODEL)),
                ("employee", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="meal_issue_reports", to=settings.AUTH_USER_MODEL)),
                ("pharmacy", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="meal_issue_reports", to="pharmacies.pharmacy")),
            ],
            options={"ordering": ["-reported_at"]},
        ),
        migrations.AddConstraint(model_name="mealissuereport", constraint=models.UniqueConstraint(fields=("employee", "date", "meal_type"), name="unique_meal_issue_day_type")),
        migrations.AddIndex(model_name="mealissuereport", index=models.Index(fields=["pharmacy", "status", "-reported_at"], name="acc_mealissue_ph_st_idx")),
    ]
