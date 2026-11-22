# SmartPOS Marketplace — Delete button submits the selected action

## Root cause
The confirmation dialog intercepted a form submit and replayed it with `form.requestSubmit()` without passing the original submit button. This drops the button's `name=value` pair (for example `action=delete`), so the Django view receives an empty action and does not archive the product.

## Fix
- Preserve `event.submitter` and replay using `form.requestSubmit(submitter)`.
- Remove duplicate inclusion of `mk-dialog.js` from `_mk_styles.html`; the Marketplace base template already includes it.
- Bump the script query version to force browsers to fetch the updated JavaScript.

## Deploy and test
1. Back up the database.
2. Deploy this project to Railway. No database migration is required for this JavaScript fix (the previous `0014_product_archived_at` migration is already applied in the supplied logs).
3. Hard-refresh the browser (Ctrl+F5).
4. Delete a test product, confirm the dialog, and verify it disappears from the seller product list and public shop.
5. Verify order history and any paid digital download remain available.
