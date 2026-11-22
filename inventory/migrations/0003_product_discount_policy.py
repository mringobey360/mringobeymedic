from django.db import migrations, models
from decimal import Decimal


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0002_stockimport_productbatch_stock_import"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="discount_enabled",
            field=models.BooleanField(default=False, help_text="Allow shop employees to apply a discount to this product in the POS."),
        ),
        migrations.AddField(
            model_name="product",
            name="min_discount_percent",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), help_text="Minimum discount percentage a seller may apply.", max_digits=5),
        ),
        migrations.AddField(
            model_name="product",
            name="max_discount_percent",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), help_text="Maximum discount percentage a seller may apply.", max_digits=5),
        ),
    ]
