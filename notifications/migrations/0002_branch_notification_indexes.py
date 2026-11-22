from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('notifications', '0001_initial'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(
                fields=['recipient', 'pharmacy', 'is_read'],
                name='notif_recip_pharm_read_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(
                fields=['recipient', 'pharmacy', '-created_at'],
                name='notif_recip_pharm_date_idx',
            ),
        ),
    ]
