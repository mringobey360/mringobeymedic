"""Business rules for returning / voiding sales.

A sale is never deleted. A mistake is corrected through a SaleReturnRequest:

* an employee can only REQUEST a return (reason is mandatory);
* a Shop Owner / Supervisor approves or rejects it — or performs it directly,
  which still creates an already-approved request so the audit trail is uniform;
* on approval, stock goes back to the SAME batch it was sold from (or the item
  is written off), SaleItem.quantity drops to the NET quantity still sold, the
  linked debt is reduced, and the sale becomes PARTIALLY_RETURNED or VOIDED.

Everything runs inside one transaction with row locks, so two people can never
return the same units twice.
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from inventory.models import ProductBatch, StockMovement
from notifications.models import Notification
from notifications.utils import notify

from .models import Debt, Sale, SaleItem, SaleReturnLine, SaleReturnRequest

MAX_REASON_LENGTH = 500


class ReturnError(Exception):
    """A rule was broken; the message is safe to show to the user."""


def receipt_no(sale):
    return f"PM-{sale.pharmacy_id}-{(sale.sale_number or sale.pk):08d}"


def employee_window():
    return timedelta(hours=getattr(settings, 'SALE_RETURN_EMPLOYEE_WINDOW_HOURS', 24))


def within_employee_window(sale):
    return timezone.now() - sale.created_at <= employee_window()


def user_can_request(user, sale):
    """Owners: any sale in their branch. Employees: their own sales, inside the time window."""
    if sale.status == Sale.Status.VOIDED:
        return False
    if user.is_management_user():
        return True
    return sale.served_by_id == user.id and within_employee_window(sale)


def pending_quantities(sale):
    """{sale_item_id: quantity already reserved by PENDING requests}."""
    rows = (
        SaleReturnLine.objects
        .filter(request__sale=sale, request__status=SaleReturnRequest.Status.PENDING)
        .values('sale_item_id')
        .annotate(total=Sum('quantity'))
    )
    return {r['sale_item_id']: r['total'] for r in rows}


def returnable_items(sale):
    """Items that can still be returned, with the quantity left after pending requests."""
    pending = pending_quantities(sale)
    result = []
    for item in sale.items.select_related('product', 'batch').all():
        available = item.quantity - pending.get(item.pk, 0)
        result.append({
            'item': item,
            'pending': pending.get(item.pk, 0),
            'available': max(available, 0),
        })
    return result


def _managers(pharmacy):
    return list(pharmacy.managers.filter(is_active=True))


@transaction.atomic
def create_request(sale_id, user, pharmacy, requested_lines, reason, full_void=False):
    """Validate and store a return request. Returns the new (PENDING) request.

    requested_lines: {sale_item_id: quantity}
    """
    reason = (reason or '').strip()
    if len(reason) < 3:
        raise ReturnError("Please explain why this sale is being returned.")
    if len(reason) > MAX_REASON_LENGTH:
        raise ReturnError(f"The reason is too long (maximum {MAX_REASON_LENGTH} characters).")

    # Lock the sale so concurrent requests/approvals for it are serialised.
    sale = Sale.objects.select_for_update().filter(pk=sale_id, pharmacy=pharmacy).first()
    if sale is None:
        raise ReturnError("This sale could not be found in the current branch.")
    if sale.status == Sale.Status.VOIDED:
        raise ReturnError("This sale has already been fully voided.")
    if not user.is_management_user():
        if sale.served_by_id != user.id:
            raise ReturnError("You can only request returns for sales you served.")
        if not within_employee_window(sale):
            hours = int(employee_window().total_seconds() // 3600)
            raise ReturnError(f"Returns can only be requested within {hours} hours of the sale. Please ask the shop owner.")

    items = list(sale.items.select_related('product'))
    items_by_id = {i.pk: i for i in items}
    pending = pending_quantities(sale)

    if full_void:
        requested_lines = {
            i.pk: i.quantity - pending.get(i.pk, 0)
            for i in items if i.quantity - pending.get(i.pk, 0) > 0
        }

    cleaned = {}
    for raw_id, raw_qty in (requested_lines or {}).items():
        try:
            item_id, qty = int(raw_id), int(raw_qty)
        except (TypeError, ValueError):
            raise ReturnError("Invalid quantity.")
        if qty <= 0:
            continue
        item = items_by_id.get(item_id)
        if item is None:
            raise ReturnError("One of the selected items does not belong to this sale.")
        available = item.quantity - pending.get(item_id, 0)
        if qty > available:
            if available <= 0:
                raise ReturnError(f"'{item.product.name}' has nothing left to return (already returned or awaiting approval).")
            raise ReturnError(f"Only {available} unit(s) of '{item.product.name}' can still be returned.")
        cleaned[item_id] = qty

    if not cleaned:
        raise ReturnError("Select at least one item and a quantity to return.")

    # Does this request take EVERYTHING that is still counted in the sale?
    takes_everything = all(cleaned.get(i.pk, 0) == i.quantity for i in items if i.quantity > 0)

    rr = SaleReturnRequest.objects.create(
        pharmacy=pharmacy, sale=sale, reason=reason, requested_by=user, is_full_void=takes_everything,
    )
    SaleReturnLine.objects.bulk_create([
        SaleReturnLine(request=rr, sale_item_id=item_id, quantity=qty) for item_id, qty in cleaned.items()
    ])
    return rr


def notify_managers_of_request(rr, actor):
    """Tell every owner/supervisor of the branch that a return needs a decision."""
    actor_name = actor.get_full_name() or actor.username
    kind = "void" if rr.is_full_void else "return"
    for manager in _managers(rr.pharmacy):
        if manager.pk == actor.pk:
            continue
        try:
            notify(
                manager,
                title=f"Sale {kind} request — #{rr.sale.sale_number or rr.sale_id}",
                message=f"{actor_name} asked to {kind} items worth TZS {rr.total_value:,.0f}. Reason: {rr.reason[:150]}",
                category=Notification.Category.SALES, level=Notification.Level.WARNING,
                link_url=reverse('sales:returns'), pharmacy=rr.pharmacy,
            )
        except Exception:  # noqa: BLE001 - a notification must never break the business action
            pass


@transaction.atomic
def approve_request(request_id, decided_by, disposition=SaleReturnRequest.Disposition.RESTOCK, note=''):
    """Apply a PENDING request: give stock back, reduce net quantities/debt, update sale status."""
    if disposition not in SaleReturnRequest.Disposition.values:
        raise ReturnError("Choose whether the items go back to stock or are written off.")

    rr = SaleReturnRequest.objects.select_for_update().filter(pk=request_id).first()
    if rr is None:
        raise ReturnError("Return request not found.")
    if rr.status != SaleReturnRequest.Status.PENDING:
        raise ReturnError("This request has already been decided.")

    sale = Sale.objects.select_for_update().get(pk=rr.sale_id)

    # A cash refund leaves the drawer on the day it is DECIDED (CashLedgerService rule: decided_at), and
    # the original sale stays counted at its full value on its own day. So the day that must still be
    # open is TODAY's cash day, not the day the sale was made. A void requested yesterday and approved
    # today is therefore fine even though yesterday's cash day is already closed; only an already
    # ended/closed cash day for today blocks it (that would silently change a counted drawer).
    if sale.payment_method in (Sale.PaymentMethod.CASH, Sale.PaymentMethod.SPLIT):
        from expenses.models import CashDay
        today = timezone.localdate()
        cash_day = CashDay.objects.filter(pharmacy=sale.pharmacy, date=today).first()
        if cash_day and cash_day.status in (CashDay.Status.DAY_ENDED, CashDay.Status.CLOSED):
            raise ReturnError(
                f"Today's cash day ({cash_day.date:%d %b %Y}) has already ended or been closed, and this refund "
                f'would take cash out of the drawer today. Reopen today\'s cash day through Cash Control, '
                f'then approve the request again.'
            )

    lines = list(rr.lines.all())
    # Lock only the SaleItem rows (no join), so product rows are not locked needlessly.
    items = {
        i.pk: i for i in SaleItem.objects.select_for_update().filter(pk__in=[l.sale_item_id for l in lines])
    }

    now = timezone.now()
    refund_amount = Decimal('0')
    label = receipt_no(sale)
    short_reason = rr.reason[:120]

    for line in lines:
        item = items.get(line.sale_item_id)
        if item is None or line.quantity > item.quantity:
            raise ReturnError(
                f"'{item.product.name if item else 'An item'}' no longer has {line.quantity} unit(s) left to return. "
                "Reject this request and create a new one."
            )
        item.quantity -= line.quantity
        item.returned_quantity += line.quantity
        item.save(update_fields=['quantity', 'returned_quantity'])
        refund_amount += item.unit_price * line.quantity

        if disposition == SaleReturnRequest.Disposition.RESTOCK and item.batch_id:
            batch = ProductBatch.objects.select_for_update().get(pk=item.batch_id)
            batch.quantity += line.quantity
            batch.save(update_fields=['quantity'])
            StockMovement.objects.create(
                product=item.product, batch=batch, movement_type=StockMovement.MovementType.SALE_RETURN,
                quantity_change=line.quantity, performed_by=decided_by,
                note=f"Returned from {label}: {short_reason}"[:255],
            )
        else:
            # Written off (or the original batch is unknown): stock is NOT increased,
            # but the event is still recorded so the loss is traceable.
            StockMovement.objects.create(
                product=item.product, batch=item.batch, movement_type=StockMovement.MovementType.WRITE_OFF,
                quantity_change=0, performed_by=decided_by,
                note=f"Written off, {line.quantity} unit(s) from {label}: {short_reason}"[:255],
            )

    # Sale status from the net quantities that remain.
    remaining_units = sale.items.aggregate(total=Sum('quantity'))['total'] or 0
    if remaining_units == 0:
        sale.status = Sale.Status.VOIDED
        sale.voided_at = now
    else:
        sale.status = Sale.Status.PARTIALLY_RETURNED
    sale.save(update_fields=['status', 'voided_at'])

    # Money: a debt is reduced; a paid sale means cash/mobile money goes back to the customer.
    debt = Debt.objects.select_for_update().filter(sale=sale).first()
    if debt is not None:
        new_owed = max(debt.amount_owed - refund_amount, Decimal('0'))
        overpaid = max(debt.amount_paid - new_owed, Decimal('0'))
        debt.amount_owed = new_owed
        debt.amount_paid = min(debt.amount_paid, new_owed)
        debt.is_settled = debt.amount_paid >= debt.amount_owed
        debt.save()
        refund_due = overpaid
    else:
        refund_due = refund_amount

    rr.status = SaleReturnRequest.Status.APPROVED
    rr.decided_by = decided_by
    rr.decided_at = now
    rr.decision_note = (note or '').strip()[:MAX_REASON_LENGTH]
    rr.disposition = disposition
    rr.refund_amount = refund_amount
    rr.refund_due = refund_due
    rr.is_full_void = sale.status == Sale.Status.VOIDED
    rr.save()
    return rr


@transaction.atomic
def reject_request(request_id, decided_by, note=''):
    rr = SaleReturnRequest.objects.select_for_update().filter(pk=request_id).first()
    if rr is None:
        raise ReturnError("Return request not found.")
    if rr.status != SaleReturnRequest.Status.PENDING:
        raise ReturnError("This request has already been decided.")
    note = (note or '').strip()
    if len(note) < 3:
        raise ReturnError("Please give a short reason for rejecting this request.")
    rr.status = SaleReturnRequest.Status.REJECTED
    rr.decided_by = decided_by
    rr.decided_at = timezone.now()
    rr.decision_note = note[:MAX_REASON_LENGTH]
    rr.save()
    return rr


def notify_requester_of_decision(rr):
    """Tell the employee what happened to their request (unless they decided it themselves)."""
    if not rr.requested_by_id or rr.requested_by_id == rr.decided_by_id:
        return
    approved = rr.status == SaleReturnRequest.Status.APPROVED
    number = rr.sale.sale_number or rr.sale_id
    if approved:
        message = f"Your return request for sale #{number} was approved."
        if rr.refund_due > 0:
            message += f" Hand TZS {rr.refund_due:,.0f} back to the customer."
    else:
        message = f"Your return request for sale #{number} was rejected. {rr.decision_note[:150]}"
    try:
        notify(
            rr.requested_by, title=f"Return request {'approved' if approved else 'rejected'}", message=message,
            category=Notification.Category.SALES,
            level=Notification.Level.SUCCESS if approved else Notification.Level.WARNING,
            link_url=reverse('sales:returns'), pharmacy=rr.pharmacy,
        )
    except Exception:  # noqa: BLE001
        pass
