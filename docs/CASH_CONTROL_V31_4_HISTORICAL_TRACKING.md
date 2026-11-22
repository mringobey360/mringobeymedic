# V31.4 — Historical Cash Tracking + Automatic Reconciliation

## Purpose
Cash Control now reads the source ledgers directly so cash activity from previous days is visible even when a `CashDay` record was never opened for that date.

## Rules
1. Cash sales come from completed/partially-returned/voided cash sales using the original sale amount; approved cash refunds are shown separately.
2. Approved expenses paid from sales cash are shown separately.
3. Historical activity is **not** treated as a cash opening balance.
4. A physical opening/closing anchor is required before a new branch can form a verified carry-forward chain.
5. Once a verified CashDay exists, automatic closing may use the existing V31.3 exception rules and a verified closing carries to the next day.
6. Historical activity is read-only/source-derived, so it cannot silently mutate sales or accounting records.

## Why this is safer
Monthly sales or yesterday's sales cannot safely be converted into opening cash without knowing the actual physical balance. V31.4 therefore shows yesterday's cash sales automatically while keeping the cash-balance chain auditable.

## Automation
Celery Beat continues to run the cash automation every five minutes. The automation also scans the last 90 days of source cash activity so a new branch has a visible, trackable cash history immediately.

## Database
No new database migration is required for V31.4. It is compatible with the existing V31.3 CashDay fields.
