from django.db.models import Avg, Count

from .models import TestimonialAuditLog, TestimonialReview


def log(actor, action, *, target_user=None, invitation=None, review=None, detail=''):
    TestimonialAuditLog.objects.create(actor=actor, action=action, target_user=target_user,
                                       invitation=invitation, review=review, detail=detail[:240])


def public_reviews(limit=12):
    """Approved reviews only, newest first. Safe to render on the public landing page."""
    return list(TestimonialReview.objects.filter(status=TestimonialReview.Status.APPROVED)
                .select_related('customer').order_by('-created_at')[:limit])


def public_stats():
    agg = TestimonialReview.objects.filter(status=TestimonialReview.Status.APPROVED).aggregate(
        avg=Avg('rating'), n=Count('id'))
    avg = round(float(agg['avg']), 1) if agg['avg'] else 0
    return {'average': avg, 'rounded': int(round(avg)) if avg else 0, 'count': agg['n'] or 0}
