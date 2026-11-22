"""Remove stored customer phone numbers that are no longer needed.

    python manage.py scrub_payment_pii --dry-run     # see what would change
    python manage.py scrub_payment_pii               # do it (default: payments older than 30 days)
    python manage.py scrub_payment_pii --days 7

What it does
  1. Masks phone numbers inside every stored gateway payload (PaymentEvent.payload) and
     raw response (PesapalTransaction.raw_response). These are diagnostics only.
  2. For payments that are FINISHED (completed + activated, failed, cancelled, expired,
     reversed) and older than --days, replaces payer_phone with a masked value, so a
     database / backup / admin leak does not hand out a list of paying customers' numbers.
Safe to run repeatedly (e.g. weekly from a scheduled job).
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from payments.models import PaymentEvent, PesapalTransaction
from payments.privacy import mask_phone, redact_payload, redact_text


class Command(BaseCommand):
    help = 'Mask stored customer phone numbers in payment payloads and old finished payments.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30, help='Mask payer_phone on finished payments older than this (default 30).')
        parser.add_argument('--dry-run', action='store_true', help='Only report; change nothing.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        cutoff = timezone.now() - timedelta(days=max(0, opts['days']))
        S = PesapalTransaction.Status

        events_changed = 0
        for ev in PaymentEvent.objects.exclude(payload={}).iterator(chunk_size=500):
            clean = redact_payload(ev.payload)
            if clean != ev.payload:
                events_changed += 1
                if not dry:
                    PaymentEvent.objects.filter(pk=ev.pk).update(payload=clean)

        raw_changed = 0
        for txn in PesapalTransaction.objects.exclude(raw_response='').only('pk', 'raw_response').iterator(chunk_size=500):
            clean = redact_text(txn.raw_response)
            if clean != txn.raw_response:
                raw_changed += 1
                if not dry:
                    PesapalTransaction.objects.filter(pk=txn.pk).update(raw_response=clean)

        finished = PesapalTransaction.objects.filter(created_at__lt=cutoff).exclude(payer_phone='').filter(
            status__in=[S.FAILED, S.CANCELLED, S.INVALID, S.REVERSED]
        ) | PesapalTransaction.objects.filter(created_at__lt=cutoff, status=S.COMPLETED, processed_at__isnull=False).exclude(payer_phone='')
        phones_changed = 0
        for txn in finished.only('pk', 'payer_phone').iterator(chunk_size=500):
            masked = mask_phone(txn.payer_phone)
            if masked and masked != txn.payer_phone:
                phones_changed += 1
                if not dry:
                    PesapalTransaction.objects.filter(pk=txn.pk).update(payer_phone=masked)

        verb = 'Would change' if dry else 'Changed'
        self.stdout.write(self.style.SUCCESS(
            f'{verb}: {events_changed} event payload(s), {raw_changed} raw response(s), {phones_changed} old payer phone(s).'
        ))
