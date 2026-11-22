from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0012_finalize_weekly_meal_policy')]
    operations = [
        migrations.AlterField(
            model_name='attendancerecord', name='method',
            field=models.CharField(
                choices=[('GEOFENCE', 'GPS geofence'), ('QR', 'Dynamic branch QR'), ('MANUAL', 'Manager approved')],
                default='GEOFENCE', max_length=10),
        ),
    ]
