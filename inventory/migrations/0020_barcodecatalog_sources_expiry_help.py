from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0019_barcodecatalog'),
    ]

    operations = [
        migrations.AlterField(
            model_name='barcodecatalog',
            name='source',
            field=models.CharField(blank=True, choices=[('openfoodfacts', 'Open Food Facts'), ('openbeautyfacts', 'Open Beauty Facts'), ('openproductsfacts', 'Open Products Facts'), ('upcitemdb', 'UPCitemdb'), ('smartpos', 'SmartPOS catalog')], max_length=20),
        ),
        migrations.AlterField(
            model_name='product',
            name='requires_expiry',
            field=models.BooleanField(default=True, help_text='This product has an expiry date (e.g. medicines). When on, every new batch must have an expiry date. Turn off for items that do not expire, such as cosmetics and accessories.'),
        ),
    ]
