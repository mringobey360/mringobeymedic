import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0016_product_discount_amount_policy'),
        ('pharmacies', '0015_pharmacy_attendance_qr_requires_location'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='stockmovement',
            name='movement_type',
            field=models.CharField(choices=[('NEW_BATCH', 'New Batch Received'), ('SALE', 'Sale'), ('ADJUSTMENT', 'Manual Adjustment'), ('BULK_UPLOAD', 'Bulk Upload'), ('SALE_RETURN', 'Sale Return (Restocked)'), ('WRITE_OFF', 'Returned Item Written Off'), ('TRANSFER_OUT', 'Branch Transfer Out'), ('TRANSFER_IN', 'Branch Transfer In')], max_length=15),
        ),
        migrations.CreateModel(
            name='BranchStockTransfer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reference', models.CharField(editable=False, max_length=32, unique=True)),
                ('quantity', models.PositiveIntegerField()),
                ('source_quantity_after', models.PositiveIntegerField(default=0, help_text='Source batch quantity immediately after this transfer.')),
                ('status', models.CharField(choices=[('COMPLETED', 'Completed'), ('CANCELLED', 'Cancelled')], default='COMPLETED', max_length=12)),
                ('note', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='branch_stock_transfers', to=settings.AUTH_USER_MODEL)),
                ('destination_batch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='transfers_in', to='inventory.productbatch')),
                ('destination_pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stock_transfers_in', to='pharmacies.pharmacy')),
                ('source_batch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='transfers_out', to='inventory.productbatch')),
                ('source_pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stock_transfers_out', to='pharmacies.pharmacy')),
            ],
            options={'ordering': ['-created_at', '-pk']},
        ),
        migrations.AddIndex(model_name='branchstocktransfer', index=models.Index(fields=['source_pharmacy', '-created_at'], name='inventory_b_source__d1a4b3_idx')),
        migrations.AddIndex(model_name='branchstocktransfer', index=models.Index(fields=['destination_pharmacy', '-created_at'], name='inventory_b_destina_1e63f1_idx')),
    ]
