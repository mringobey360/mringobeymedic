"""Bulk branch-to-branch stock transfer (V54.25).

One transfer = one reference = many batch lines, processed in a single database
transaction. Either every line is moved or nothing is.

Safety properties
-----------------
* Branch scope  - source and destination must both be branches the user manages.
* Re-validation - every batch is re-read under ``SELECT ... FOR UPDATE`` inside the
  transaction; the quantities the browser saw are never trusted.
* Deadlock-safe - batches are locked in primary-key order, so two managers moving
  overlapping batches cannot deadlock each other.
* All-or-nothing - all line problems are collected and reported together, and the
  transaction is rolled back before anything is written.
* Safe retry   - a client-generated idempotency key makes double clicks, refreshes
  and network retries return the original transfer instead of creating a second one.
* Audit trail  - per-line snapshot (product, batch, cost, price, expiry, remaining
  stock) plus StockMovement rows on both branches.
"""
import re
import secrets
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    BranchStockTransfer, BranchStockTransferItem, Category, Product, ProductBatch, StockMovement, unit_label,
)

MAX_ITEMS_PER_TRANSFER = 200
MAX_QUANTITY_PER_LINE = 10_000_000
_KEY_RE = re.compile(r'^[A-Za-z0-9_-]{16,64}$')


class TransferError(Exception):
    """A transfer that was refused. Nothing has been changed when this is raised."""

    def __init__(self, message, line_errors=None):
        super().__init__(message)
        self.message = message
        # {source_batch_id: "reason"} so the UI can mark the exact cart rows.
        self.line_errors = line_errors or {}


@dataclass
class TransferResult:
    transfer: BranchStockTransfer
    replayed: bool = False  # True when an earlier identical submission was returned
    warnings: list = field(default_factory=list)


def parse_lines(batch_ids, quantities):
    """Turn two parallel POST lists into [(batch_id, qty)], rejecting malformed or duplicate rows."""
    if len(batch_ids) != len(quantities):
        raise TransferError('The transfer data is incomplete. Please refresh the page and try again.')
    lines, seen = [], set()
    for raw_id, raw_qty in zip(batch_ids, quantities):
        try:
            batch_id, qty = int(raw_id), int(raw_qty)
        except (TypeError, ValueError):
            raise TransferError('One of the cart lines has an invalid batch or quantity.')
        if batch_id in seen:
            raise TransferError('The same batch was added to the cart twice. Please remove one copy.', {batch_id: 'Duplicate'})
        seen.add(batch_id)
        lines.append((batch_id, qty))
    if not lines:
        raise TransferError('The cart is empty. Add at least one product before transferring.')
    if len(lines) > MAX_ITEMS_PER_TRANSFER:
        raise TransferError(f'A single transfer cannot exceed {MAX_ITEMS_PER_TRANSFER} items. Please split it into two transfers.')
    return lines


def normalize_key(raw):
    key = (raw or '').strip()
    if not _KEY_RE.match(key):
        raise TransferError('This transfer request has no valid identifier. Please refresh the page and try again.')
    return key


def _destination_product(src_product, destination, user, cache, line_batch_id=None):
    """Match the destination catalog item by name, or create a branch-local copy.

    Product names are UNIQUE per branch (``unique_together = (pharmacy, name)``), so the match
    is on name alone (case-insensitive). Matching on name + unit previously made a same-name,
    different-unit product look "missing"; creating the copy then violated the unique constraint
    and surfaced as a generic server error. A unit mismatch is now reported as a clear,
    line-level validation error, because stock in different units must never be merged silently.
    """
    if src_product.pk in cache:
        return cache[src_product.pk]
    dst = Product.objects.filter(pharmacy=destination, name__iexact=src_product.name).order_by('pk').first()
    if dst is not None:
        if (dst.unit or '').strip().lower() != (src_product.unit or '').strip().lower():
            raise TransferError(
                f'"{src_product.name}" already exists in {destination.name} with a different unit '
                f'({unit_label(dst.unit)} instead of {unit_label(src_product.unit)}). '
                'Align the unit on both branches, then try again.',
                {line_batch_id: 'Unit differs from destination branch.'} if line_batch_id else None,
            )
        if not dst.is_active:
            # Receiving stock into a deactivated catalog item would hide it from the POS.
            dst.is_active = True
            dst.save(update_fields=['is_active', 'updated_at'])
    else:
        dst_category = None
        if src_product.category_id:
            dst_category, _ = Category.objects.get_or_create(pharmacy=destination, name=src_product.category.name)
        try:
            with transaction.atomic():  # savepoint: a lost race must not poison the outer transaction
                dst = Product.objects.create(
                    pharmacy=destination, name=src_product.name, unit=src_product.unit, category=dst_category,
                    requires_expiry=src_product.requires_expiry, reorder_level=src_product.reorder_level,
                    discount_enabled=src_product.discount_enabled, discount_type=src_product.discount_type,
                    min_discount_amount=src_product.min_discount_amount, max_discount_amount=src_product.max_discount_amount,
                    min_discount_percent=src_product.min_discount_percent, max_discount_percent=src_product.max_discount_percent,
                    created_by=user, is_active=True,
                )
        except IntegrityError:
            # Another request created the same product between our lookup and insert: reuse it.
            dst = Product.objects.filter(pharmacy=destination, name__iexact=src_product.name).order_by('pk').first()
            if dst is None:
                raise
    cache[src_product.pk] = dst
    return dst


def execute_bulk_transfer(*, user, source, destination, lines, note='', idempotency_key, allowed_branch_ids):
    """Move every ``(batch_id, quantity)`` in ``lines`` from ``source`` to ``destination`` atomically.

    ``allowed_branch_ids`` is the set of branches the user may operate (server-side scope).
    Raises TransferError (nothing changed) or returns a TransferResult.
    """
    key = normalize_key(idempotency_key)
    if source.pk not in allowed_branch_ids or destination.pk not in allowed_branch_ids:
        raise TransferError('The selected branch is not one of the branches you manage.')
    if source.pk == destination.pk:
        raise TransferError('Source and destination branch cannot be the same.')
    note = (note or '').strip()[:255]

    # Fast path: this exact submission already completed (double click / refresh / retry).
    existing = BranchStockTransfer.objects.filter(idempotency_key=key).first()
    if existing:
        return _replay(existing, user, source, destination)

    try:
        with transaction.atomic():
            # Creating the header first claims the idempotency key: a concurrent duplicate
            # hits the UNIQUE constraint here and is turned into a replay below.
            transfer = BranchStockTransfer.objects.create(
                source_pharmacy=source, destination_pharmacy=destination, quantity=0, item_count=0,
                note=note, created_by=user, idempotency_key=key,
            )
            batch_ids = [b for b, _ in lines]
            locked = {
                # of=('self',): lock only the batch rows. Without it PostgreSQL rejects the query
                # ("FOR UPDATE cannot be applied to the nullable side of an outer join") because
                # Product.category is nullable and select_related turns it into a LEFT JOIN.
                # SQLite ignores row locks, so this only ever failed on production PostgreSQL.
                b.pk: b for b in ProductBatch.objects.select_for_update(of=('self',))
                .select_related('product', 'product__category')
                .filter(pk__in=batch_ids, product__pharmacy=source).order_by('pk')
            }

            errors = {}
            today = timezone.localdate()
            for batch_id, qty in lines:
                batch = locked.get(batch_id)
                if batch is None:
                    errors[batch_id] = 'This batch does not exist in the source branch.'
                elif qty < 1:
                    errors[batch_id] = 'Quantity must be at least 1.'
                elif qty > MAX_QUANTITY_PER_LINE:
                    errors[batch_id] = 'Quantity is too large.'
                elif batch.quantity < 1:
                    errors[batch_id] = 'This batch has no stock left.'
                elif qty > batch.quantity:
                    errors[batch_id] = f'Insufficient stock: {batch.quantity} available, {qty} requested.'
                elif batch.expiry_date is not None and batch.expiry_date < today:
                    errors[batch_id] = 'Expired batches cannot be transferred.'
            if errors:
                names = []
                for bid in list(errors)[:5]:
                    b = locked.get(bid)
                    names.append(f'{b.product.name} ({b.batch_number})' if b else f'batch #{bid}')
                more = '' if len(errors) <= 5 else f' and {len(errors) - 5} more'
                raise TransferError(
                    f'Transfer not completed. {len(errors)} item(s) have a problem: ' + ', '.join(names) + more + '. No stock was changed.',
                    errors,
                )

            dst_cache, items, movements, total = {}, [], [], 0
            stamp = f'{today:%Y%m%d}'
            for batch_id, qty in sorted(lines, key=lambda l: l[0]):
                batch = locked[batch_id]
                src_product = batch.product
                dst_product = _destination_product(src_product, destination, user, dst_cache, batch_id)
                batch.quantity -= qty
                batch.save(update_fields=['quantity'])
                dst_batch = ProductBatch.objects.create(
                    product=dst_product, batch_number=f'TR-{stamp}-{secrets.token_hex(4).upper()}',
                    purchase_unit='', purchase_quantity=qty, units_per_purchase_unit=1,
                    purchase_unit_price=batch.purchase_price, initial_quantity=qty, quantity=qty,
                    purchase_price=batch.purchase_price, selling_price=batch.selling_price,
                    expiry_date=batch.expiry_date, received_by=user,
                    note=f'Transfer {transfer.reference} from {source.name}; source batch {batch.batch_number}'[:255],
                )
                items.append(BranchStockTransferItem(
                    transfer=transfer, source_batch=batch, destination_batch=dst_batch,
                    product_name=src_product.name, unit=src_product.unit, source_batch_number=batch.batch_number or '',
                    quantity=qty, source_quantity_after=batch.quantity, purchase_price=batch.purchase_price,
                    selling_price=batch.selling_price, expiry_date=batch.expiry_date,
                ))
                movements.append(StockMovement(
                    product=src_product, batch=batch, movement_type=StockMovement.MovementType.TRANSFER_OUT,
                    quantity_change=-qty, note=f'{transfer.reference} → {destination.name}'[:255], performed_by=user,
                ))
                movements.append(StockMovement(
                    product=dst_product, batch=dst_batch, movement_type=StockMovement.MovementType.TRANSFER_IN,
                    quantity_change=qty, note=f'{transfer.reference} ← {source.name}'[:255], performed_by=user,
                ))
                total += qty

            BranchStockTransferItem.objects.bulk_create(items)
            StockMovement.objects.bulk_create(movements)
            transfer.quantity = total
            transfer.item_count = len(items)
            transfer.save(update_fields=['quantity', 'item_count'])
    except IntegrityError:
        # Lost the race on the idempotency key: another request already created this transfer.
        existing = BranchStockTransfer.objects.filter(idempotency_key=key).first()
        if existing:
            return _replay(existing, user, source, destination)
        raise
    return TransferResult(transfer=transfer)


def _replay(existing, user, source, destination):
    """Return an earlier submission, but never leak or reuse another user's/other branches' transfer."""
    if (existing.created_by_id != user.pk or existing.source_pharmacy_id != source.pk
            or existing.destination_pharmacy_id != destination.pk):
        raise TransferError('This request identifier has already been used. Please refresh the page to start a new transfer.')
    return TransferResult(transfer=existing, replayed=True)
