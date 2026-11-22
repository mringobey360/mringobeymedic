from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('expenses', '0004_cashday_physical_count_audit'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CashCount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('counted_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('physical_cash', models.DecimalField(decimal_places=2, max_digits=14)),
                ('expected_cash', models.DecimalField(decimal_places=2, max_digits=14)),
                ('variance', models.DecimalField(decimal_places=2, max_digits=14)),
                ('note', models.TextField(blank=True)),
                ('confirmed', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('cash_day', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='physical_counts', to='expenses.cashday')),
                ('counted_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cash_count_records', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-counted_at'],
                'indexes': [models.Index(fields=['cash_day', '-counted_at'], name='expenses_ca_cash_da_6b7f35_idx')],
            },
        ),
    ]
