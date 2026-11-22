from django.db import migrations, models


class Migration(migrations.Migration):
    """Expiry date becomes optional so non-expiring products (cosmetics, accessories, ...) can be stocked."""

    dependencies = [
        ('inventory', '0012_stockmovement_return_types'),
    ]

    operations = [
        migrations.AlterField(
            model_name='productbatch',
            name='expiry_date',
            field=models.DateField(blank=True, help_text='Optional. Leave blank for products that do not expire.', null=True),
        ),
    ]
