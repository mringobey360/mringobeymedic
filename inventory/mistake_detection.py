from decimal import Decimal
from difflib import SequenceMatcher

from notifications.models import Notification
from notifications.utils import notify_many
from django.urls import reverse

SUSPICIOUS_MARKUP_MULTIPLIER = Decimal('10')
SIMILAR_NAME_THRESHOLD = 0.82


def _normalize_name(name):
    return ' '.join(name.strip().lower().split())


def find_similar_products(pharmacy, name, exclude_pk=None):
    """
    Looks for existing active products at this branch whose name is a close
    (but not exact) match to `name` — e.g. 'Panadol 500mg' vs 'Panadol500 mg'
    or a typo like 'Panadoll'. Exact matches are already blocked separately
    by the unique constraint / form validation, so this only catches the
    near-misses that would otherwise silently split one medicine's stock
    and sales history across two catalog entries.
    """
    from .models import Product  # local import avoids a circular import at module load

    target = _normalize_name(name)
    candidates = Product.objects.filter(pharmacy=pharmacy, is_active=True)
    if exclude_pk:
        candidates = candidates.exclude(pk=exclude_pk)

    matches = []
    for candidate in candidates.only('id', 'name'):
        other = _normalize_name(candidate.name)
        if other == target:
            continue  # exact match — handled elsewhere, not "similar"
        ratio = SequenceMatcher(None, target, other).ratio()
        if ratio >= SIMILAR_NAME_THRESHOLD:
            matches.append(candidate)
    return matches


def flag_if_similar_product_exists(product, user):
    """Non-blocking nudge: warns the branch's managers that a newly added
    product looks like it might be a near-duplicate of one already in the
    catalog, so stock/sales don't end up split across two entries by
    mistake (e.g. different category, slightly different spelling)."""
    similar = find_similar_products(product.pharmacy, product.name, exclude_pk=product.pk)
    if not similar:
        return
    names = ", ".join(f"'{p.name}'" for p in similar[:3])
    notify_many(
        product.pharmacy.managers.all(),
        title=f"Possible Duplicate Product: {product.name}",
        message=(
            f"'{product.name}' (added by {user.get_full_name() or user.username}) looks similar to "
            f"existing product(s) {names}. If this is the same medicine, please merge/remove the duplicate "
            f"and move its stock to the correct product to keep sales history accurate."
        ),
        category=Notification.Category.STOCK_ALERT, level=Notification.Level.WARNING, pharmacy=product.pharmacy,
        link_url=reverse('inventory:product_merge', args=[product.pk]) + f'?target={similar[0].pk}',
    )


def check_batch_for_issues(batch):
    """
    Returns a list of (level, message) tuples for anything about this batch's
    pricing that looks like a likely data-entry mistake (swapped buy/sell
    price, a missing decimal point, etc). This never blocks saving the batch
    — it's a nudge for the admin to double-check, since an unusual price can
    occasionally be intentional (e.g. a clearance sale).
    """
    product = batch.product
    purchase = Decimal(batch.purchase_price)
    selling = Decimal(batch.selling_price)
    issues = []

    if selling < purchase:
        issues.append((
            Notification.Level.DANGER,
            f"Selling price (TZS {selling:,.0f}) is BELOW the purchase price (TZS {purchase:,.0f}) "
            f"for '{product.name}'. Selling at this price would cause a loss — open this product's 'Batches' "
            f"and correct the price before selling."
        ))
    elif selling == purchase:
        issues.append((
            Notification.Level.WARNING,
            f"Selling price and purchase price are equal (TZS {selling:,.0f}) for '{product.name}' — there is no "
            f"profit on this batch. Please confirm that this is intentional."
        ))
    elif purchase > 0 and selling >= purchase * SUSPICIOUS_MARKUP_MULTIPLIER:
        issues.append((
            Notification.Level.WARNING,
            f"Selling price (TZS {selling:,.0f}) is more than 10 times the purchase price (TZS {purchase:,.0f}) "
            f"for '{product.name}'. Check that an extra digit was not entered by mistake (e.g. 5000 instead of 50000)."
        ))

    return issues


def flag_batch_if_suspicious(batch):
    """Runs check_batch_for_issues() and immediately notifies the pharmacy's admin(s) if anything was found."""
    issues = check_batch_for_issues(batch)
    for level, message in issues:
        notify_many(
            batch.product.pharmacy.managers.all(),
            title=f"Kagua Stock: {batch.product.name}", message=message,
            category=Notification.Category.STOCK_ALERT, level=level, pharmacy=batch.product.pharmacy,
        )
