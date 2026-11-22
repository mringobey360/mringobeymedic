from django.db import migrations, models
import uuid


def populate_receipt_tokens(apps, schema_editor):
    Sale = apps.get_model('sales', 'Sale')
    for sale in Sale.objects.filter(receipt_access_token__isnull=True).only('pk'):
        Sale.objects.filter(pk=sale.pk).update(receipt_access_token=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [('sales', '0004_saleitem_discount_fields')]
    operations = [
        migrations.AddField(
            model_name='sale', name='receipt_access_token',
            field=models.UUIDField(db_index=True, editable=False, null=True),
        ),
        migrations.RunPython(populate_receipt_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='sale', name='receipt_access_token',
            field=models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
