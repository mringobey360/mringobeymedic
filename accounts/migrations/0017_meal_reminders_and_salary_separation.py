from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("accounts", "0016_professional_payroll")]

    operations = [
        migrations.AddField(model_name="mealtimingsettings", name="breakfast_start", field=models.TimeField(default="07:00")),
        migrations.AddField(model_name="mealtimingsettings", name="lunch_start", field=models.TimeField(default="12:00")),
        migrations.AddField(model_name="mealtimingsettings", name="dinner_start", field=models.TimeField(default="18:00")),
        migrations.AddField(model_name="mealtimingsettings", name="reminder_minutes_before", field=models.PositiveSmallIntegerField(default=0, help_text="Send the push reminder this many minutes before the meal window opens.")),
        migrations.CreateModel(
            name="PushSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("endpoint", models.URLField(max_length=1000, unique=True)),
                ("p256dh", models.CharField(max_length=255)),
                ("auth", models.CharField(max_length=255)),
                ("user_agent", models.CharField(blank=True, max_length=500)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("last_error", models.TextField(blank=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="push_subscriptions", to="accounts.user")),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.CreateModel(
            name="MealReminderDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField()),
                ("meal_type", models.CharField(max_length=12)),
                ("scheduled_for", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("delivered_count", models.PositiveIntegerField(default=0)),
                ("pharmacy", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="meal_reminder_deliveries", to="pharmacies.pharmacy")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("pharmacy", "date", "meal_type"), name="unique_meal_reminder_delivery")], "indexes": [models.Index(fields=["scheduled_for"], name="accounts_meal_scheduled_idx")]},
        ),
        migrations.AddIndex(model_name="pushsubscription", index=models.Index(fields=["user", "active"], name="accounts_push_user_active_idx")),
    ]
