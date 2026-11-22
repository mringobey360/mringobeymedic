# Layaway reporting reconciliation

## Accounting rules
- Layaway payments are cash-flow receipts on `LayawayPayment.received_at`, grouped by the actual payment method.
- Layaway refunds are cash-flow outflows on `LayawayRefund.refunded_at`, grouped by the actual refund method.
- A linked handover `Sale` remains part of sales/revenue and inventory fulfilment, but its tender is not a new receipt. Its instalments were counted when received, so the handover sale is excluded from cash/mobile receipt calculations.
- Cash instalments and cash refunds are included in the canonical cash ledger. Mobile-money instalments and refunds are included in the mobile-money account movement.
- Layaway detail is disclosed separately in the Advanced Report Builder, range PDF/Excel, daily sales intelligence HTML/PDF, dashboard daily financial summary, and mobile-money reconciliation. The detail is explanatory and is not added a second time to cash/mobile totals.

## Validation
Python source syntax was checked. Full Django checks and database-backed tests still need to be run in the project's deployment environment because Django is not installed in this packaging environment.
