from django.db import migrations, models
import django.db.models.deletion


def seed_weekly_meal_schedules(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    WeeklyMealSchedule = apps.get_model('accounts', 'WeeklyMealSchedule')
    for employee in User.objects.filter(role='EMPLOYEE'):
        for weekday in range(7):
            breakfast = weekday < 5 or employee.weekend_meal_schedule == 'ALL_THREE'
            lunch = True
            dinner = weekday < 5 or employee.weekend_meal_schedule in ('LUNCH_DINNER', 'ALL_THREE')
            if employee.weekend_meal_schedule == 'LUNCH_ONLY' and weekday >= 5:
                dinner = False
            WeeklyMealSchedule.objects.create(employee=employee, weekday=weekday, breakfast=breakfast, lunch=lunch, dinner=dinner)


class Migration(migrations.Migration):
    dependencies = [('accounts', '0001_initial')]
    operations = [
        migrations.CreateModel(
            name='WeeklyMealSchedule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('weekday', models.PositiveSmallIntegerField(choices=[(0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'), (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday')])),
                ('breakfast', models.BooleanField(default=True)),
                ('lunch', models.BooleanField(default=True)),
                ('dinner', models.BooleanField(default=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='meal_schedules', to='accounts.user')),
            ],
            options={'ordering': ['weekday']},
        ),
        migrations.CreateModel(
            name='MealAdjustment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('breakfast', models.BooleanField(default=False)),
                ('lunch', models.BooleanField(default=False)),
                ('dinner', models.BooleanField(default=False)),
                ('reason', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='created_meal_adjustments', to='accounts.user')),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='meal_adjustments', to='accounts.user')),
            ],
            options={'ordering': ['-date', '-created_at']},
        ),
        migrations.AddConstraint(model_name='weeklymealschedule', constraint=models.UniqueConstraint(fields=('employee','weekday'), name='unique_employee_weekday_meal_schedule')),
        migrations.AddConstraint(model_name='mealadjustment', constraint=models.UniqueConstraint(fields=('employee','date'), name='unique_employee_meal_adjustment_date')),
        migrations.RunPython(seed_weekly_meal_schedules, migrations.RunPython.noop),
    ]
