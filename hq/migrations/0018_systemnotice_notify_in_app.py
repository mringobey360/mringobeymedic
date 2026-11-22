from django.db import migrations, models
from django.utils import timezone


def mark_existing_as_sent(apps, schema_editor):
    """Notices that already started before this release must not suddenly ring every bell."""
    SystemNotice = apps.get_model('hq', 'SystemNotice')
    SystemNotice.objects.filter(starts_at__lte=timezone.now()).update(notified_at=timezone.now())


class Migration(migrations.Migration):

    dependencies = [('hq', '0017_systemnotice_review_workflow')]

    operations = [
        migrations.AddField(
            model_name='systemnotice', name='notify_in_app',
            field=models.BooleanField(default=True, help_text="Also put this on each customer's notification bell (and send a phone push if they have it on). Untick it for small typo fixes."),
        ),
        migrations.AddField(
            model_name='systemnotice', name='notified_at',
            field=models.DateTimeField(blank=True, null=True, help_text='When the bell notifications were sent (so they are never sent twice).'),
        ),
        migrations.RunPython(mark_existing_as_sent, migrations.RunPython.noop),
    ]
