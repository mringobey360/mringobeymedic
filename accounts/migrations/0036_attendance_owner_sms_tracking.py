from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0035_user_google_sub'),
    ]

    operations = [
        migrations.AddField(
            model_name='attendancerecord',
            name='check_in_sms_sent_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='attendancerecord',
            name='check_out_sms_sent_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
