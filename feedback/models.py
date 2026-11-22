from django.conf import settings
from django.db import models
from django.utils import timezone
import uuid

class Feedback(models.Model):
    class Category(models.TextChoices):
        BUG = 'BUG', 'Bug or error'
        IDEA = 'IDEA', 'Feature idea'
        USABILITY = 'USABILITY', 'Ease of use'
        SPEED = 'SPEED', 'System speed'
        PRAISE = 'PRAISE', 'Compliment'
    class Status(models.TextChoices):
        NEW = 'NEW', 'New'
        REVIEWING = 'REVIEWING', 'Under review'
        PLANNED = 'PLANNED', 'Planned'
        RESOLVED = 'RESOLVED', 'Resolved'
        DECLINED = 'DECLINED', 'Not planned'
    RATING_LABELS = [(1,'Very poor'),(2,'Poor'),(3,'Average'),(4,'Good'),(5,'Excellent')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='smartpos_feedback')
    branch = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.SET_NULL, null=True, blank=True, related_name='smartpos_feedback')
    rating = models.PositiveSmallIntegerField(choices=RATING_LABELS)
    category = models.CharField(max_length=16, choices=Category.choices, default=Category.USABILITY)
    comment = models.CharField(max_length=500, blank=True)
    page_url = models.CharField(max_length=500, blank=True)
    page_section = models.CharField(max_length=80, blank=True)
    app_version = models.CharField(max_length=32, default='V52')
    user_role = models.CharField(max_length=40, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-created_at']
    def __str__(self):
        return f'{self.get_rating_display()} feedback from {self.user} ({self.created_at:%Y-%m-%d})'

class FeedbackReply(models.Model):
    feedback = models.ForeignKey(Feedback, on_delete=models.CASCADE, related_name='replies')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='smartpos_feedback_replies')
    message = models.TextField(max_length=3000)
    is_internal = models.BooleanField(default=False, help_text='Internal staff note; not visible to customer.')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    class Meta:
        ordering = ['created_at']
    def __str__(self):
        return f'Reply to feedback #{self.feedback_id} by {self.author}'

class SupportTicket(models.Model):
    class Category(models.TextChoices):
        TECHNICAL = 'TECHNICAL', 'Technical problem'
        ACCOUNT = 'ACCOUNT', 'Account or login'
        SALES = 'SALES', 'Sales / POS'
        INVENTORY = 'INVENTORY', 'Inventory / stock'
        BILLING = 'BILLING', 'Billing or subscription'
        OTHER = 'OTHER', 'Other'
    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        IN_PROGRESS = 'IN_PROGRESS', 'In progress'
        WAITING_CUSTOMER = 'WAITING_CUSTOMER', 'Waiting for customer'
        RESOLVED = 'RESOLVED', 'Resolved'
        CLOSED = 'CLOSED', 'Closed'
    class Priority(models.TextChoices):
        LOW = 'LOW', 'Low'
        NORMAL = 'NORMAL', 'Normal'
        HIGH = 'HIGH', 'High'
        URGENT = 'URGENT', 'Urgent'
    ticket_number = models.CharField(max_length=20, unique=True, blank=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='smartpos_support_tickets')
    branch = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.SET_NULL, null=True, blank=True, related_name='smartpos_support_tickets')
    subject = models.CharField(max_length=160)
    category = models.CharField(max_length=16, choices=Category.choices, default=Category.TECHNICAL)
    description = models.TextField(max_length=5000)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.OPEN, db_index=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL, db_index=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='assigned_smartpos_tickets')
    page_url = models.CharField(max_length=500, blank=True)
    app_version = models.CharField(max_length=32, default='V52')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ['-updated_at', '-created_at']
    def save(self, *args, **kwargs):
        if not self.ticket_number and self.pk is None:
            # Use a unique temporary value for the INSERT; an empty string with a
            # unique constraint would allow only one ticket in PostgreSQL.
            self.ticket_number = f'TMP-{uuid.uuid4().hex}'
            super().save(*args, **kwargs)
            self.ticket_number = f'SP-{timezone.now():%y%m}-{self.pk:06d}'
            return super().save(update_fields=['ticket_number'])
        return super().save(*args, **kwargs)
    def __str__(self):
        return self.ticket_number or f'Support ticket #{self.pk}'

class SupportMessage(models.Model):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name='messages')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='smartpos_support_messages')
    body = models.TextField(max_length=5000)
    is_internal = models.BooleanField(default=False, help_text='Staff-only note, never shown to customer.')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    class Meta:
        ordering = ['created_at']
    def __str__(self):
        return f'Message on {self.ticket} by {self.author}'
