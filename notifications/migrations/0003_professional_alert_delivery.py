from django.db import migrations, models


def mark_existing_notifications_as_already_delivered(apps, schema_editor):
    Notification = apps.get_model('notifications', 'Notification')
    Notification.objects.filter(push_sent_at__isnull=True).update(push_sent_at=models.F('created_at'))


class Migration(migrations.Migration):
    dependencies = [
        ('notifications', '0002_branch_notification_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='dedupe_key',
            field=models.CharField(blank=True, default='', max_length=220),
        ),
        migrations.AddField(
            model_name='notification',
            name='push_attempts',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='notification',
            name='push_last_error',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='notification',
            name='push_sent_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(fields=['push_sent_at', 'push_attempts'], name='notif_push_pending_idx'),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(fields=['dedupe_key'], name='notif_dedupe_key_idx'),
        ),
        migrations.RunPython(mark_existing_notifications_as_already_delivered, migrations.RunPython.noop),
    ]
