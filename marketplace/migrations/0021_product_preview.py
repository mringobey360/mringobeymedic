from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('marketplace', '0020_pendingupload')]
    operations = [
        migrations.AddField(model_name='product', name='preview_file', field=models.FileField(blank=True, help_text='Short PUBLIC sample (PDF, MP3, MP4 or WebM). The full product stays private until the buyer pays.', max_length=255, upload_to='marketplace/previews/')),
        migrations.AddField(model_name='product', name='whats_inside', field=models.TextField(blank=True, default='', help_text='One item per line. Shown to buyers as a checklist.')),
        migrations.AddField(model_name='product', name='content_summary', field=models.CharField(blank=True, default='', help_text='Size of the full product, e.g. "120 pages" or "8 lessons - 2h 30m".', max_length=80)),
        migrations.AddField(model_name='product', name='content_language', field=models.CharField(blank=True, default='', help_text='e.g. Kiswahili, English.', max_length=30)),
    ]
