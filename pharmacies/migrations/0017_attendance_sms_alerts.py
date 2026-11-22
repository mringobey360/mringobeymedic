import datetime

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pharmacies', '0016_business_types_online_seller_real_estate'),
    ]

    operations = [
        migrations.AddField(
            model_name='pharmacy',
            name='attendance_sms_mode',
            field=models.CharField(choices=[('OFF', 'Off - no attendance SMS'), ('EXCEPTIONS', 'Exceptions only (recommended) - late, early leave, approval requests'), ('ALL', 'Every check-in and check-out')], default='OFF', help_text="Choose how the branch owner is told about employee attendance by SMS. 'Exceptions only' keeps SMS cost low and only alerts you when something needs attention.", max_length=12),
        ),
        migrations.AddField(
            model_name='pharmacy',
            name='attendance_sms_work_start',
            field=models.TimeField(default=datetime.time(8, 0), help_text='Official time employees should start work. Used to decide if a check-in is late.'),
        ),
        migrations.AddField(
            model_name='pharmacy',
            name='attendance_sms_work_end',
            field=models.TimeField(default=datetime.time(17, 0), help_text='Official closing time. Used to decide if a check-out is early.'),
        ),
        migrations.AddField(
            model_name='pharmacy',
            name='attendance_sms_grace_minutes',
            field=models.PositiveIntegerField(default=10, help_text='Minutes of tolerance before a check-in counts as late (or a check-out as early).'),
        ),
    ]
