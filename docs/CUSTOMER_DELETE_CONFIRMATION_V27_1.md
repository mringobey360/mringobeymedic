# V27.1 — Customer Delete Confirmation

- Replaced the browser `confirm()` prompt on Customer Registry deletion with the system-wide professional confirmation modal.
- Uses the existing `data-confirm` / `data-confirm-detail` mechanism in `static/js/main.js`.
- Confirmation shows the customer name and explains that campaign history is preserved.
- Cancel leaves the customer untouched.
- Confirm submits the existing POST deletion endpoint with CSRF protection.
- No database migration is required.
