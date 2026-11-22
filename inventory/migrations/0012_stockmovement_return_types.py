from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('inventory', '0011_generate_missing_batch_numbers')]

    operations = [
        migrations.AlterField(
            model_name='stockmovement',
            name='movement_type',
            field=models.CharField(
                choices=[
                    ('NEW_BATCH', 'New Batch Received'),
                    ('SALE', 'Sale'),
                    ('ADJUSTMENT', 'Manual Adjustment'),
                    ('BULK_UPLOAD', 'Bulk Upload'),
                    ('SALE_RETURN', 'Sale Return (Restocked)'),
                    ('WRITE_OFF', 'Returned Item Written Off'),
                ],
                max_length=15,
            ),
        ),
    ]
