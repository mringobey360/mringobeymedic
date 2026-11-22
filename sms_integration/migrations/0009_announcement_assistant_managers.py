from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sms_integration', '0008_smslog_provider_delivery_tracking'),
    ]

    operations = [
        migrations.AddField(
            model_name='smsannouncement',
            name='include_assistants',
            field=models.BooleanField(
                default=True,
                help_text="The announcement was also delivered to the customers' active assistant managers.",
            ),
        ),
        migrations.AlterField(
            model_name='smsannouncement',
            name='audience',
            field=models.CharField(
                choices=[
                    ('ACTIVE', 'Customers with an active subscription'),
                    ('ALL', 'All active customer accounts'),
                    ('EXPIRING', 'Subscriptions expiring within 7 days'),
                    ('EXPIRED', 'Customers with an expired subscription'),
                    ('SELECTED', 'Selected customers only'),
                    ('ASSISTANTS', 'Assistant managers only'),
                ],
                default='ACTIVE', max_length=10,
            ),
        ),
    ]
