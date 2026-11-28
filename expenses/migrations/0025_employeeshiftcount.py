from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('pharmacies', '0004_branchaddon_payment_reference'),
        ('expenses', '0024_expense_auto_record'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmployeeShiftCount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(db_index=True)),
                ('counted_cash', models.DecimalField(decimal_places=2, max_digits=14)),
                ('opening_cash', models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ('cash_sales', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('cash_refunds', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('meal_earned', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('expenses_paid', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('system_amount', models.DecimalField(decimal_places=2, max_digits=14)),
                ('difference', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('status', models.CharField(choices=[('PENDING', 'Awaiting manager'), ('ACCEPTED', 'Accepted'), ('REJECTED', 'Recount requested')], db_index=True, default='PENDING', max_length=10)),
                ('reason', models.TextField(blank=True)),
                ('reason_at', models.DateTimeField(blank=True, null=True)),
                ('counted_at', models.DateTimeField(auto_now_add=True)),
                ('auto_accepted', models.BooleanField(default=False)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decision_note', models.CharField(blank=True, max_length=255)),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='decided_shift_counts', to=settings.AUTH_USER_MODEL)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='shift_counts', to=settings.AUTH_USER_MODEL)),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='shift_counts', to='pharmacies.pharmacy')),
            ],
            options={'ordering': ['-date', '-counted_at']},
        ),
        migrations.AddIndex(
            model_name='employeeshiftcount',
            index=models.Index(fields=['pharmacy', 'date'], name='shiftcount_pharm_date_idx'),
        ),
        migrations.AddConstraint(
            model_name='employeeshiftcount',
            constraint=models.UniqueConstraint(condition=models.Q(status__in=['PENDING', 'ACCEPTED']), fields=('employee', 'date'), name='one_live_shift_count_per_employee_day'),
        ),
    ]
