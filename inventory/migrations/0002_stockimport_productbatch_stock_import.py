from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('pharmacies', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='StockImport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mode', models.CharField(choices=[('OPENING', 'Opening Stock'), ('RESTOCK', 'Restock / Purchase')], max_length=10)),
                ('reference', models.CharField(max_length=40)),
                ('file_name', models.CharField(blank=True, max_length=255)),
                ('row_count', models.PositiveIntegerField(default=0)),
                ('new_products', models.PositiveIntegerField(default=0)),
                ('new_batches', models.PositiveIntegerField(default=0)),
                ('topped_up', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='stock_imports_created', to=settings.AUTH_USER_MODEL)),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='stock_imports', to='pharmacies.pharmacy')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddConstraint(
            model_name='stockimport',
            constraint=models.UniqueConstraint(fields=('pharmacy', 'reference'), name='inventory_stockimport_pharmacy_reference_uniq'),
        ),
        migrations.AddField(
            model_name='productbatch',
            name='stock_import',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='batches', to='inventory.stockimport'),
        ),
    ]
