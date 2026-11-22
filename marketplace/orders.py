"""Order workflow for online shops: status moves, stock handling and seller notifications."""
import logging

from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from .models import DownloadGrant, Order, OrderStatusEvent, Product

logger = logging.getLogger(__name__)


class TransitionError(Exception):
    """Raised when a requested order status move is not allowed."""


def change_status(order_id, new_status, actor=None, note='', store=None):
    """Move an order one step along its delivery workflow, atomically.

    * Only moves listed in ``Order.FLOW`` are accepted (no skipping, no re-opening).
    * Cancelling needs a reason and returns the reserved stock to the shelf (once).
    * Every move is written to the order's audit trail.
    """
    note = (note or '').strip()[:300]
    with transaction.atomic():
        qs = Order.objects.select_for_update()
        if store is not None:
            qs = qs.filter(seller_store=store)
        order = qs.get(pk=order_id)
        if new_status not in Order.FLOW.get(order.status, ()):
            raise TransitionError('This order cannot move from "%s" to that step.' % order.get_status_display())
        if new_status in (Order.Status.CONFIRMED, Order.Status.OUT_FOR_DELIVERY, Order.Status.FULFILLED) \
                and order.payment_status != Order.PaymentStatus.PAID:
            raise TransitionError('Payment for this order is not confirmed yet, so it cannot be processed.')
        if new_status == Order.Status.CANCELLED and not note:
            raise TransitionError('Please enter the reason for cancelling this order.')
        now = timezone.now()
        fields = ['status']
        order.status = new_status
        if new_status == Order.Status.OUT_FOR_DELIVERY:
            order.dispatched_at = now
            fields.append('dispatched_at')
        elif new_status == Order.Status.FULFILLED:
            order.delivered_at = now
            fields.append('delivered_at')
        elif new_status == Order.Status.CANCELLED:
            order.cancelled_at = now
            order.cancel_reason = note
            fields += ['cancelled_at', 'cancel_reason']
            if not order.stock_restored:
                for item in order.items.all():
                    if item.is_digital or not item.product_id:
                        continue  # digital goods never consumed shelf stock; deleted products have no stock to restore
                    Product.objects.filter(pk=item.product_id).update(stock=F('stock') + item.quantity)
                order.stock_restored = True
                fields.append('stock_restored')
            DownloadGrant.objects.filter(order_item__order=order).update(revoked=True)
        order.save(update_fields=fields)
        OrderStatusEvent.objects.create(order=order, status=new_status, note=note, actor=actor)
    return order


def record_created(order):
    """First entry of the audit trail for a freshly created order."""
    OrderStatusEvent.objects.get_or_create(order=order, status=Order.Status.NEW, defaults={'note': 'Order placed and paid online.'})


def provision_digital(order):
    """Issue download links for the digital items of a PAID order (idempotent).

    A digital-only order has nothing to ship, so it is completed immediately; a mixed order keeps its
    normal delivery workflow for the physical items while the downloads are available right away.
    Call this only after payment has been verified.
    """
    if order.payment_status != Order.PaymentStatus.PAID:
        return 0
    items = list(order.items.all())
    digital = [i for i in items if i.is_digital]
    for item in digital:
        DownloadGrant.objects.get_or_create(order_item=item)
    if digital and len(digital) == len(items) and order.status == Order.Status.NEW:
        order.status = Order.Status.FULFILLED
        order.delivered_at = timezone.now()
        order.save(update_fields=['status', 'delivered_at'])
        OrderStatusEvent.objects.create(order=order, status=Order.Status.FULFILLED,
                                        note='Digital items delivered: download links issued after verified payment.')
    return len(digital)


def notify_new_order(order):
    """Tell the shop's managers (in-app) that a paid order is waiting. Never raises."""
    try:
        store = order.seller_store
        if store is None:
            return
        from django.contrib.auth import get_user_model
        from notifications.models import Notification
        from notifications.utils import notify
        User = get_user_model()
        managers = User.objects.filter(is_active=True, role=User.Role.ADMIN, managed_pharmacies=store.pharmacy).distinct()
        link = reverse('marketplace:seller_order_detail', args=[store.pk, order.reference])
        for manager in managers:
            notify(manager, title='New online order',
                   message=f'{order.customer_name} ordered TSh {order.total:,.0f} from {store.public_name}. Confirm and arrange delivery.',
                   category=Notification.Category.SYSTEM, level=Notification.Level.SUCCESS,
                   link_url=link, pharmacy=store.pharmacy, dedupe_key=f'marketplace-new-order:{order.pk}')
    except Exception:
        logger.warning('Could not notify managers about marketplace order %s', getattr(order, 'pk', '?'), exc_info=True)


def send_customer_ticket_sms(order_id):
    """Best-effort verified-payment confirmation, with recoverable Ticket ID.

    Uses the platform SMS sender (not the seller's optional alert quota).
    Failed SMS never affects payment or download entitlements.
    """
    try:
        from sms_integration.models import SMSLog
        from sms_integration.utils import send_sms
        from .refs import short_reference
        order = Order.objects.get(pk=order_id, payment_status=Order.PaymentStatus.PAID)
        ticket = short_reference(order.reference)
        # Avoid repeat sends from replayed provider callbacks or admin retries.
        if SMSLog.objects.filter(category=SMSLog.Category.PAYMENT_RECEIPT,
                                 recipient__in=[order.phone, str(order.phone)],
                                 message__contains=ticket, was_sent=True).exists():
            return
        from django.conf import settings
        base = (getattr(settings, 'SITE_URL', '') or 'https://mringobey.store').rstrip('/')
        track_url = base + reverse('marketplace:track')
        message = (f'SmartPOS: Malipo yako ya TSh {order.total:,.0f} yamethibitishwa. '
                   f'Ticket ID: {ticket}. Fuatilia oda na pakua bidhaa zako: {track_url} '
                   'Tumia Ticket ID na namba ya simu uliyotumia kununua.')
        if not send_sms(order.phone, message, category=SMSLog.Category.PAYMENT_RECEIPT,
                        owner=None, consume_wallet=False):
            logger.warning('Ticket confirmation SMS not delivered for order %s', order.pk)
    except Exception:
        logger.exception('Ticket confirmation SMS error for order %s', order_id)
