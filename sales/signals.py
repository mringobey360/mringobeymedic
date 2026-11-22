from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Sale


@receiver(post_save, sender=Sale)
def reconcile_meals_after_sale(sender, instance, **kwargs):
    """Late-entered/offline sales can fund meals for their business date."""
    try:
        from accounts.meal_services import reconcile_meal_funding_for_date
        reconcile_meal_funding_for_date(instance.pharmacy, instance.business_date)
    except Exception:
        # Meal reconciliation must never block or fail a valid sale.
        # The scheduled meal accrual command will reconcile again.
        pass
