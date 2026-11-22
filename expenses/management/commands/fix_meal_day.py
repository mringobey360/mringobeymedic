import datetime

from django.core.management.base import BaseCommand, CommandError

from expenses.ledger import CashLedgerService
from expenses.meal_cash import charge_closed_day_meals_on, ensure_meal_day_payments
from expenses.models import CashDay


class Command(BaseCommand):
    help = ("Make one day's drawer exact: every EARNED meal of --date is deducted from the drawer on that day "
            "(profit untouched). Example: python manage.py fix_meal_day --pharmacy 1 --date 2026-10-11 --dry-run")

    def add_arguments(self, parser):
        parser.add_argument('--pharmacy', type=int, required=True)
        parser.add_argument('--date', required=True, help='YYYY-MM-DD')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--charge-on', metavar='YYYY-MM-DD',
                            help='For a CLOSED --date: deduct its undeducted earned meals on this later, not-closed '
                                 'day instead (the closed day itself is never rewritten).')

    def handle(self, *args, **opts):
        charge_date = None
        try:
            d = datetime.date.fromisoformat(opts['date'])
            if opts['charge_on']:
                charge_date = datetime.date.fromisoformat(opts['charge_on'])
                changes, total, manual = charge_closed_day_meals_on(opts['pharmacy'], d, charge_date, dry_run=opts['dry_run'])
                self.stdout.write(self.style.WARNING(
                    f'--charge-on: {d} stays untouched; these meals leave the drawer of {charge_date}. '
                    'Use it only if the cash for these meals physically left the drawer AFTER the count of that day.'))
            else:
                changes, total, manual = ensure_meal_day_payments(opts['pharmacy'], d, dry_run=opts['dry_run'])
        except ValueError as exc:
            raise CommandError(str(exc))
        for meal, what in changes:
            self.stdout.write(f'  meal {meal.pk} {meal.get_meal_type_display()} {meal.amount:,.0f}: {what}')
        self.stdout.write(self.style.SUCCESS(
            f"{'Would fix' if opts['dry_run'] else 'Fixed'} {len(changes)} item(s). Meals of {d} handled: TZS {total:,.0f}."))
        if manual:
            self.stdout.write(self.style.WARNING(
                f'WARNING: manual "Staff Meals" expenses of TZS {manual:,.0f} also exist on {d}; they deduct the drawer too. '
                'Delete them if they are the same meals, or the drawer is deducted twice.'))
        day = CashDay.objects.filter(pharmacy_id=opts['pharmacy'], date=(charge_date or d)).first()
        if day and not opts['dry_run']:
            self.stdout.write(self.style.SUCCESS(f'Expected cash of {day.date}: TZS {CashLedgerService.expected_cash(day):,.0f}'))
