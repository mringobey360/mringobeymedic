from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0023_unitcatalog'),
    ]

    operations = [
        migrations.AddField(
            model_name='productbatch',
            name='discount_cap_amount',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=12, null=True,
                validators=[django.core.validators.MinValueValidator(Decimal('0'))],
                help_text="Optional. The most that can be taken off ONE unit of this batch (TZS), including any VIP discount. "
                          "Leave blank to follow the product's discount policy. Enter 0 to allow no discount on this batch.",
            ),
        ),
    ]
