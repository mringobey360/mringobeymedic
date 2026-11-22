from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0022_product_is_demo'),
        ('pharmacies', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='UnitCatalog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=30)),
                ('normalized_name', models.CharField(editable=False, max_length=30)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='custom_units', to='pharmacies.pharmacy')),
            ],
            options={'ordering': ['name']},
        ),
        migrations.AddConstraint(
            model_name='unitcatalog',
            constraint=models.UniqueConstraint(fields=('pharmacy', 'normalized_name'), name='uniq_unit_per_pharmacy'),
        ),
    ]
