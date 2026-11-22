"""First-week checklist shown on the Shop Owner dashboard (new accounts only).

Everything is derived from real data, so a step ticks itself the moment the owner does it.
Nothing is stored, nothing can get out of sync, and it disappears once every step is done.
"""
from django.urls import reverse
from django.utils import timezone


def onboarding_checklist(user, pharmacy):
    """Return None (hide) or a dict: steps, done, total, percent, trial info."""
    if pharmacy is None or not user.is_admin_role():
        return None
    from accounts.models import User
    from hq.models import Subscription
    from inventory.models import Product
    from sales.models import Sale

    try:
        sub = user.subscription
    except Subscription.DoesNotExist:
        sub = None
    # Only accounts opened recently, or still on the trial, get the checklist.
    age_days = (timezone.now() - user.date_joined).days
    on_trial = bool(sub and sub.is_trial)
    if not on_trial and age_days > 30:
        return None

    has_product = Product.objects.filter(pharmacy=pharmacy, is_demo=False, is_active=True).exists()
    has_sale = Sale.objects.filter(pharmacy=pharmacy, items__product__is_demo=False).exists()
    has_staff = User.objects.filter(pharmacy=pharmacy, role=User.Role.EMPLOYEE).exists() or \
        user.managed_pharmacies.filter(employees__isnull=False).exists()

    steps = [
        {'key': 'account', 'title': 'Account created', 'hint': 'Your business is live.', 'done': True, 'url': '', 'cta': ''},
        {'key': 'product', 'title': 'Add your first product', 'hint': 'Replace the sample items with your real stock.',
         'done': has_product, 'url': reverse('inventory:product_create'), 'cta': 'Add product'},
        {'key': 'sale', 'title': 'Make your first sale', 'hint': 'Open the till and sell one item to see how fast it is.',
         'done': has_sale, 'url': reverse('sales:pos'), 'cta': 'Open POS'},
        {'key': 'staff', 'title': 'Invite a team member', 'hint': 'Give your cashier their own login and PIN.',
         'done': has_staff, 'url': reverse('accounts:employee_create'), 'cta': 'Add employee'},
    ]
    done = sum(1 for s in steps if s['done'])
    if done == len(steps) and not on_trial:
        return None
    next_step = next((s for s in steps if not s['done']), None)
    trial_days = sub.days_remaining if (on_trial and sub.is_timed_trial) else None
    return {
        'steps': steps, 'done': done, 'total': len(steps), 'percent': round(done * 100 / len(steps)),
        'next': next_step, 'complete': done == len(steps),
        'trial_days': max(trial_days, 0) if trial_days is not None else None,
        'upgrade_url': reverse('hq:packages_center') + '?renew=1',
        'packages_url': reverse('hq:packages_center'),
        'first_name': user.first_name or user.username,
    }
