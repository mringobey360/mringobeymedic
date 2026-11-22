from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

class Migration(migrations.Migration):
    dependencies = [
        ('expenses', '0005_professional_intraday_cash_counts'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='CashDayReopenAudit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('previous_opening_cash', models.DecimalField(decimal_places=2, max_digits=14)),
                ('previous_closing_cash', models.DecimalField(decimal_places=2, max_digits=14)),
                ('previous_expected_cash', models.DecimalField(decimal_places=2, max_digits=14)),
                ('previous_variance', models.DecimalField(decimal_places=2, max_digits=14)),
                ('previous_closed_at', models.DateTimeField(blank=True, null=True)),
                ('reason', models.TextField()),
                ('reopened_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('cash_day', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='reopen_audits', to='expenses.cashday')),
                ('previous_closed_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cash_day_reopen_actions', to=settings.AUTH_USER_MODEL)),
                ('reopened_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cash_day_reopened_records', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-reopened_at']},
        ),
    ]
