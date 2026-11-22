from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('marketplace', '0015_product_reviews')]
    operations = [
        migrations.AddField(model_name='sellerstore', name='instagram_url', field=models.URLField(blank=True, default='', max_length=200, help_text='Seller Instagram profile (cleaned automatically).')),
        migrations.AddField(model_name='sellerstore', name='tiktok_url', field=models.URLField(blank=True, default='', max_length=200, help_text='Seller TikTok profile (cleaned automatically).')),
        migrations.AddField(model_name='sellerstore', name='facebook_url', field=models.URLField(blank=True, default='', max_length=200, help_text='Seller Facebook page or profile (cleaned automatically).')),
    ]
