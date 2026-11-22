# Late entry sales (paper-notebook sales typed in later)

A sale that really happened yesterday (written in the notebook) can be typed in today and
still be counted on **yesterday**.

* POS -> "Sale made on an earlier day?" -> tick, choose the date (yesterday .. `LATE_ENTRY_MAX_DAYS`
  days back, default 3), choose the reason, optional notebook page / receipt no.
* `Sale.business_date` = the real day -> reports, dashboard, cash drawer and cash control use it.
* `Sale.created_at` = when it was typed in -> kept as the audit trail (shown as "entered ...").
* Stock is reduced at entry time; the stock movement says `[Late entry for 20 Nov 2026]`.
* Cash / Cash+Mobile (and credit with a cash deposit) go into THAT day's cash drawer. Blocked if
  that day is already physically CLOSED (owner reopens it first) or has no cash-control record.
* Employee late entries appear in **Sales needing review** for the owner; owner/manager entries
  are trusted. The sale list shows a "Late entry" badge.
* Never future dates, never older than `LATE_ENTRY_MAX_DAYS` (set to 0 to disable). Needs a live
  connection (never queued offline).

## What if that day's cash is already CLOSED?

| Day status | Cash / Cash+Mobile / credit with cash deposit | Mobile Money / credit without cash |
|---|---|---|
| OPEN, DAY_ENDED | joins that day's drawer immediately | joins immediately |
| CLOSED (verified) | **Owner/manager only**, after an explicit confirmation | allowed (no drawer impact) |
| no cash record | blocked - owner checks Cash Control | allowed |

On a CLOSED day the verified physical count is never rewritten. The sale is posted on its real
date, becomes *post-close activity* for that day (Cash Control banner), an immutable audit row is
written, and the sale stays in **Sales needing review** until the owner uses
**Reopen & Recount** for that day (existing audited workflow): recount the drawer, re-close, and
today's opening cash is re-based. Employees cannot post cash onto a closed day; they are told to
ask the owner (or to use Mobile/Credit if that is how it was paid).

## Guided reconcile flow (owner)

Sales -> Needs Review -> "Late sales on closed cash days" (`/sales/late-entries/`).

1. **Reopen & Recount** the closed day (audited, reason required). Shows original count, expected at
   close, expected now and the cash posted after close.
2. **Close the day again.**
3. Done: the late sales resolve themselves automatically (as soon as the day is CLOSED with no cash
   activity after its new close) and the next day's opening cash is re-based by the existing
   carry-forward logic. Nothing is overwritten silently.

Owner alerts (in-app): an employee's late entry ("Late entry to verify"), an employee blocked by a
closed day ("Late cash sale needs the owner"), and a posted closed-day correction
("Reconcile a closed cash day").
