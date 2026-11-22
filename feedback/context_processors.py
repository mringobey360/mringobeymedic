import time
from datetime import timedelta

from django.utils import timezone

_RECHECK_SECONDS = 600          # re-evaluate at most every 10 minutes per session
_REPEAT_DAYS = 30               # ask again 30 days after the last submission


def feedback_prompt(request):
    """Show a feedback reminder to every authenticated SmartPOS account type
    (owners/managers, assistant managers and employees) if they have not sent
    feedback, or their last submission was over 30 days ago. Only the platform
    System Administrator (is_superuser) uses the admin notification bell instead.
    Cache per user so changing accounts cannot inherit another user's reminder state."""

    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {'FEEDBACK_PROMPT_DUE': False}
    # Platform System Administrator is notified through the in-app notification bell,
    # not interrupted by the customer feedback prompt.
    if getattr(user, 'is_superuser', False):
        return {'FEEDBACK_PROMPT_DUE': False}
    try:
        cache_key = f"spfb_due_user_{user.pk}"
        cached = request.session.get(cache_key)
        now = time.time()
        if cached and now - cached[0] < _RECHECK_SECONDS:
            return {'FEEDBACK_PROMPT_DUE': bool(cached[1])}
        from .models import Feedback
        latest = Feedback.objects.filter(user=user).values_list('created_at', flat=True).first()
        due = latest is None or latest < timezone.now() - timedelta(days=_REPEAT_DAYS)
        request.session[cache_key] = [now, bool(due)]
        return {'FEEDBACK_PROMPT_DUE': bool(due)}
    except Exception:
        return {'FEEDBACK_PROMPT_DUE': False}
