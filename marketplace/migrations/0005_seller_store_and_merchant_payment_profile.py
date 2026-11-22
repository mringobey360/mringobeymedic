import django.db.models.deletion
from django.db import migrations, models
from core.encrypted_fields import EncryptedTextField

class Migration(migrations.Migration):
    dependencies = [('marketplace', '0004_product_seller_name'), ('pharmacies', '0001_initial')]
    operations = [
        migrations.CreateModel(name='SellerStore', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('slug', models.SlugField(max_length=180, unique=True)),
            ('public_name', models.CharField(max_length=150)),
            ('description', models.TextField(blank=True)),
            ('logo', models.ImageField(blank=True, upload_to='marketplace/stores/')),
            ('is_published', models.BooleanField(default=False)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('pharmacy', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='marketplace_store', to='pharmacies.pharmacy')),
        ], options={'ordering':['public_name']}),
        migrations.CreateModel(name='MerchantPaymentProfile', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('provider', models.CharField(blank=True, choices=[('ABLINER','ABLiner'),('PESAPAL','Pesapal')], max_length=12)),
            ('abliner_api_key', EncryptedTextField(blank=True)),
            ('abliner_webhook_secret', EncryptedTextField(blank=True)),
            ('abliner_base_url', models.URLField(blank=True, default='https://abliner.net')),
            ('pesapal_environment', models.CharField(choices=[('SANDBOX','Sandbox'),('LIVE','Live')], default='SANDBOX', max_length=10)),
            ('pesapal_consumer_key', EncryptedTextField(blank=True)),
            ('pesapal_consumer_secret', EncryptedTextField(blank=True)),
            ('verified', models.BooleanField(default=False, help_text='Set only after provider-side merchant approval and a verified integration test.')),
            ('enabled', models.BooleanField(default=False)),
            ('last_verified_at', models.DateTimeField(blank=True, null=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('store', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='payment_profile', to='marketplace.sellerstore')),
        ]),
        migrations.AddField(model_name='product', name='seller_store', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='products', to='marketplace.sellerstore')),
    ]
