from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('notifications', '0003_professional_alert_delivery'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification', name='action_url',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='notification', name='action_label',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
    ]
