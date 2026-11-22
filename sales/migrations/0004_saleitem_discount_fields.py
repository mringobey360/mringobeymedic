from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0003_add_performance_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="saleitem",
            name="original_unit_price",
            field=models.DecimalField(decimal_places=2, default=0, help_text="Selling price before discount.", max_digits=12),
        ),
        migrations.AddField(
            model_name="saleitem",
            name="discount_percent",
            field=models.DecimalField(decimal_places=2, default=0, help_text="Discount percentage granted on this line.", max_digits=5),
        ),
        migrations.AddField(
            model_name="saleitem",
            name="discount_amount",
            field=models.DecimalField(decimal_places=2, default=0, help_text="Total discount amount granted on this line.", max_digits=12),
        ),
    ]
