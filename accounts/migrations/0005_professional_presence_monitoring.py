from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0004_attendance_verification')]
    operations = [
        migrations.AddField(model_name='attendancerecord', name='presence_status', field=models.CharField(choices=[('VERIFIED','At workplace'),('OUTSIDE','Outside workplace'),('STALE','Location stale'),('UNAVAILABLE','Location unavailable')], default='VERIFIED', max_length=12)),
        migrations.AddField(model_name='attendancerecord', name='last_presence_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='attendancerecord', name='last_presence_distance_m', field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
        migrations.AddField(model_name='attendancerecord', name='last_presence_accuracy_m', field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True)),
        migrations.AddField(model_name='attendancerecord', name='outside_since', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='attendancerecord', name='presence_check_count', field=models.PositiveIntegerField(default=0)),
    ]
