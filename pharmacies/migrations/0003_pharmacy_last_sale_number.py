from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pharmacies', '0002_branchaddon'),
    ]

    operations = [
        migrations.AddField(
            model_name='pharmacy',
            name='last_sale_number',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Internal counter — the last receipt/sale number issued at this branch. Do not edit manually.',
            ),
        ),
    ]
