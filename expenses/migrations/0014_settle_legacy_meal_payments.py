from django.db import migrations, models
from django.utils import timezone


def settle_legacy(apps, schema_editor):
    """Charge pre-V50.42 EARNED meals to the drawer once, today (automatic, idempotent)."""
    MealCashPayment = apps.get_model('expenses', 'MealCashPayment')
    now = timezone.now()
    MealCashPayment.objects.filter(
        is_legacy=True, reversed_at__isnull=True, meal__status='EARNED',
    ).update(is_legacy=False, paid_at=now, legacy_settled_at=now)


class Migration(migrations.Migration):
    dependencies = [
        ('expenses', '0013_meal_cash_payment'),
    ]

    operations = [
        migrations.AddField(
            model_name='mealcashpayment', name='legacy_settled_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(settle_legacy, migrations.RunPython.noop),
    ]
