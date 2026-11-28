# V97.36 - Employee expenses auto-deduct from the drawer; boss may reject at any time

## Behaviour
- Employee saves a SALES-CASH expense -> it leaves the drawer immediately (status APPROVED, auto_recorded=True,
  cash_out_at = exact moment, cash_date = today). Boss is notified (WARNING) with a link to review.
- Boss can Confirm (no money change) or Reject, at any time, however old. Rejection needs a reason.
- Rejection never rewrites the original day (closed days stay frozen). A reversal is recorded on the day of the
  rejection (Expense.reversed_at): today's expected cash rises by the amount. Status becomes REJECTED, so the expense
  also drops out of profit/reports. Same pattern as meal reversals.
- Money movement: ledger shows the original "- TZS x" line and a separate "+ TZS x Reversal" line; the expense detail
  page has a "Money movement" timeline; list shows "Deducted - to review / confirmed / Rejected - returned".
- Mobile money / bank / other expenses from employees are unchanged (still need boss approval).
- Boss/supervisor-entered expenses are unchanged (authorized immediately).

## Files
expenses/models.py, migrations/0024_expense_auto_record.py, ledger.py, services.py, views.py, urls.py, tests.py,
templates/expenses/detail.html, list.html, pharmacy_system/context_processors.py (boss badge counts "to review").
Migration: python manage.py migrate
Tests: python manage.py test expenses   (NOT run in the build sandbox - Django unavailable there)

# V97.37 - one expected-cash statement for employee and boss
- Per-employee statement: Total sales -> not cash -> Cash sales -> refunds -> meals -> expenses -> = Expected cash.
- Single partial templates/dashboard/_cash_statement.html drives the employee dashboard; the boss's "Cash by employee"
  table and the history page use the same ledger function (expenses/ledger.py employee_cash_reference), so figures match.
- Employee expenses (auto-recorded) reduce the employee's expected cash; a boss rejection gives it back that day.

# V97.38 - report consistency
Rule: DRAWER figures always come from expenses/ledger.py (CashLedgerService); PROFIT figures follow expense_date and
count APPROVED expenses only (a rejected expense leaves profit automatically).
- daily_reports/services.py: drawer expenses now taken from the ledger (was expense_date based, so an expense entered
  today for an earlier date, and any rejection reversal, were missing from the daily report's cash figure).
- dashboard/perf_cash.py: expense list now shows a rejected-later expense as two drawer lines (cash out + negative
  reversal), so the list always adds up to the Expenses column of the expected-cash table.
- dashboard/reconciliation.py: new checks EXPENSE_UNREVIEWED_OLD and LEDGER_ITEMISED_MISMATCH (itemised movements must
  equal the day's total).
- tests: ReportConsistencyTests.

# V97.39 - exact drawer statement for any date range (Reports > custom report)
New card "Expected Cash, Day by Day" on templates/dashboard/custom_reports.html:
per day Opening, Total sales, Cash sales, - Meals, Cash after meals, + Debt/layaway cash, + Mobile cashed out,
- Refunds, - Expenses (shows what was returned after a rejection), - Cash to mobile, Expected cash, Counted, Difference;
PERIOD TOTAL row; a "How the total is calculated" waterfall; and 5 consistency checks (all must tick) with a
"Reconciled" badge. Same ledger as Cash Control, daily report and exports. No migration.
Files: dashboard/range_report.py (extra keys in expected_cash_by_day), dashboard/views.py (custom_reports_view),
templates/dashboard/custom_reports.html, expenses/tests.py (DrawerStatementTests).
