from django.db import migrations, models


class Migration(migrations.Migration):
    """Units are no longer a closed list: a branch can add its own (stored as typed). Standard units are unchanged."""

    dependencies = [
        ('inventory', '0020_barcodecatalog_sources_expiry_help'),
    ]

    operations = [
        migrations.AlterField(
            model_name='product',
            name='unit',
            field=models.CharField(default='TABLET', help_text='Smallest/base unit used for stock and sales.', max_length=30),
        ),
        migrations.AlterField(
            model_name='productbatch',
            name='purchase_unit',
            field=models.CharField(blank=True, default='', help_text='Unit used for this purchase, e.g. Box, Carton, Bottle. Leave blank when purchased as the base unit.', max_length=30),
        ),
        migrations.AlterField(
            model_name='branchstocktransferitem',
            name='unit',
            field=models.CharField(blank=True, max_length=30),
        ),
    ]
