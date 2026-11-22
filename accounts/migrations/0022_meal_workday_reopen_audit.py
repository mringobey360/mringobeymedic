from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('accounts', '0021_meal_late_requests')]

    operations = [
        migrations.AddField(
            model_name='mealworkday', name='reopened_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='mealworkday', name='reopen_note',
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name='mealworkday', name='reopened_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reopened_meal_workdays', to=settings.AUTH_USER_MODEL),
        ),
    ]
