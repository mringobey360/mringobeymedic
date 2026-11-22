from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('expenses', '0003_professional_auto_close'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='cashday',
            name='physical_counted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cashday',
            name='physical_counted_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='verified_cash_counts', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='cashday',
            name='variance_reason',
            field=models.TextField(blank=True),
        ),
    ]
