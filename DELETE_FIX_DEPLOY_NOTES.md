# SmartPOS V62 — Marketplace seller delete repair

## Changes
- Adds `Product.archived_at` with migration `0014_product_archived_at`.
- Seller Delete now atomically archives a listing instead of deleting order history or paid digital files.
- Seller dashboard, seller products, public catalogue and public shop exclude archived products.
- Server logs the full traceback if archiving fails; the user gets a friendly message.
- Delete remains POST-only, CSRF protected and seller-store scoped.

## Production procedure
1. Back up Railway PostgreSQL and object storage; stage changes before deploying.
2. Deploy code with migration 0014 (which depends on 0013).
3. Run `python manage.py migrate --noinput` and `python manage.py check`.
4. Test delete on a test physical item and a test PDF item, then verify existing paid downloads and order history.
5. If errors persist, inspect deploy logs for `Marketplace seller product archive failed` and its traceback.

## Caveats
- `archived_at` is intentionally a new field. No existing rows are deleted.
- A failed delete can still indicate another production-only schema mismatch. Django and PostgreSQL are unavailable in the authoring environment, so runtime tests and migration execution have not been performed here.
- Existing model/migration drift warnings in unrelated apps are not fixed by this targeted patch. Do not generate all migrations blindly in production.
