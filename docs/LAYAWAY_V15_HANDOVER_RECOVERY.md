# SmartPOS Layaway V15 — Handover recovery and stock protection

## What changed

- The plan detail page no longer silently removes the handover action when stock is unavailable. It shows a disabled `Handover unavailable` action and explains the reason.
- A fully paid plan with units still due displays the exact stock state and keeps the balance at TZS 0. The customer must not be charged again.
- The detail page links to the product's batch/stock history. Managers also receive a direct link to the approved Add Batch workflow.
- Handover availability is calculated against sellable stock after protecting reservations belonging to other open customer plans.
- The handover service repeats the reservation check inside its transaction. If stock is short or reserved for another customer, it creates no sale, stock movement, handover, or new payment.
- Once stock is correctly received/adjusted, refresh the Layaway plan. The available handover quantity is recalculated and the normal partial/full handover action becomes available.
- Added a regression test for not consuming another active customer's reserved stock.

## Resolve the displayed Jummo case

For `LAY-20261022-EB2978`, the recorded position remains 5 of 6 units handed over, TZS 30,000 paid, TZS 0 outstanding, and 1 unit pending. The new UI directs staff to stock batches and explains why handover is blocked. A manager must verify physical stock and receive a valid batch or correct a proven stock-entry error. Do not create a second payment, fake a handover, or set the plan to Completed manually.

## Deployment

1. Back up the database and current deployed source.
2. Deploy the full V15 source ZIP.
3. No model changes are included in this V15 patch, so no new migration is expected.
4. Test in staging: no stock; one sellable unit; stock reserved by another plan; expired-only batch; successful final handover; duplicate submission.
5. Only after verification deploy to production and inspect Railway logs.

## Validation note

Python syntax compilation and ZIP integrity were checked in the build environment. Django runtime checks and the test suite could not be executed because Django is not installed in that environment. Run `python manage.py test layaway` in the project's configured environment before production deployment.
