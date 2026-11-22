"""Repair V54.28's first settlement, which charged EVERY legacy meal to today's drawer.

Meals dated before the latest verified cash figure (a physical count, or a manager-typed opening) are
already inside that figure, so charging them again overstated "Staff meals paid from the drawer".
Those rows go back to legacy (never charged); only meals after the boundary stay settled on the day of settlement.
"""
from datetime import timedelta

from django.db import migrations


def repair(apps, schema_editor):
    MealCashPayment = apps.get_model('expenses', 'MealCashPayment')
    CashDay = apps.get_model('expenses', 'CashDay')
    pharmacy_ids = (MealCashPayment.objects.filter(legacy_settled_at__isnull=False)
                    .values_list('pharmacy_id', flat=True).distinct())
    for pid in list(pharmacy_ids):
        boundaries = []
        settled_on = (MealCashPayment.objects.filter(pharmacy_id=pid, legacy_settled_at__isnull=False)
                      .order_by('legacy_settled_at').values_list('legacy_settled_at', flat=True).first())
        today = settled_on.date() if settled_on else None
        counted = (CashDay.objects.filter(pharmacy_id=pid, actual_closing_cash__isnull=False, date__lt=today)
                   .order_by('-date').values_list('date', flat=True).first()) if today else None
        if counted:
            boundaries.append(counted + timedelta(days=1))
        typed = (CashDay.objects.filter(pharmacy_id=pid, opening_source__in=['MANUAL', 'OVERRIDE'], date__lte=today)
                 .order_by('-date').values_list('date', flat=True).first()) if today else None
        if typed:
            boundaries.append(typed)
        if not boundaries:
            continue
        MealCashPayment.objects.filter(
            pharmacy_id=pid, legacy_settled_at__isnull=False, meal_date__lt=max(boundaries),
        ).update(is_legacy=True, legacy_settled_at=None)


class Migration(migrations.Migration):
    dependencies = [
        ('expenses', '0014_settle_legacy_meal_payments'),
    ]
    operations = [
        migrations.RunPython(repair, migrations.RunPython.noop),
    ]
