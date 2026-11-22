from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('marketplace', '0017_sellerstore_whatsapp_youtube')]
    operations = [
        migrations.AddField(model_name='sellerstore', name='social_verified', field=models.JSONField(blank=True, default=dict, help_text="Platform -> the exact link SmartPOS verified. A badge shows only while the seller's link is unchanged.")),
    ]
