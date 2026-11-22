from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('expenses', '0021_closing_snapshot_debt_collected'),
    ]

    operations = [
        migrations.AddField(model_name='cashday', name='actual_mobile_received',
                            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
        migrations.AddField(model_name='cashday', name='mobile_checked_at',
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='cashday', name='mobile_check_note',
                            field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name='cashday', name='mobile_checked_by',
                            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                    related_name='mobile_money_checks', to=settings.AUTH_USER_MODEL)),
    ]
