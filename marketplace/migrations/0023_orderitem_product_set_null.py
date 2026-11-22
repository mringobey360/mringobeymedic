import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """OrderItem.product: PROTECT -> SET_NULL so products can be deleted from the admin.

    Order lines keep their own name/unit_price snapshot, so order history stays intact.
    """
    dependencies = [('marketplace', '0022_preview_random_name')]
    operations = [
        migrations.AlterField(
            model_name='orderitem',
            name='product',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='marketplace.product'),
        ),
    ]
