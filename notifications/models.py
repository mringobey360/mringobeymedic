from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    """
    A single in-app notification for one recipient. This is the generic
    building block other features are meant to create these through — the
    bell shown in base.html reads only from this table, so anything that
    calls notifications.utils.notify(...) automatically shows up there,
    gets counted, and appears in the filterable history page — no per-
    feature UI work needed later (announcements, stock-mistake alerts,
    chat messages, debt reminders, etc. all just call notify()).
    """

    class Category(models.TextChoices):
        ANNOUNCEMENT = 'ANNOUNCEMENT', 'Announcement'
        STOCK_ALERT = 'STOCK_ALERT', 'Stock alert'
        DEBT = 'DEBT', 'Debt'
        SALES = 'SALES', 'Sales'
        CHAT = 'CHAT', 'Chat message'
        SYSTEM = 'SYSTEM', 'System'
        OTHER = 'OTHER', 'Other'

    class Level(models.TextChoices):
        INFO = 'INFO', 'Information'
        SUCCESS = 'SUCCESS', 'Success'
        WARNING = 'WARNING', 'Warning'
        DANGER = 'DANGER', 'Danger'

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications'
    )
    pharmacy = models.ForeignKey(
        'pharmacies.Pharmacy', null=True, blank=True, on_delete=models.CASCADE, related_name='notifications'
    )
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.INFO)
    title = models.CharField(max_length=150)
    message = models.TextField(blank=True)
    link_url = models.CharField(max_length=300, blank=True, help_text="Optional in-app link to open when clicked.")
    # Optional call-to-action attached by the sender (announcements). Separate from
    # ``link_url``, which is the in-app page opened when the bell item is clicked.
    action_url = models.CharField(max_length=500, blank=True, default='')
    action_label = models.CharField(max_length=40, blank=True, default='')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)
    # Automation/push delivery metadata. A dedupe key makes recurring alerts
    # idempotent so a Beat retry cannot spam managers.
    dedupe_key = models.CharField(max_length=220, blank=True, default='')
    push_sent_at = models.DateTimeField(null=True, blank=True)
    push_attempts = models.PositiveSmallIntegerField(default=0)
    push_last_error = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read']),
            models.Index(fields=['push_sent_at', 'push_attempts']),
            models.Index(fields=['dedupe_key']),
        ]

    def __str__(self):
        return f"{self.title} -> {self.recipient}"

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])
