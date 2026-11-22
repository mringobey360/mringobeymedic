from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('accounts', '0002_meal_policy_overrides')]
    operations = [
        migrations.CreateModel(
            name='MealTimingSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('breakfast_cutoff', models.TimeField(default='10:00')),
                ('lunch_cutoff', models.TimeField(default='15:00')),
                ('dinner_cutoff', models.TimeField(default='21:00')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('pharmacy', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='meal_timing', to='pharmacies.pharmacy')),
            ],
        ),
        migrations.CreateModel(
            name='MealWorkday',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('opened_at', models.DateTimeField()),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('opened_manually', models.BooleanField(default=True)),
                ('note', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='meal_workdays', to='accounts.user')),
            ],
            options={'ordering': ['-date']},
        ),
        migrations.CreateModel(
            name='MealLedger',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('meal_type', models.CharField(choices=[('BREAKFAST','Breakfast / Tea'),('LUNCH','Lunch'),('DINNER','Dinner')], max_length=12)),
                ('amount', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('cutoff_at', models.DateTimeField()),
                ('status', models.CharField(choices=[('PENDING','Pending'),('EARNED','Earned / Funded'),('DEBT','Meal Debt'),('WAIVED','Waived')], default='PENDING', max_length=10)),
                ('source', models.CharField(choices=[('WEEKLY','Weekly policy'),('OVERRIDE','Date override')], default='WEEKLY', max_length=10)),
                ('reason', models.CharField(blank=True, max_length=255)),
                ('earned_at', models.DateTimeField(blank=True, null=True)),
                ('debt_at', models.DateTimeField(blank=True, null=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='meal_ledger', to='accounts.user')),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='meal_ledger', to='pharmacies.pharmacy')),
            ],
            options={'ordering': ['-date','cutoff_at']},
        ),
        migrations.AddConstraint(model_name='mealworkday', constraint=models.UniqueConstraint(fields=('employee','date'), name='unique_employee_meal_workday')),
        migrations.AddConstraint(model_name='mealledger', constraint=models.UniqueConstraint(fields=('employee','date','meal_type'), name='unique_employee_meal_ledger_day_type')),
        migrations.AddIndex(model_name='mealworkday', index=models.Index(fields=['employee','-date'], name='accounts_mea_employee_9e5a75_idx')),
        migrations.AddIndex(model_name='mealledger', index=models.Index(fields=['pharmacy','-date','status'], name='accounts_mea_pharmacy_5f5e0f_idx')),
        migrations.AddIndex(model_name='mealledger', index=models.Index(fields=['employee','-date','status'], name='accounts_mea_employee_6a2f4b_idx')),
    ]
