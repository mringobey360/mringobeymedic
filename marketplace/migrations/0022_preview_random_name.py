from django.db import migrations, models

import marketplace.storage


class Migration(migrations.Migration):
    """New free samples are stored under a random, non-guessable file name (no data change)."""
    dependencies = [('marketplace', '0021_product_preview')]
    operations = [
        migrations.AlterField(model_name='product', name='preview_file', field=models.FileField(blank=True, help_text='Short PUBLIC sample (PDF, MP3, MP4 or WebM). The full product stays private until the buyer pays.', max_length=255, upload_to=marketplace.storage.preview_upload_to)),
    ]
