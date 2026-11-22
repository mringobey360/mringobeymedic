from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from payments.models import PesapalTransaction

class Command(BaseCommand):
    help = "Report unresolved online payments and activation issues without changing customer balances."
    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(minutes=30)
        pending = PesapalTransaction.objects.filter(status__in=[PesapalTransaction.Status.PENDING, PesapalTransaction.Status.SUBMITTED], created_at__lt=cutoff).count()
        paid_unactivated = PesapalTransaction.objects.filter(status=PesapalTransaction.Status.COMPLETED, processed_at__isnull=True).count()
        invalid = PesapalTransaction.objects.filter(status=PesapalTransaction.Status.INVALID, created_at__gte=cutoff).count()
        self.stdout.write(f"Payment reconciliation\nPending >30m: {pending}\nCompleted but not activated: {paid_unactivated}\nRecent invalid/mismatch: {invalid}")
