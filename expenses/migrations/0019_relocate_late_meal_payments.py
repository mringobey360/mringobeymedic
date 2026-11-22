"""V54.33: move yesterday's/today's meal drawer payments recorded on a later day onto the meal's own day
(unless that day is CLOSED), so each day's expected cash and the next opening are right. Idempotent; skipped on
error (`python manage.py settle_legacy_meals` does the same)."""
from django.db import migrations


def relocate(apps, schema_editor):
    try:
        from expenses.meal_cash import relocate_late_meal_payments
        relocate_late_meal_payments()
    except Exception as exc:
        print(f'  [expenses.0019] skipped ({exc}); run: python manage.py settle_legacy_meals')


class Migration(migrations.Migration):
    dependencies = [('expenses', '0018_settle_legacy_meals_on_meal_day')]
    operations = [migrations.RunPython(relocate, migrations.RunPython.noop)]
