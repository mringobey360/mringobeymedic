import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0018_bulk_stock_transfer'),
    ]

    operations = [
        migrations.CreateModel(
            name='BarcodeCatalog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('barcode', models.CharField(max_length=32, unique=True)),
                ('found', models.BooleanField(default=True)),
                ('name', models.CharField(blank=True, max_length=200)),
                ('brand', models.CharField(blank=True, max_length=100)),
                ('quantity', models.CharField(blank=True, max_length=60)),
                ('image_url', models.URLField(blank=True, max_length=500)),
                ('source', models.CharField(blank=True, choices=[('openfoodfacts', 'Open Food Facts'), ('upcitemdb', 'UPCitemdb')], max_length=20)),
                ('looked_up_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={'ordering': ['-looked_up_at']},
        ),
    ]
