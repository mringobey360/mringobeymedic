"""Admin-only hard delete of inventory products.

A product is referenced by sale lines, layaway plans and branch-transfer lines through PROTECT
foreign keys, so Django refuses to delete it. For a *superuser clearing test/wrong data* from the
Django admin we remove those dependent rows explicitly (in a safe order, in one transaction) and then
delete the product itself. If anything else still protects the product, the whole thing rolls back.

The normal app screens keep archiving products that have history; this is only used by the admin.
"""
from django.db import transaction


def _dependents(product):
    from inventory.models import BranchStockTransfer, BranchStockTransferItem
    from layaway.models import LayawayPlan
    from sales.models import SaleItem

    batch_ids = list(product.batches.values_list('pk', flat=True))
    sale_items = SaleItem.objects.filter(product=product) | SaleItem.objects.filter(batch_id__in=batch_ids)
    plans = LayawayPlan.objects.filter(product=product)
    transfer_items = BranchStockTransferItem.objects.filter(source_batch_id__in=batch_ids) | \
        BranchStockTransferItem.objects.filter(destination_batch_id__in=batch_ids)
    transfers = BranchStockTransfer.objects.filter(source_batch_id__in=batch_ids) | \
        BranchStockTransfer.objects.filter(destination_batch_id__in=batch_ids)
    return sale_items.distinct(), plans, transfer_items.distinct(), transfers.distinct()


def dependency_counts(product):
    sale_items, plans, transfer_items, transfers = _dependents(product)
    return {
        'sale_items': sale_items.count(),
        'layaway_plans': plans.count(),
        'transfer_lines': transfer_items.count() + transfers.count(),
    }


@transaction.atomic
def purge_product(product):
    """Delete `product` together with the history rows that would otherwise block it."""
    from layaway.models import LayawayHandover, LayawayPayment, LayawayRefund
    from sales.models import Sale, SaleItem

    sale_items, plans, transfer_items, transfers = _dependents(product)

    # 1. Layaway plans: their payments/refunds/handovers are PROTECT children of the plan.
    for plan in list(plans):
        LayawayHandover.objects.filter(plan=plan).delete()
        LayawayPayment.objects.filter(plan=plan).delete()
        LayawayRefund.objects.filter(plan=plan).delete()
        plan.delete()  # audit events and SMS logs cascade

    # 2. Sale lines of this product; then drop sales that are left with no lines at all.
    sale_ids = set(sale_items.values_list('sale_id', flat=True))
    SaleItem.objects.filter(pk__in=list(sale_items.values_list('pk', flat=True))).delete()
    empty = Sale.objects.filter(pk__in=sale_ids).exclude(pk__in=SaleItem.objects.filter(sale_id__in=sale_ids).values('sale_id'))
    LayawayHandover.objects.filter(sale__in=empty).delete()
    empty.delete()  # debts and return requests cascade

    # 3. Branch-transfer lines that point at this product's batches.
    transfer_items.delete()
    transfers.delete()

    # 4. The product itself (batches and stock movements cascade).
    product.delete()
