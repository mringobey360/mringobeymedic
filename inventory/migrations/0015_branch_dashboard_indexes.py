from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0014_product_requires_expiry'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='product',
            index=models.Index(fields=['pharmacy', 'is_active', 'name'], name='product_branch_active_name_idx'),
        ),
        migrations.AddIndex(
            model_name='productbatch',
            index=models.Index(fields=['product', 'quantity', 'expiry_date'], name='batch_product_qty_exp_idx'),
        ),
    ]
