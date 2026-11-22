"""Layaway business rules that touch other apps: soft-reservation counts and the official handover.

Design rules
------------
* Layaway quantities are soft reservations. POS protects reserved units by default; a manager can explicitly
  authorise a sale against an unpaid plan, with a reason and an audited resolution workflow.
* Fully-paid plans are protected from reservation override. A manager cannot sell stock promised to a fully-paid customer.
* The Sale (stock deduction, receipt, sales reports) is created ONLY at handover, when the product physically
  leaves the shop. Until then layaway money is customer deposits, not revenue.
* Cash is different: it is counted in the cash drawer the day each cash payment is received (see
  expenses/ledger.py, "Layaway"), labelled as layaway. The handover Sale is excluded from the drawer so the same
  cash is never counted twice, which also means handover is never blocked by a closed cash day.
* If stock is no longer available at handover, nothing is changed: the plan stays as it is, the cashier is told
  exactly how many units are missing, and the manager can restock or flag the plan for resolution.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from .models import LayawayAudit, LayawayHandover, LayawayPayment, LayawayPlan

# A plan reserves stock while the customer still expects the product; released plans are excluded.
RESERVING_STATUSES = (
    LayawayPlan.Status.ACTIVE,
    LayawayPlan.Status.PAID_PENDING_HANDOVER,
    LayawayPlan.Status.PARTIALLY_HANDED_OVER,
    LayawayPlan.Status.NEEDS_RESOLUTION,
)


class HandoverError(Exception):
    """A handover was refused. The message is safe to show to the cashier; nothing was changed."""


def reserved_quantities(pharmacy, product_ids=None):
    """Return only the unhanded quantity reserved by open plans."""
    qs = (LayawayPlan.objects.filter(pharmacy=pharmacy, status__in=RESERVING_STATUSES,
                                     reservation_released=False)
          .annotate(handed_qty=Sum('handovers__quantity')))
    if product_ids is not None:
        qs = qs.filter(product_id__in=list(product_ids))
    out = {}
    for row in qs.values('id', 'product_id', 'status', 'quantity', 'handed_qty'):
        remaining = max(0, int(row['quantity'] or 0) - int(row['handed_qty'] or 0))
        if not remaining:
            continue
        d = out.setdefault(row['product_id'], {'qty': 0, 'plans': 0, 'ready': 0})
        d['qty'] += remaining
        d['plans'] += 1
        if row['status'] == LayawayPlan.Status.PAID_PENDING_HANDOVER:
            d['ready'] += remaining
    return out


def reservation_for_product(product):
    return reserved_quantities(product.pharmacy_id, [product.pk]).get(product.pk, {'qty': 0, 'plans': 0, 'ready': 0})


def payment_split(plan):
    """(cash, non_cash) actually received for the plan. Mobile money, bank and other never enter the drawer."""
    cash = Decimal('0')
    other = Decimal('0')
    for p in plan.payments.filter(status=LayawayPayment.Status.RECEIVED):
        if p.method == LayawayPayment.Method.CASH:
            cash += p.amount
        else:
            other += p.amount
    return cash, other


def _customer_contact(pharmacy, user, plan):
    """Same customer registry entry a normal POS sale would create. Never allowed to break a handover."""
    try:
        from sales.views import _get_or_create_customer_contact
        with transaction.atomic():
            return _get_or_create_customer_contact(pharmacy, user, plan.customer_name, plan.customer_phone)
    except Exception:  # noqa: BLE001
        return None


def hand_over(plan_id, user, pharmacy, quantity=None, note=''):
    """Record a partial or full physical handover for units already covered by payments.

    A handover creates one official Sale and one LayawayHandover event. Deposits already
    received are not collected again at handover. A missing unit remains on the plan.
    """
    from inventory.models import FEFO_ORDER, ProductBatch, StockMovement
    from sales.models import Sale, SaleItem

    with transaction.atomic():
        try:
            plan = (LayawayPlan.objects.select_for_update()
                    .select_related('product', 'pharmacy').get(pk=plan_id, pharmacy=pharmacy))
        except LayawayPlan.DoesNotExist:
            raise HandoverError('This layaway plan was not found in the current branch.')

        if plan.status in (LayawayPlan.Status.COMPLETED, LayawayPlan.Status.CANCELLED):
            raise HandoverError('This plan is already closed and cannot be handed over.')
        if plan.reservation_released:
            raise HandoverError('This plan reservation was released for resolution. A manager must reopen or resolve it before handover.')

        already_handed = plan.quantity_handed_over
        remaining_plan = max(0, int(plan.quantity) - int(already_handed))
        if remaining_plan <= 0:
            raise HandoverError('All units on this plan have already been handed over.')

        total_paid = plan.amount_paid - plan.amount_refunded
        unit_price = plan.unit_price or Decimal('0')
        if unit_price <= 0:
            raise HandoverError('The agreed unit price is invalid. Ask a manager to review this plan.')
        # Never hand over more product value than the net amount actually retained.
        paid_units_total = int(total_paid // unit_price)
        eligible_units = max(0, paid_units_total - int(already_handed))
        if eligible_units <= 0:
            raise HandoverError('No units are yet covered by retained payments. Record a payment before handover.')

        try:
            requested = int(quantity) if quantity not in (None, '') else remaining_plan
        except (TypeError, ValueError):
            raise HandoverError('Enter a valid whole-number quantity to hand over.')
        if requested < 1:
            raise HandoverError('Handover quantity must be at least one unit.')
        if requested > remaining_plan:
            raise HandoverError(f'Only {remaining_plan} unit(s) remain to be handed over on this plan.')
        if requested > eligible_units:
            raise HandoverError(f'Payments currently cover {eligible_units} additional unit(s). Record more payment before handing over {requested}.')

        product = plan.product
        batches = list(ProductBatch.objects.select_for_update()
                       .filter(product=product, quantity__gt=0).order_by(*FEFO_ORDER))
        sellable = [b for b in batches if not b.is_expired]
        available = sum(b.quantity for b in sellable)
        # Keep other customers' active reservations protected. This plan can use
        # its own remaining reservation plus genuinely unreserved stock only.
        reservation_totals = reserved_quantities(pharmacy, [product.pk])
        total_reserved = reservation_totals.get(product.pk, {}).get('qty', 0)
        other_reserved = max(0, int(total_reserved) - int(remaining_plan))
        available_for_plan = max(0, int(available) - other_reserved)
        if available_for_plan < requested:
            shortage = requested - available_for_plan
            raise HandoverError(
                f"Cannot hand over {requested} unit(s) of '{product.name}' yet. "
                f"There are {available} sellable unit(s) in the branch, but {other_reserved} "
                f"are reserved for other active customer plan(s), leaving {available_for_plan} "
                f"available for this plan. Restock at least {shortage} unit(s) or choose a smaller "
                'quantity if one is available. No payment, stock, sale, or handover record was changed.')

        # The original Layaway payments already entered cash/non-cash ledgers. This Sale records
        # product revenue/stock only; zero new tender prevents double-counting the drawer.
        cash, other = payment_split(plan)
        if cash == 0:
            method = Sale.PaymentMethod.MOBILE_MONEY
        elif other == 0:
            method = Sale.PaymentMethod.CASH
        else:
            method = Sale.PaymentMethod.SPLIT
        sale = Sale.objects.create(
            pharmacy=pharmacy, served_by=user, payment_method=method, cash_paid=Decimal('0'), mobile_paid=Decimal('0'),
            customer_name=plan.customer_name, customer_phone=plan.customer_phone,
            customer_contact=_customer_contact(pharmacy, user, plan), business_date=timezone.localdate(),
        )

        remaining = requested
        for batch in sellable:
            if remaining <= 0:
                break
            take = min(batch.quantity, remaining)
            list_price = batch.selling_price or unit_price
            original = max(list_price, unit_price)
            per_unit_discount = original - unit_price
            SaleItem.objects.create(
                sale=sale, product=product, batch=batch, quantity=take,
                unit_price=unit_price, unit_cost=batch.purchase_price, original_unit_price=original,
                discount_percent=(per_unit_discount / original * Decimal('100')).quantize(Decimal('0.01')) if original > 0 else Decimal('0.00'),
                discount_amount=(per_unit_discount * take).quantize(Decimal('0.01')),
            )
            batch.quantity -= take
            batch.save(update_fields=['quantity'])
            StockMovement.objects.create(
                product=product, batch=batch, movement_type=StockMovement.MovementType.SALE,
                quantity_change=-take, note=f'Layaway {plan.reference} handover ({take}/{plan.quantity}) - Sale #{sale.id}', performed_by=user,
            )
            remaining -= take

        now = timezone.now()
        handover = LayawayHandover.objects.create(plan=plan, sale=sale, quantity=requested,
                                                   handed_over_by=user, handed_over_at=now,
                                                   note=(note or '').strip()[:255])
        total_handed = already_handed + requested
        if total_handed >= plan.quantity:
            plan.status = LayawayPlan.Status.COMPLETED
            plan.handed_over_at = now
            plan.handed_over_by = user
        elif plan.balance == 0:
            plan.status = LayawayPlan.Status.PAID_PENDING_HANDOVER
        else:
            plan.status = LayawayPlan.Status.PARTIALLY_HANDED_OVER if total_handed else LayawayPlan.Status.ACTIVE
        # Keep the latest sale available to existing receipt UI; individual receipts remain in handovers.
        plan.sale = sale
        plan.save(update_fields=['sale', 'status', 'handed_over_at', 'handed_over_by'])
        LayawayAudit.objects.create(
            plan=plan, actor=user, action='PARTIAL_HANDOVER' if total_handed < plan.quantity else 'HANDED_OVER',
            detail=(f'{requested} x {product.name} handed to {plan.customer_name}; {total_handed}/{plan.quantity} total handed over. '
                    f'Official Sale #{sale.sale_number or sale.pk}; TZS {(unit_price * requested):,.0f} product value. '
                    f'{plan.quantity - total_handed} unit(s) remain. No additional payment collected at handover.'),
        )
        return sale



def approve_layaway_sms_purchase(purchase, *, reviewed_by=None, reference=''):
    """Idempotently activate a Layaway SMS credit purchase after verified payment."""
    from django.utils import timezone
    from .models import LayawaySMSPurchase, LayawaySMSWallet
    with transaction.atomic():
        locked = LayawaySMSPurchase.objects.select_for_update().select_related('package', 'pharmacy').get(pk=purchase.pk)
        if locked.status == LayawaySMSPurchase.Status.APPROVED and locked.credits_granted:
            return False
        if locked.status not in (LayawaySMSPurchase.Status.PENDING, LayawaySMSPurchase.Status.REJECTED):
            raise RuntimeError('This SMS purchase cannot be activated from its current status.')
        wallet, _ = LayawaySMSWallet.objects.get_or_create(pharmacy=locked.pharmacy)
        wallet = LayawaySMSWallet.objects.select_for_update().get(pk=wallet.pk)
        if not locked.credits_granted:
            wallet.paid_credits += locked.package.sms_credits
            wallet.save(update_fields=['paid_credits', 'updated_at'])
            locked.credits_granted = True
        locked.status = LayawaySMSPurchase.Status.APPROVED
        locked.payment_reference = (reference or locked.payment_reference)[:150]
        locked.admin_note = 'Payment verified automatically by online gateway.'
        locked.reviewed_at = timezone.now()
        locked.reviewed_by = reviewed_by
        locked.save(update_fields=['status', 'payment_reference', 'admin_note', 'reviewed_at', 'reviewed_by', 'credits_granted'])
        return True
