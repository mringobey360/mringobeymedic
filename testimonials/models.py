"""Client testimonials: admin-authorised invitations -> customer rating popup ->
moderated reviews -> public landing page carousel.

Nothing here is public until the platform superadmin (is_superuser) approves it.
"""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class TestimonialSettings(models.Model):
    """Single-row settings. Disabled by default so no popup appears until HQ enables it."""
    enabled = models.BooleanField(
        default=False,
        help_text='Master switch. When off, no customer sees the rating popup (authorised invitations wait).')
    snooze_days = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(60)],
        help_text='Days before the popup reappears after a customer taps "Later".')
    show_on_landing = models.BooleanField(
        default=True, help_text='Show the approved-reviews carousel on the public landing page.')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Testimonial settings'
        verbose_name_plural = 'Testimonial settings'

    def __str__(self):
        return 'Testimonial settings'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class TestimonialInvitation(models.Model):
    class Status(models.TextChoices):
        AUTHORIZED = 'AUTHORIZED', 'Authorised (waiting for customer)'
        ANSWERED = 'ANSWERED', 'Answered'
        DECLINED = 'DECLINED', 'Declined by customer'
        REVOKED = 'REVOKED', 'Revoked by admin'

    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name='testimonial_invitations')
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                      related_name='+')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.AUTHORIZED, db_index=True)
    authorized_at = models.DateTimeField(default=timezone.now)
    first_shown_at = models.DateTimeField(null=True, blank=True)
    snoozed_until = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-authorized_at']
        constraints = [
            # One live invitation per customer at a time (history rows are unaffected).
            models.UniqueConstraint(fields=['customer'], condition=Q(status='AUTHORIZED'),
                                    name='testimonials_one_open_invite_per_customer'),
        ]

    def __str__(self):
        return f'Invitation #{self.pk} -> {self.customer_id} ({self.status})'


class TestimonialReview(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    invitation = models.OneToOneField(TestimonialInvitation, on_delete=models.CASCADE, related_name='review')
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name='testimonial_reviews')
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.CharField(max_length=600)
    display_name = models.CharField(max_length=80, blank=True)
    business_name = models.CharField(max_length=80, blank=True)
    consent_public_name = models.BooleanField(
        default=False, help_text='Customer agreed to show their name and business publicly.')
    reviewer_role = models.CharField(max_length=40, blank=True, help_text='Role of the reviewer, set by the server.')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    moderated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='+')
    moderated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.rating}★ review #{self.pk} ({self.status})'

    @property
    def public_name(self):
        if self.consent_public_name and self.display_name:
            return self.display_name
        return 'Verified SmartPOS customer'

    @property
    def public_role(self):
        return self.reviewer_role if (self.consent_public_name and self.display_name) else ''

    @property
    def public_photo(self):
        """Profile photo URL, only when the user consented to show their identity."""
        if not (self.consent_public_name and self.display_name):
            return ''
        try:
            photo = self.customer.profile_photo
            return photo.url if photo else ''
        except Exception:
            return ''

    @property
    def public_business(self):
        return self.business_name if (self.consent_public_name and self.business_name) else ''


class TestimonialAuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name='+')
    action = models.CharField(max_length=40, db_index=True)
    target_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+')
    invitation = models.ForeignKey(TestimonialInvitation, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    review = models.ForeignKey(TestimonialReview, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name='+')
    detail = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.action} @ {self.created_at:%Y-%m-%d %H:%M}'
