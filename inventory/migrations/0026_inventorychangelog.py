from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('pharmacies', '0001_initial'),
        ('inventory', '0025_product_image'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='InventoryChangeLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('product_name', models.CharField(blank=True, help_text='Name at the time of the change.', max_length=200)),
                ('batch_number', models.CharField(blank=True, max_length=100)),
                ('action', models.CharField(choices=[('PRODUCT_CREATED', 'Product registered'), ('PRODUCT_EDITED', 'Product edited'), ('PRODUCT_ARCHIVED', 'Product removed from catalog'), ('PRODUCT_PURGED', 'Product permanently deleted'), ('PRODUCT_MERGED', 'Products merged'), ('BATCH_ADDED', 'Batch added'), ('BATCH_EDITED', 'Batch edited')], db_index=True, max_length=20)),
                ('summary', models.CharField(blank=True, max_length=255)),
                ('changes', models.JSONField(blank=True, default=list, help_text='List of {field, label, old, new}.')),
                ('reason', models.CharField(blank=True, help_text='Reason given by the person who made the change.', max_length=255)),
                ('performed_by_name', models.CharField(blank=True, max_length=150)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('batch', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='change_logs', to='inventory.productbatch')),
                ('performed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='inventory_changes', to=settings.AUTH_USER_MODEL)),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='inventory_change_logs', to='pharmacies.pharmacy')),
                ('product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='change_logs', to='inventory.product')),
            ],
            options={
                'ordering': ['-created_at', '-pk'],
                'indexes': [models.Index(fields=['pharmacy', '-created_at'], name='inventory_i_pharmac_chg_idx'), models.Index(fields=['product', '-created_at'], name='inventory_i_product_chg_idx')],
            },
        ),
    ]
