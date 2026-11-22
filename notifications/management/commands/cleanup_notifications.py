from django.core.management.base import BaseCommand
from notifications.utils import cleanup_expired_notifications


class Command(BaseCommand):
    help = "Compatibility command. Notification history is durable; no automatic deletion is performed."

    def handle(self, *args, **options):
        deleted = cleanup_expired_notifications()
        self.stdout.write(
            self.style.SUCCESS(
                f"Notification cleanup is disabled for durable history. Deleted {deleted} notifications."
            )
        )
