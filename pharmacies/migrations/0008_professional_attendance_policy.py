from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pharmacies', '0007_professional_presence_settings'),
    ]

    operations = [
        migrations.AddField(
            model_name='pharmacy',
            name='attendance_lunch_rescan_required',
            field=models.BooleanField(
                default=False,
                help_text='If enabled, employees must complete one additional attendance scan during the configured lunch window.',
            ),
        ),
        migrations.AddField(
            model_name='pharmacy',
            name='attendance_lunch_rescan_start',
            field=models.TimeField(default='12:00', help_text='Start of the optional lunch attendance verification window.'),
        ),
        migrations.AddField(
            model_name='pharmacy',
            name='attendance_lunch_rescan_end',
            field=models.TimeField(default='14:00', help_text='End of the optional lunch attendance verification window.'),
        ),
        migrations.AddField(
            model_name='pharmacy',
            name='attendance_outside_grace_minutes',
            field=models.PositiveIntegerField(
                default=5,
                help_text='Minutes an employee may remain outside the geofence before the manager is alerted.',
            ),
        ),
    ]
