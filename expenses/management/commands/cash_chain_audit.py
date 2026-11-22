"""Diagnose (and optionally repair) the cash-day chain.

    python manage.py cash_chain_audit                 # read-only report
    python manage.py cash_chain_audit --fix --user mutech   # audited self-heal

Prints every cash day with the exact reason (if any) that blocks the days after
it, so "Settle the earlier day first" is never a mystery again.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from expenses.models import CashDay
from expenses.services import heal_stale_provisional_closed_days, repair_provisional_opening_chain
from expenses.views import _cash_day_needs_attention
from pharmacies.models import Pharmacy


class Command(BaseCommand):
    help = 'Report cash-day chain blockers; --fix applies the audited self-heal.'

    def add_arguments(self, parser):
        parser.add_argument('--pharmacy', type=int, help='Pharmacy id (default: all)')
        parser.add_argument('--fix', action='store_true', help='Apply audited self-heal')
        parser.add_argument('--user', help='Username recorded as the actor for --fix')

    def handle(self, *args, **opts):
        pharmacies = Pharmacy.objects.all()
        if opts['pharmacy']:
            pharmacies = pharmacies.filter(pk=opts['pharmacy'])
        actor = None
        if opts['fix']:
            if not opts['user']:
                raise CommandError('--fix requires --user <username> so the audit trail names who ran it.')
            try:
                actor = get_user_model().objects.get(username=opts['user'])
            except get_user_model().DoesNotExist:
                raise CommandError(f"User '{opts['user']}' not found.")

        for ph in pharmacies:
            self.stdout.write(self.style.MIGRATE_HEADING(f'\n{ph} (id={ph.pk})'))
            if actor:
                repaired = repair_provisional_opening_chain(ph, actor)
                healed = heal_stale_provisional_closed_days(ph, actor)
                self.stdout.write(f'  fix applied: {repaired} opening(s) rebased, {healed} stale flag(s) cleared')
            self.stdout.write(f"  {'date':<12}{'status':<11}{'opening':>12}  {'source':<14}{'prov':<6}{'post-close':<11}{'closing':>12}  blocks-later-days")
            for d in CashDay.objects.filter(pharmacy=ph).order_by('date'):
                reason = _cash_day_needs_attention(d) or '-'
                line = (f"  {d.date!s:<12}{d.status:<11}{d.opening_cash:>12,.0f}  {d.opening_source:<14}"
                        f"{'YES' if d.opening_is_provisional else 'no':<6}"
                        f"{'YES' if d.post_close_cash_activity else 'no':<11}"
                        f"{(f'{d.actual_closing_cash:,.0f}' if d.actual_closing_cash is not None else '-'):>12}  {reason}")
                self.stdout.write(self.style.WARNING(line) if reason != '-' else line)
