from django.db import migrations


def backfill_missing_weekly_policies(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    WeeklyMealSchedule = apps.get_model('accounts', 'WeeklyMealSchedule')

    for employee in User.objects.filter(role='EMPLOYEE').iterator():
        existing = set(
            WeeklyMealSchedule.objects.filter(employee_id=employee.pk)
            .values_list('weekday', flat=True)
        )
        for weekday in range(7):
            if weekday in existing:
                continue
            legacy = employee.weekend_meal_schedule
            if weekday < 5:
                breakfast, lunch, dinner = True, True, True
            elif legacy == 'ALL_THREE':
                breakfast, lunch, dinner = True, True, True
            elif legacy == 'LUNCH_DINNER':
                breakfast, lunch, dinner = False, True, True
            else:
                breakfast, lunch, dinner = False, True, False
            WeeklyMealSchedule.objects.create(
                employee_id=employee.pk,
                weekday=weekday,
                breakfast=breakfast,
                lunch=lunch,
                dinner=dinner,
            )


class Migration(migrations.Migration):
    dependencies = [('accounts', '0011_allow_multiple_assistants_per_branch')]

    operations = [
        migrations.RunPython(backfill_missing_weekly_policies, migrations.RunPython.noop),
    ]
