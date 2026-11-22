from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0010_listings'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='compare_at_price',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Old price before the discount. Shown crossed out next to the current price.', max_digits=13, null=True),
        ),
        migrations.AddField(
            model_name='sellerstore',
            name='verified',
            field=models.BooleanField(default=False, help_text='Identity / business checked by the platform owner. Shows a Verified badge on listings.'),
        ),
        migrations.CreateModel(
            name='ProductImage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to='marketplace/products/')),
                ('position', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='gallery', to='marketplace.product')),
            ],
            options={'ordering': ['position', 'pk']},
        ),
        migrations.CreateModel(
            name='ListingReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.CharField(choices=[('SCAM', 'Looks like a scam / asks for money first'), ('FAKE', 'Fake or misleading photos / details'), ('SOLD', 'Already sold or rented'), ('PRICE', 'Wrong price'), ('OTHER', 'Other')], max_length=6)),
                ('details', models.CharField(blank=True, max_length=500)),
                ('reporter_phone', models.CharField(blank=True, max_length=25)),
                ('handled', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('listing', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reports', to='marketplace.listing')),
            ],
            options={'ordering': ['handled', '-created_at']},
        ),
    ]
