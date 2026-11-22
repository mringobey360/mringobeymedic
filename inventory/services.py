"""Single source of truth for registering a product and receiving stock.

Used by the normal pages (product_create_view, batch_add_view) AND the scan
register / restock sheet, so every path applies the same rules, writes the same
StockMovement audit rows, runs the same mistake detection and sends the same
manager notifications.
"""
from django.db import transaction

from notifications.models import Notification
from notifications.utils import notify_many

from .mistake_detection import flag_batch_if_suspicious, flag_if_similar_product_exists
from .barcode_lookup import remember_registered_name
from .models import StockMovement


@transaction.atomic
def register_product(user, pharmacy, product_form, batch_form):
    """Save a validated ProductForm + opening ProductBatchForm. Returns (product, batch)."""
    product = product_form.save(commit=False)
    product.pharmacy = pharmacy
    product.created_by = user
    product.save()
    flag_if_similar_product_exists(product, user)

    batch = batch_form.save(commit=False)
    batch.product = product
    batch.received_by = user
    batch.prepare_packaging_cost()
    batch.save()

    StockMovement.objects.create(
        product=product, batch=batch, movement_type=StockMovement.MovementType.NEW_BATCH,
        quantity_change=batch.quantity, note="Initial batch on registration", performed_by=user,
    )
    remember_registered_name(product.barcode, product.name)   # next scan of this pack fills the name
    notify_many(
        product.pharmacy.managers.all(),
        title="New Product Registered",
        message=f"'{product.name}' registered with {batch.quantity} {product.get_unit_display()} opening stock "
                f"by {user.get_full_name() or user.username}.",
        category=Notification.Category.STOCK_ALERT, pharmacy=product.pharmacy,
    )
    return product, batch


@transaction.atomic
def receive_batch(user, product, batch_form):
    """Restock: always a NEW batch row (never merged), fully traceable. Returns the batch."""
    batch = batch_form.save(commit=False)
    batch.product = product
    batch.received_by = user
    batch.prepare_packaging_cost()
    batch.save()
    StockMovement.objects.create(
        product=product, batch=batch, movement_type=StockMovement.MovementType.NEW_BATCH,
        quantity_change=batch.quantity, note=batch.note or "New batch received", performed_by=user,
    )
    flag_batch_if_suspicious(batch)
    notify_many(
        product.pharmacy.managers.all(),
        title="New Batch Added",
        message=f"{batch.quantity} {product.get_unit_display()} of '{product.name}' added by "
                f"{user.get_full_name() or user.username}.",
        category=Notification.Category.STOCK_ALERT, pharmacy=product.pharmacy,
    )
    return batch
