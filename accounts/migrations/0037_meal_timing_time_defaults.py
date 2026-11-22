import datetime

from django.db import migrations, models


class Migration(migrations.Migration):
    """Use real ``datetime.time`` defaults for meal windows.

    The old string defaults ('10:00') were returned as strings on the request that created a
    branch's MealTimingSettings row, which crashed the employee dashboard with
    ``TypeError: combine() argument 2 must be datetime.time, not str``. No data changes.
    """

    dependencies = [
        ('accounts', '0036_attendance_owner_sms_tracking'),
    ]

    operations = [
        migrations.AlterField(model_name='mealtimingsettings', name='breakfast_start', field=models.TimeField(default=datetime.time(7, 0))),
        migrations.AlterField(model_name='mealtimingsettings', name='breakfast_cutoff', field=models.TimeField(default=datetime.time(10, 0))),
        migrations.AlterField(model_name='mealtimingsettings', name='lunch_start', field=models.TimeField(default=datetime.time(12, 0))),
        migrations.AlterField(model_name='mealtimingsettings', name='lunch_cutoff', field=models.TimeField(default=datetime.time(15, 0))),
        migrations.AlterField(model_name='mealtimingsettings', name='dinner_start', field=models.TimeField(default=datetime.time(18, 0))),
        migrations.AlterField(model_name='mealtimingsettings', name='dinner_cutoff', field=models.TimeField(default=datetime.time(21, 0))),
    ]
