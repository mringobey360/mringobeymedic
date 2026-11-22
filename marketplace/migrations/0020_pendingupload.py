import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('marketplace', '0019_movies'),
    ]
    operations = [
        migrations.CreateModel(
            name='PendingUpload',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('filename', models.CharField(max_length=180)),
                ('size', models.PositiveBigIntegerField()),
                ('content_type', models.CharField(blank=True, max_length=100)),
                ('storage_name', models.CharField(max_length=255)),
                ('s3_upload_id', models.CharField(blank=True, max_length=512)),
                ('part_size', models.PositiveIntegerField()),
                ('status', models.CharField(choices=[('UPLOADING', 'Uploading'), ('COMPLETE', 'Complete'), ('FAILED', 'Failed'), ('ABORTED', 'Cancelled')], db_index=True, default='UPLOADING', max_length=10)),
                ('error', models.CharField(blank=True, max_length=255)),
                ('consumed', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('seller_store', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pending_uploads', to='marketplace.sellerstore')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='pendingupload',
            index=models.Index(fields=['seller_store', 'user', 'filename', 'size'], name='mk_pu_lookup_idx'),
        ),
    ]
