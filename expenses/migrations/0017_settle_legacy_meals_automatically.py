"""V54.31: after 0016 gave back wrong settlements, charge the legacy meals that really belong in the drawer.

Only meals whose cash is not already accounted for are charged (see expenses.meal_cash.legacy_meal_plan),
once, on the day this runs. Profit is untouched. Idempotent. On any error it is skipped and
`python manage.py settle_legacy_meals` does the same.
"""
from django.db import migrations


def settle(apps, schema_editor):
    try:
        from expenses.meal_cash import repair_over_settled_meals, settle_legacy_meal_payments
        repair_over_settled_meals()
        settle_legacy_meal_payments()
    except Exception as exc:
        print(f'  [expenses.0017] skipped ({exc}); run: python manage.py settle_legacy_meals')


class Migration(migrations.Migration):
    dependencies = [
        ('expenses', '0016_repair_meal_settlement_double_count'),
    ]
    operations = [
        migrations.RunPython(settle, migrations.RunPython.noop),
    ]
