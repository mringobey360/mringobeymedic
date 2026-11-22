from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('marketplace', '0016_sellerstore_social_links')]
    operations = [
        migrations.AddField(model_name='sellerstore', name='whatsapp_url', field=models.URLField(blank=True, default='', max_length=200, help_text='Seller WhatsApp chat link, built from the number they enter.')),
        migrations.AddField(model_name='sellerstore', name='youtube_url', field=models.URLField(blank=True, default='', max_length=200, help_text='Seller YouTube channel (cleaned automatically).')),
    ]
