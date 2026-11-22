"""
Activation logic for Premium SMS package purchases, shared between:

1. The superadmin manually approving a mobile-money payment
   (hq.views.approve_*_sms_purchase_view).
2. Pesapal online payment completing automatically (payments.services).

Top-up rules (identical for every package family — Debt, Receipt, Marketing and,
via apply_package_entitlement, Intelligent Report SMS):

* The purchase row is LOCKED and re-checked inside the transaction, so an HQ
  approval and an online payment racing each other credit the customer once.
* If the owner already has a LIVE entitlement (status ACTIVE and not past its end
  date) the new package is a TOP-UP: its credits are added to that entitlement and
  its validity is stacked after the current end date (no paid days are lost).
* Otherwise a fresh entitlement is opened starting today.
* Notifications and SMS are sent only AFTER the transaction commits, and a failure
  to notify can never undo or break a payment that was applied.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from notifications.models import Notification
from notifications.utils import notify_many

from .models import SMSDebtSubscription, SMSLog, SMSReceiptSubscription
from .utils import send_sms

logger = logging.getLogger('sms_integration.services')

# Admin note put on an online-checkout placeholder that was abandoned and closed automatically.
ONLINE_CLOSED_NOTE = 'Online checkout was not completed (closed automatically).'


def visible_purchases(qs):
    """
    Hide ONLINE-checkout placeholders from customer histories, HQ queues and the
    "already pending" guard. An online checkout creates its own purchase row with a
    blank payment_reference; until it is paid (and activated) it is not a request
    anyone has to verify, and if abandoned it is closed automatically. Real manual
    requests always carry the customer's mobile-money reference, so they are never hidden.
    """
    return qs.exclude(status__in=['PENDING', 'REJECTED'], payment_reference='')


def lock_pending_purchase(model, purchase):
    """Return the purchase row locked and still PENDING, or None if it was already handled.
    Must be called inside transaction.atomic()."""
    locked = model.objects.select_for_update().get(pk=purchase.pk)
    return locked if locked.status == model.Status.PENDING else None


def apply_package_entitlement(model, owner, package, *, credits_field, credits, amount, reference, order_by,
                              never_expires=True):
    """
    Add `credits` to the owner's live entitlement (top-up) or open a new one.
    Returns (entitlement, topped_up). Must be called inside transaction.atomic().

    `order_by` MUST match the ordering the consuming code uses to pick the entitlement
    it spends from, so topped-up credits land on the row that is actually used.
    """
    today = timezone.localdate()
    duration = timedelta(days=package.duration_days)
    live_qs = model.objects.select_for_update().filter(owner=owner, status=model.Status.ACTIVE)
    if never_expires:
        # Credits never expire: any ACTIVE row is topped up, whatever its old end_date.
        from .models import NO_EXPIRY_DATE
        new_end = NO_EXPIRY_DATE
    else:
        live_qs = live_qs.filter(end_date__gte=today)
        new_end = None
    live = live_qs.order_by(*order_by).first()
    if live:
        setattr(live, credits_field, getattr(live, credits_field) + credits)
        live.end_date = new_end or (max(live.end_date, today) + duration)
        live.amount_paid = (live.amount_paid or 0) + amount
        live.package = package
        live.payment_reference = reference
        live.save(update_fields=[credits_field, 'end_date', 'amount_paid', 'package', 'payment_reference', 'updated_at'])
        return live, True
    entitlement = model.objects.create(
        owner=owner, package=package, start_date=today, end_date=new_end or (today + duration),
        status=model.Status.ACTIVE, amount_paid=amount, payment_reference=reference,
        **{credits_field: credits},
    )
    return entitlement, False


def mark_purchase_approved(locked, reviewed_by, reference, admin_note=''):
    locked.status = locked.Status.APPROVED
    locked.reviewed_at = timezone.now()
    locked.reviewed_by = reviewed_by
    locked.payment_reference = reference
    fields = ['status', 'reviewed_at', 'reviewed_by', 'payment_reference']
    if admin_note:
        locked.admin_note = admin_note
        fields.append('admin_note')
    locked.save(update_fields=fields)


def notify_package_activated(owner, *, title, message, link_name, sms_message=''):
    """Best-effort notification + SMS, deferred until the surrounding transaction commits."""
    def _send():
        try:
            notify_many([owner], title, message, category=Notification.Category.SYSTEM,
                        level=Notification.Level.SUCCESS, link_url=reverse(link_name))
        except Exception:
            logger.exception("Could not create the '%s' notification for user #%s", title, owner.pk)
        if sms_message:
            try:
                send_sms(owner.phone_number, sms_message, category=SMSLog.Category.OTHER,
                         owner=None, consume_wallet=False)
            except Exception:
                logger.warning("Package activation SMS to user #%s failed", owner.pk, exc_info=True)
    transaction.on_commit(_send)


def _approve(purchase, model, subscription_model, *, reviewed_by, reference, order_by=('id',),
             never_expires=True):
    """Shared body of the three SMS approvals. Returns the (refreshed) purchase."""
    with transaction.atomic():
        locked = lock_pending_purchase(model, purchase)
        if locked is None:
            purchase.refresh_from_db()
            return purchase, None, None
        package = locked.package
        payment_reference = reference or locked.payment_reference
        entitlement, topped_up = apply_package_entitlement(
            subscription_model, locked.owner, package,
            credits_field='sms_credits', credits=package.sms_credits,
            amount=locked.amount or package.price, reference=payment_reference, order_by=order_by,
            never_expires=never_expires,
        )
        mark_purchase_approved(locked, reviewed_by, payment_reference)
    purchase.refresh_from_db()
    return purchase, package, entitlement


def approve_debt_sms_purchase(purchase, *, reviewed_by=None, reference=''):
    """purchase: sms_integration.models.SMSDebtPurchase (status PENDING)."""
    from .models import SMSDebtPurchase
    purchase, package, entitlement = _approve(
        purchase, SMSDebtPurchase, SMSDebtSubscription, reviewed_by=reviewed_by, reference=reference)
    if package is not None:
        notify_package_activated(
            purchase.owner, title='Premium Debt SMS activated',
            message=(f'Your Premium Debt SMS package is active: {package.sms_credits:,} SMS added. '
                     f'Remaining: {entitlement.credits_remaining:,} SMS. These SMS do not expire - use them until they finish.'),
            link_name='sms_integration:debt_sms',
            sms_message=f'Premium Debt SMS activated: {package.sms_credits:,} SMS added (no expiry). Open SmartPOS to use debt reminders.',
        )
    return purchase


def approve_receipt_sms_purchase(purchase, *, reviewed_by=None, reference=''):
    """purchase: sms_integration.models.SMSReceiptPurchase (status PENDING)."""
    from .models import SMSReceiptPurchase
    purchase, package, entitlement = _approve(
        purchase, SMSReceiptPurchase, SMSReceiptSubscription, reviewed_by=reviewed_by, reference=reference)
    if package is not None:
        notify_package_activated(
            purchase.owner, title='Premium Receipt SMS activated',
            message=(f'Your Premium Receipt SMS package is active: {package.sms_credits:,} SMS added. '
                     f'Remaining: {entitlement.credits_remaining:,} SMS. These SMS do not expire - use them until they finish.'),
            link_name='sms_integration:receipt_sms',
            sms_message=f'Premium Receipt SMS activated: {package.sms_credits:,} SMS added (no expiry).',
        )
    return purchase


def approve_marketing_sms_purchase(purchase, *, reviewed_by=None, reference=''):
    """Activate or top-up a paid Customer Marketing SMS entitlement."""
    from .models import SMSMarketingPurchase, SMSMarketingSubscription
    purchase, package, entitlement = _approve(
        purchase, SMSMarketingPurchase, SMSMarketingSubscription, reviewed_by=reviewed_by, reference=reference,
        order_by=('id',))
    if package is not None:
        notify_package_activated(
            purchase.owner, title='Customer Marketing SMS activated',
            message=(f'Your {package.name} is active: {package.sms_credits:,} SMS added. '
                     f'Remaining: {entitlement.credits_remaining:,} SMS. These SMS do not expire - use them until they finish.'),
            link_name='hq:marketing_sms',
            sms_message=f'Customer Marketing SMS activated: {package.sms_credits:,} SMS added. They do not expire - use them until finished.',
        )
    return purchase
