# Layaway stock reservation and POS override policy

- Layaway is a soft reservation: stock is protected from ordinary POS sales while a plan remains open.
- POS can sell unreserved sellable stock normally.
- Only a shop owner/manager or authorised manager assistant can approve a sale that consumes units reserved by an unpaid ACTIVE Layaway plan. A reason (at least 8 characters) is mandatory.
- The override is online-only and runs in the same database transaction as checkout. If checkout fails, reservation changes roll back.
- Affected unpaid plans move to NEEDS_RESOLUTION, their reservation is released, and audit events record the actor, reason, payment position and POS sale. Management must contact the customer and agree to restock, amend the plan, or refund according to shop policy.
- Fully-paid PAID_PENDING_HANDOVER plans and other non-ACTIVE plans cannot be overridden. If protected reservations would be consumed, checkout is refused.
- A manager override cannot sell more physical sellable stock than exists; expired stock is excluded.
- Override is not queued for offline synchronisation because permission, reservations and stock must be revalidated live.
- Layaway payments are not re-collected at POS sale/handover; the existing cash ledger remains the source of drawer cash received at instalment time.
