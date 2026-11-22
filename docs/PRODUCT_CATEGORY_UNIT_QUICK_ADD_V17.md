# SmartPOS V17 — Product Category and Unit Quick Add

## What changed
- Product registration and product editing keep the existing inline `+ Add new category` control.
- A new inline `+ Add new unit` control persists custom units in a branch-scoped catalog.
- Custom units are reusable in later product forms and purchase/stock-batch forms, even if the user first added them before completing the product form.
- Standard units (Tablet, Bottle, Box, etc.) continue to use their canonical values and are not duplicated in the custom catalog.
- Unit and category creation endpoints require an authenticated manager/authorized admin and use the active branch; data from one branch is not exposed to another.
- Case-insensitive duplicate unit names are reused. Validation restricts names to 30 characters and a safe set of characters.
- Existing product and batch unit fields remain text fields, so the migration does not rewrite existing inventory, sales, or stock records.

## Deployment
1. Back up the production database.
2. Deploy this project ZIP.
3. Run `python manage.py migrate` to create the `UnitCatalog` table.
4. Verify in staging: add a category, add a custom unit (e.g. `Crate of 24`), save a product, then confirm the same unit is available in a second product and a stock-receiving form.
5. Confirm one branch cannot see or reuse another branch's custom unit catalog.

## Validation status
Python syntax compilation and ZIP integrity can be checked during packaging. Django migrations, view tests, browser interactions and production deployment must be tested in the configured Django/Railway environment before treating this update as production-verified.
