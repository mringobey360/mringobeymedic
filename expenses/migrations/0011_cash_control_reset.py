from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

class Migration(migrations.Migration):
    dependencies = [
        ('expenses', '0010_cash_audit_and_snapshots'),
        ('pharmacies', '0013_pharmacy_business_type'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='CashControlReset',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start_date', models.DateField(db_index=True)),
                ('opening_cash', models.DecimalField(decimal_places=2, max_digits=14)),
                ('reason', models.TextField()),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('archived_cash_days', models.PositiveIntegerField(default=0)),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cash_control_resets', to='pharmacies.pharmacy')),
                ('reset_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cash_control_resets', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at', '-pk']},
        ),
    ]
