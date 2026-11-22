from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('pharmacies', '0004_branchaddon_payment_reference'),
        ('expenses', '0009_cashcount_correction_audit'),
    ]

    operations = [
        migrations.CreateModel(
            name='CashClosingSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('revision', models.PositiveIntegerField(default=1)),
                ('opening_cash', models.DecimalField(decimal_places=2, max_digits=14)),
                ('expected_cash', models.DecimalField(decimal_places=2, max_digits=14)),
                ('physical_cash', models.DecimalField(decimal_places=2, max_digits=14)),
                ('variance', models.DecimalField(decimal_places=2, max_digits=14)),
                ('cash_sales', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('cash_refunds', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('cash_expenses', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('note', models.TextField(blank=True)),
                ('closed_at', models.DateTimeField(db_index=True)),
                ('cash_day', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='closing_snapshots', to='expenses.cashday')),
                ('closed_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cash_closing_snapshots', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-closed_at']},
        ),
        migrations.AddConstraint(
            model_name='cashclosingsnapshot',
            constraint=models.UniqueConstraint(fields=('cash_day', 'revision'), name='unique_snapshot_revision_per_day'),
        ),
        migrations.CreateModel(
            name='CashAuditEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('business_date', models.DateField(db_index=True)),
                ('action', models.CharField(choices=[
                    ('DAY_CREATED', 'Cash day created'), ('OPENING_ADJUSTED', 'Opening adjusted'),
                    ('OPENING_RECONCILED', 'Opening reconciled'), ('SPOT_CHECK', 'Spot check'),
                    ('COUNT_CORRECTED', 'Count corrected'), ('DAY_ENDED', 'Day ended'),
                    ('CASH_CLOSED', 'Cash closed'), ('POST_CLOSE_DETECTED', 'Post-close activity detected'),
                    ('REOPENED', 'Cash day reopened')], db_index=True, max_length=24)),
                ('old_value', models.CharField(blank=True, max_length=120)),
                ('new_value', models.CharField(blank=True, max_length=120)),
                ('reason', models.TextField(blank=True)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cash_audit_events', to=settings.AUTH_USER_MODEL)),
                ('cash_day', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='audit_events', to='expenses.cashday')),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cash_audit_events', to='pharmacies.pharmacy')),
            ],
            options={'ordering': ['-created_at', '-pk']},
        ),
        migrations.AddIndex(
            model_name='cashauditevent',
            index=models.Index(fields=['pharmacy', '-created_at'], name='expenses_ca_pharmac_idx'),
        ),
    ]
