from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('sms_integration', '0007_announcement_title_signature_standard'),
    ]

    operations = [
        migrations.AddField(
            model_name='smslog', name='provider_status',
            field=models.CharField(blank=True, editable=False, max_length=60),
        ),
        migrations.AddField(
            model_name='smslog', name='provider_message_id',
            field=models.CharField(blank=True, editable=False, max_length=120),
        ),
        migrations.AddField(
            model_name='smslog', name='delivery_status',
            field=models.CharField(blank=True, editable=False, max_length=40),
        ),
        migrations.AddField(
            model_name='smslog', name='delivery_error',
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name='smslog', name='delivery_updated_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]
