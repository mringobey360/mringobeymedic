from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sms_integration', '0006_customer_marketing_sms'),
    ]

    operations = [
        migrations.AlterField(
            model_name='smsannouncement',
            name='title',
            field=models.CharField(
                help_text='Official announcement title shown to customers in SMS and in-app notifications.',
                max_length=120,
            ),
        ),
        migrations.AlterField(
            model_name='smsannouncement',
            name='message',
            field=models.TextField(
                help_text='Message body. The final customer SMS is composed as: title + message + administrator signature. {name} is replaced with the customer first name; {link} (optional) with a trackable link to link_url.',
            ),
        ),
    ]
