# SmartPOS Layaway V1 — implementation notes

## Included
- Branch-scoped Layaway plans and payments with unique references.
- Initial deposit recorded as a payment, payment history, balance calculation and audit events.
- Search/filter/list/detail/create screens, POS shortcut, manager review/cancellation state.
- Soft reservation: no stock quantity is reduced and a Layaway does not lock stock.
- Payment rows are protected against duplicate database identity through a unique idempotency UUID.

## Important integration boundary
This is a first usable management module, not a completed accounting/checkout integration. It does **not yet** automatically create a Sale, update the existing cash ledger, send Africa's Talking SMS, generate a sales receipt URL, or show reservation labels/warnings inside product search and checkout. Do not deploy as a production-ready end-to-end Layaway feature until those integrations are completed and tested. Full payment currently changes the plan status to `COMPLETED` in the V1 code; because official sale/hand-over is not yet automated, staff must not interpret that status as proof a Sale or receipt exists. This limitation should be addressed before production use.

## Deploy
1. Back up production database.
2. Deploy code.
3. Run `python manage.py migrate`.
4. Run `python manage.py check` and test a dedicated branch with demo products.
5. Validate branch isolation, payment totals, concurrent payments, cash ledger, stock, SMS and receipt behavior before enabling real customer use.
