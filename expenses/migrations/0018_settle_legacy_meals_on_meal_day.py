"""V54.32: legacy meals that belong in the drawer are charged on their OWN day (if not closed), moving any
settlement made on a later day, and today's provisional opening follows. Idempotent; skipped on error
(`python manage.py settle_legacy_meals` does the same).
"""
from django.db import migrations


def settle(apps, schema_editor):
    try:
        from expenses.meal_cash import repair_over_settled_meals, settle_legacy_meal_payments
        repair_over_settled_meals()
        settle_legacy_meal_payments()
    except Exception as exc:
        print(f'  [expenses.0018] skipped ({exc}); run: python manage.py settle_legacy_meals')


class Migration(migrations.Migration):
    dependencies = [
        ('expenses', '0017_settle_legacy_meals_automatically'),
    ]
    operations = [
        migrations.RunPython(settle, migrations.RunPython.noop),
    ]
