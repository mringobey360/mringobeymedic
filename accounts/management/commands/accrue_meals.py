from django.core.management.base import BaseCommand
from accounts.meal_services import accrue_due_meals

class Command(BaseCommand):
    help = 'Accrue due employee meals and finalize eligible meal debts.'
    def handle(self, *args, **options):
        created = accrue_due_meals()
        self.stdout.write(self.style.SUCCESS(f'Meal accrual completed. New ledger rows: {created}.'))
        # V54.31 safety net: legacy EARNED meals that still belong in the drawer are charged automatically (idempotent).
        try:
            from expenses.meal_cash import (
                relocate_late_meal_payments, repair_over_settled_meals, settle_legacy_meal_payments,
            )
            relocate_late_meal_payments()
            repair_over_settled_meals()
            n, total = settle_legacy_meal_payments()
            if n:
                self.stdout.write(self.style.SUCCESS(f'Legacy meals charged to the drawer: {n} (TZS {total:,.0f}).'))
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f'Legacy meal settlement skipped: {exc}'))
