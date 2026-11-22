import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


def backfill_legacy_items(apps, schema_editor):
    """Give every pre-V54.25 single-batch transfer one item row so history is uniform."""
    Transfer = apps.get_model('inventory', 'BranchStockTransfer')
    Item = apps.get_model('inventory', 'BranchStockTransferItem')
    for t in Transfer.objects.filter(source_batch__isnull=False, destination_batch__isnull=False).select_related('source_batch__product', 'destination_batch').iterator():
        if Item.objects.filter(transfer=t).exists():
            continue
        src, dst = t.source_batch, t.destination_batch
        Item.objects.create(
            transfer=t, source_batch=src, destination_batch=dst,
            product_name=src.product.name, unit=src.product.unit,
            source_batch_number=src.batch_number or '', quantity=t.quantity,
            source_quantity_after=t.source_quantity_after,
            purchase_price=dst.purchase_price, selling_price=dst.selling_price, expiry_date=dst.expiry_date,
        )
        Transfer.objects.filter(pk=t.pk).update(item_count=1)


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0017_branch_stock_transfer'),
    ]

    operations = [
        migrations.AlterField(
            model_name='branchstocktransfer', name='source_batch',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='transfers_out', to='inventory.productbatch'),
        ),
        migrations.AlterField(
            model_name='branchstocktransfer', name='destination_batch',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='transfers_in', to='inventory.productbatch'),
        ),
        migrations.AlterField(
            model_name='branchstocktransfer', name='quantity',
            field=models.PositiveIntegerField(help_text='Total units moved across all items.'),
        ),
        migrations.AddField(
            model_name='branchstocktransfer', name='item_count',
            field=models.PositiveIntegerField(default=0, help_text='Number of batches (lines) in this transfer.'),
        ),
        migrations.AddField(
            model_name='branchstocktransfer', name='idempotency_key',
            field=models.CharField(blank=True, editable=False, help_text='Client token that makes retries/double submits safe.', max_length=64, null=True, unique=True),
        ),
        migrations.CreateModel(
            name='BranchStockTransferItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('product_name', models.CharField(max_length=200)),
                ('unit', models.CharField(blank=True, max_length=10)),
                ('source_batch_number', models.CharField(blank=True, max_length=100)),
                ('quantity', models.PositiveIntegerField()),
                ('source_quantity_after', models.PositiveIntegerField(default=0)),
                ('purchase_price', models.DecimalField(decimal_places=2, max_digits=12)),
                ('selling_price', models.DecimalField(decimal_places=2, max_digits=12)),
                ('expiry_date', models.DateField(blank=True, null=True)),
                ('destination_batch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='transfer_items_in', to='inventory.productbatch')),
                ('source_batch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='transfer_items_out', to='inventory.productbatch')),
                ('transfer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='inventory.branchstocktransfer')),
            ],
            options={'ordering': ['product_name', 'pk']},
        ),
        migrations.AddConstraint(
            model_name='branchstocktransferitem',
            constraint=models.UniqueConstraint(fields=('transfer', 'source_batch'), name='uniq_transfer_item_source_batch'),
        ),
        migrations.AddConstraint(
            model_name='branchstocktransferitem',
            constraint=models.CheckConstraint(condition=Q(('quantity__gte', 1)), name='transfer_item_quantity_gte_1'),
        ),
        migrations.RunPython(backfill_legacy_items, migrations.RunPython.noop),
    ]
