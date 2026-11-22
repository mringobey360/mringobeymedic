# Release V19 — Professional Inventory & Restock

## Inventory identity
- Every product now has a stable, branch-scoped SKU.
- SKU is generated automatically when left blank (`SKU-XXXXXXXXXX`).
- Existing products keep their SKU across archive/restore.
- Barcode remains automatically generated when blank.
- Product search covers name, SKU, barcode and batch number.

## Purchase / Restock workflow
- New **Restock / Purchase Template** endpoint exports a live snapshot of the current branch catalog.
- Template includes product name, SKU, barcode, category, base unit, current stock, reorder level, suggested purchase quantity, purchase unit, conversion, purchase quantity, purchase price, selling price, batch and expiry.
- `purchase_quantity` is intentionally blank: it is the incoming purchase quantity to be entered by the user.
- `suggested_purchase_qty = max(reorder_level - current_stock, 0)` is informational only.
- Downloads support all products, low-stock-only, or a selected category.
- Restock imports require an existing SKU, preventing accidental creation of new products through the purchase workflow.
- Carton/box quantities are converted to base units (`purchase_quantity × units_per_purchase_unit`).
- Partial cartons should be recorded as base units (e.g. 7 bottles), not fractional cartons.

## Duplicate protection
- Exact repeated Excel uploads are blocked using a SHA-256 file fingerprint.
- Opening-stock imports are idempotent for an unchanged existing opening batch: the existing batch is safely reused instead of adding stock again.
- Restock only tops up a live batch when the complete lot identity matches: product, batch, expiry, cost, selling price and packaging conversion.
- Otherwise a separate batch is created.
- All database writes remain atomic: validation failure or unexpected error rolls back the whole import.

## Security
- Production HSTS preload defaults to enabled.
- Local development fallback secret is longer than Django's weak-secret threshold.
- Production still requires a real `SECRET_KEY` environment variable.

## Deployment
Run migrations after deploying:

```bash
python manage.py migrate
```

Then restart the Railway service.
