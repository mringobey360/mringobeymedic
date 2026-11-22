from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0015_professional_attendance_display'),
        ('pharmacies', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PayrollRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('period_start', models.DateField()),
                ('period_end', models.DateField()),
                ('status', models.CharField(choices=[('DRAFT', 'Draft'), ('APPROVED', 'Approved'), ('PAID', 'Paid')], default='DRAFT', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('paid_at', models.DateTimeField(blank=True, null=True)),
                ('note', models.TextField(blank=True)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='approved_payroll_runs', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='created_payroll_runs', to=settings.AUTH_USER_MODEL)),
                ('paid_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='paid_payroll_runs', to=settings.AUTH_USER_MODEL)),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payroll_runs', to='pharmacies.pharmacy')),
            ],
            options={'ordering': ['-period_start', '-created_at']},
        ),
        migrations.CreateModel(
            name='PayrollItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('basic_salary', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('earned_meals', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('other_allowances', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('tax_deduction', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('other_deductions', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('working_days', models.PositiveIntegerField(default=0)),
                ('attendance_days', models.PositiveIntegerField(default=0)),
                ('note', models.TextField(blank=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payroll_items', to=settings.AUTH_USER_MODEL)),
                ('payroll', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='accounts.payrollrun')),
            ],
            options={'ordering': ['employee__first_name', 'employee__last_name', 'employee__username']},
        ),
        migrations.AddConstraint(
            model_name='payrollrun',
            constraint=models.UniqueConstraint(fields=('pharmacy', 'period_start', 'period_end'), name='unique_payroll_period_branch'),
        ),
        migrations.AddConstraint(
            model_name='payrollitem',
            constraint=models.UniqueConstraint(fields=('payroll', 'employee'), name='unique_payroll_employee'),
        ),
    ]
