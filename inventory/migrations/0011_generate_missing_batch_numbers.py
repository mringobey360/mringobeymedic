from django.db import migrations
from django.utils import timezone
import secrets


def generate_missing_batch_numbers(apps, schema_editor):
    ProductBatch = apps.get_model("inventory", "ProductBatch")
    for batch in ProductBatch.objects.select_related("product").filter(batch_number=""):
        prefix = f"BATCH-{timezone.localdate():%Y%m%d}-"
        while True:
            value = prefix + secrets.token_hex(4).upper()
            if not ProductBatch.objects.filter(product_id=batch.product_id, batch_number=value).exists():
                batch.batch_number = value
                batch.save(update_fields=["batch_number"])
                break


def reverse_missing_batch_numbers(apps, schema_editor):
    # Do not erase generated identifiers on rollback; batch references are audit data.
    pass


class Migration(migrations.Migration):
    dependencies = [("inventory", "0010_stockimport_reused_batches")]
    operations = [migrations.RunPython(generate_missing_batch_numbers, reverse_missing_batch_numbers)]
