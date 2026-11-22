from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("inventory", "0007_product_sku")]
    operations = [migrations.AddField(
        model_name="stockimport", name="file_sha256",
        field=models.CharField(blank=True, default="", help_text="Fingerprint used to prevent accidental duplicate uploads.", max_length=64),
    )]
