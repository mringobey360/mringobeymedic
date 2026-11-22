from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("accounts", "0017_meal_reminders_and_salary_separation")]
    operations = [
        migrations.AlterField(
            model_name="attendancecheckpoint", name="checkpoint_type",
            field=models.CharField(max_length=12, choices=[("BREAKFAST", "Breakfast verification"), ("LUNCH", "Lunch verification"), ("DINNER", "Dinner verification"), ("CHECKOUT", "End-of-shift check-out")]),
        ),
    ]
