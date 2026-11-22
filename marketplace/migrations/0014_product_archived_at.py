from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('marketplace', '0013_reconcile_legacy_product_type')]
    operations = [migrations.AddField(model_name='product', name='archived_at', field=models.DateTimeField(null=True, blank=True, db_index=True, help_text='Removed from seller catalogue; retained for order and download history.'))]
