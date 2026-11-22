from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hq', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentsettings',
            name='branch_addon_price',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('12500'), max_digits=12,
                help_text='Monthly price for each branch beyond the first (included) branch.',
            ),
        ),
    ]
