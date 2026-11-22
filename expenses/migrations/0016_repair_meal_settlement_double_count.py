"""V54.30: give back legacy-meal settlements that charged the drawer for cash already accounted for.

Rows go back to legacy (never charged) when the meal is dated before a verified drawer figure, or when the
manager already deducted it as a manual 'Staff Meals' expense. Profit is untouched. Idempotent. If anything
goes wrong the migration is skipped and `python manage.py settle_legacy_meals` does the same repair.
"""
from django.db import migrations


def repair(apps, schema_editor):
    try:
        from expenses.meal_cash import repair_over_settled_meals
        repair_over_settled_meals()
    except Exception as exc:  # never block a deploy for a data repair
        print(f'  [expenses.0016] skipped ({exc}); run: python manage.py settle_legacy_meals')


class Migration(migrations.Migration):
    dependencies = [
        ('expenses', '0015_repair_legacy_meal_settlement'),
    ]
    operations = [
        migrations.RunPython(repair, migrations.RunPython.noop),
    ]
