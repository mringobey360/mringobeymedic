from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('accounts', '0022_meal_workday_reopen_audit')]

    operations = [
        migrations.AlterField(
            model_name='mealissuereport', name='status',
            field=models.CharField(max_length=20, choices=[
                ('PENDING', 'Pending verification'),
                ('SCAN_REOPENED', 'Reopened for employee scan'),
                ('APPROVED', 'Manually verified'),
                ('RETURNED', 'Returned to employee'),
                ('REJECTED', 'Rejected'),
                ('RESOLVED', 'Resolved by scan'),
            ], default='PENDING'),
        ),
        migrations.AddField(
            model_name='mealissuereport', name='returned_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='mealissuereport', name='return_count',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='mealledger', name='scan_reopened_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='mealledger', name='scan_reopened_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reopened_meal_scans', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='mealledger', name='scan_reopen_note',
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
