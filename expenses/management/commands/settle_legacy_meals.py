from django.core.management.base import BaseCommand

from expenses.meal_cash import (
    legacy_meal_plan, relocate_late_meal_payments, repair_over_settled_meals, settle_legacy_meal_payments,
)

REASONS = {
    'verified': 'NOT charged - already inside a verified cash count / typed opening',
    'expense': 'NOT charged - already deducted as a manual Staff Meals expense',
    'charge': 'CHARGED to drawer',
}


class Command(BaseCommand):
    help = ("Make staff-meal drawer deductions correct: gives back any over-settled legacy meal, then charges only "
            "the legacy meals whose cash is not already accounted for (idempotent).")

    def add_arguments(self, parser):
        parser.add_argument('--pharmacy', type=int, help='Only this branch (pharmacy id).')
        parser.add_argument('--audit', action='store_true', help='List every legacy meal with its date, amount and treatment.')
        parser.add_argument('--dry-run', action='store_true', help='Show what would change without changing anything.')

    def handle(self, *args, **opts):
        from expenses.models import MealCashPayment
        pid = opts.get('pharmacy')
        if opts['audit']:
            qs = MealCashPayment.objects.filter(reversed_at__isnull=True, meal__status='EARNED')
            qs = qs.filter(is_legacy=True) | qs.filter(legacy_settled_at__isnull=False)
            if pid:
                qs = qs.filter(pharmacy_id=pid)
            for branch in sorted(set(qs.values_list('pharmacy_id', flat=True))):
                charged = 0
                self.stdout.write(f'Branch {branch}:')
                for x in legacy_meal_plan(branch):
                    p = x['payment']
                    state = 'settled' if p.legacy_settled_at else 'unsettled'
                    self.stdout.write(f'  {p.meal_date}  {p.amount:>10}  [{state}] {REASONS[x["reason"]]}')
                    if x['charge']:
                        charged += p.amount
                self.stdout.write(self.style.SUCCESS(f'  => legacy meals that belong in the drawer: TZS {charged:,.0f}'))
            return
        n1, t1 = repair_over_settled_meals(pharmacy_id=pid, dry_run=opts['dry_run'])
        n2, t2 = settle_legacy_meal_payments(pharmacy_id=pid, dry_run=opts['dry_run'])
        moved = relocate_late_meal_payments(pharmacy_id=pid, dry_run=opts['dry_run'])
        for pay, target in moved:
            self.stdout.write(f'  meal {pay.meal_id} ({pay.amount:,.0f}): deduction {pay.paid_at:%Y-%m-%d %H:%M} -> {target:%Y-%m-%d %H:%M}')
        self.stdout.write(self.style.SUCCESS(
            f"{'Would move' if opts['dry_run'] else 'Moved'} {len(moved)} meal payment(s) onto their own day, "
            f"TZS {sum(p.amount for p, _ in moved):,.0f}."))
        verb = ('Would give back', 'Would settle') if opts['dry_run'] else ('Gave back', 'Settled')
        self.stdout.write(self.style.SUCCESS(f'{verb[0]} {n1} over-settled meal(s), TZS {t1:,.0f}.'))
        self.stdout.write(self.style.SUCCESS(f'{verb[1]} {n2} legacy meal(s), TZS {t2:,.0f}.'))
