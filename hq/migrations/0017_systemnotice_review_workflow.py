import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F

_CHOICES = [('DRAFT', 'Draft (not visible to customers)'), ('PUBLISHED', 'Published')]


def backfill_published(apps, schema_editor):
    """Notices created before the review workflow were already visible: keep them published."""
    SystemNotice = apps.get_model('hq', 'SystemNotice')
    SystemNotice.objects.filter(publish_state='PUBLISHED', published_at__isnull=True).update(published_at=F('updated_at'))


class Migration(migrations.Migration):
    """Adds the draft -> review -> publish workflow to system notices.

    Existing rows are backfilled as PUBLISHED (so live banners do not disappear); every
    notice created from now on starts as a DRAFT.
    """

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('hq', '0016_systemnotice'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemnotice',
            name='publish_state',
            field=models.CharField(choices=_CHOICES, default='PUBLISHED', max_length=10),
        ),
        migrations.AlterField(
            model_name='systemnotice',
            name='publish_state',
            field=models.CharField(choices=_CHOICES, default='DRAFT', max_length=10),
        ),
        migrations.AddField(
            model_name='systemnotice',
            name='published_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='systemnotice',
            name='published_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(backfill_published, migrations.RunPython.noop),
    ]
