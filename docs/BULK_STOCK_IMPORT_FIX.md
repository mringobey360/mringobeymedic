# Bulk Stock Import Fix

## What changed

1. **Soft-deleted products are reactivated**
   - Uploading an existing product whose `is_active=False` reactivates the original product row.
   - The importer no longer attempts to create a duplicate `(pharmacy, name)` product.
   - Sales/history remain attached to the original product.

2. **Opening Stock and Restock are now different operations**
   - `Opening Stock`: always creates a new batch/receipt row. It never tops up an existing batch.
   - `Restock / Purchase`: tops up only an exact matching live batch.
   - A batch number such as `OPENING` can safely be used for many different products.

3. **Batch identity is no longer batch-number-only**
   Restock matching uses product + batch number + expiry + base purchase cost + selling price + purchase unit + conversion.
   This prevents an unrelated row from changing another lot.

4. **Import audit trail**
   - Added `StockImport` with a unique reference, mode, filename, row count, user and counters.
   - Every newly created batch from a bulk import is linked to its import reference.
   - Stock movements include the import reference and Excel row number.

5. **Validate before commit**
   - The workbook is parsed and validated before stock is written.
   - Exact duplicate rows in the same Excel file are rejected.
   - If validation or an unexpected database error occurs, the import is not committed.
   - Database writes use `transaction.atomic()`.

6. **WAC/profit safety**
   - Different purchase costs are not merged into one batch.
   - Restock can only top up an exact-cost matching batch; otherwise a new batch is created.
   - Existing FEFO selling and batch-level profit logic remain intact.

## Migration

Run:

```bash
python manage.py migrate
```

The new migration is:

`inventory/migrations/0002_stockimport_productbatch_stock_import.py`

## Recommended workflow

- First stock loading: choose **Opening Stock**.
- New supplier delivery: choose **Restock / Purchase**.
- If the same product has a new expiry or new purchase cost, it will become a new batch automatically.
- Do not use a generic batch number as a global stock identifier; batch identity is product-specific.
