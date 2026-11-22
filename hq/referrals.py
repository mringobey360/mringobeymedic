"""Referral programme: invite a business, earn a renewal discount when they pay.

* Every shop owner has a personal code (ReferralCode); link: /jiunge/?ref=CODE.
* A Referral row is created when an invited customer registers (PENDING).
* The reward is earned only when that customer PAYS (paid signup activation, or a trial
  upgraded to a paid plan) -> QUALIFIED, worth `referral_percent_per_invite` %.
* Qualified rewards are applied to the referrer's NEXT core-subscription renewal, summed
  and capped at `referral_max_percent`, and marked USED when that payment is confirmed.
* Anti-abuse: a code cannot be used by its own owner (same phone), one reward per invited
  signup, nothing is earned from free trials, and rewards come only from real payments.
"""
import logging
import secrets
import string
from decimal import Decimal, ROUND_HALF_UP

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import PaymentSettings, Referral, ReferralCode

logger = logging.getLogger('hq.referrals')
_ALPHABET = ''.join(c for c in string.ascii_uppercase + string.digits if c not in '0O1I')


def enabled():
    return PaymentSettings.load().referral_enabled


def normalize(code):
    return (code or '').strip().upper().replace(' ', '')


def get_or_create_code(owner):
    existing = ReferralCode.objects.filter(owner=owner).first()
    if existing:
        return existing
    for _ in range(10):
        code = 'SP' + ''.join(secrets.choice(_ALPHABET) for _ in range(6))
        try:
            with transaction.atomic():
                return ReferralCode.objects.create(owner=owner, code=code)
        except IntegrityError:
            existing = ReferralCode.objects.filter(owner=owner).first()
            if existing:
                return existing
    raise RuntimeError('Could not generate a unique referral code')


def find_code(code):
    code = normalize(code)
    if not code:
        return None
    return ReferralCode.objects.select_related('owner').filter(code=code, owner__is_active=True).first()


def register_referral(signup, referral_code):
    """Link a freshly created signup to the inviter (call right after the signup is saved)."""
    if not referral_code or not enabled():
        return None
    from accounts.phone_utils import normalize_phone_number
    try:
        if normalize_phone_number(referral_code.owner.phone_number or '') == normalize_phone_number(signup.phone_number):
            return None  # cannot invite yourself
    except Exception:
        pass
    ref, _ = Referral.objects.get_or_create(
        signup=signup, defaults={'referrer': referral_code.owner, 'code_used': referral_code.code})
    return ref


def qualify(signup, amount_paid=None):
    """The invited customer paid: make the referrer's reward available. Idempotent."""
    ps = PaymentSettings.load()
    if not ps.referral_enabled or (amount_paid is not None and Decimal(amount_paid) <= 0):
        return None
    with transaction.atomic():
        ref = Referral.objects.select_for_update().filter(signup=signup).first()
        if ref is None or ref.status != Referral.Status.PENDING:
            return None
        ref.status = Referral.Status.QUALIFIED
        ref.reward_percent = ps.referral_percent_per_invite
        ref.qualified_at = timezone.now()
        ref.save(update_fields=['status', 'reward_percent', 'qualified_at'])
    _notify_reward(ref, ps)
    return ref


def _notify_reward(ref, ps):
    try:
        from django.urls import reverse
        from notifications.models import Notification
        from notifications.utils import notify_many
        from sms_integration.utils import send_sms
        referrer = ref.referrer
        total = available_percent(referrer)
        text = (f"{ref.signup.business_name} joined SmartPOS using your invite. You earned "
                f"{ref.reward_percent}% off your next renewal (total now {total}%).")
        notify_many([referrer], 'Referral reward earned', text, category=Notification.Category.SYSTEM,
                    level=Notification.Level.SUCCESS, link_url=reverse('hq:referrals'))
        if referrer.phone_number:
            send_sms(referrer.phone_number, f"SmartPOS - Referral reward\n{text}")
    except Exception:
        logger.exception('Could not notify referrer for referral #%s', ref.pk)


def available_percent(owner):
    """Total discount (%) the owner can use on the next renewal (capped)."""
    ps = PaymentSettings.load()
    total = sum(Referral.objects.filter(referrer=owner, status=Referral.Status.QUALIFIED)
                .values_list('reward_percent', flat=True))
    return max(0, min(int(total), int(ps.referral_max_percent)))


def discounted_price(owner, full_price):
    """(price to pay, percent applied) for the owner's next renewal."""
    full_price = Decimal(full_price)
    pct = available_percent(owner)
    if pct <= 0:
        return full_price, 0
    net = (full_price * (Decimal(100) - pct) / Decimal(100)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return net, pct


def consume(owner, percent_given):
    """Mark rewards USED, oldest first, up to the discount that was actually given."""
    remaining = int(percent_given)
    if remaining <= 0:
        return 0
    used = 0
    now = timezone.now()
    for ref in Referral.objects.select_for_update().filter(
            referrer=owner, status=Referral.Status.QUALIFIED).order_by('qualified_at', 'pk'):
        if ref.reward_percent > remaining:
            break
        remaining -= ref.reward_percent
        used += ref.reward_percent
        ref.status = Referral.Status.USED
        ref.used_at = now
        ref.save(update_fields=['status', 'used_at'])
    return used


def percent_given(full_price, amount_paid):
    """Discount % implied by what was actually charged versus the full price."""
    full_price = Decimal(full_price)
    if full_price <= 0 or amount_paid is None or Decimal(amount_paid) >= full_price:
        return 0
    return int(((full_price - Decimal(amount_paid)) / full_price * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
