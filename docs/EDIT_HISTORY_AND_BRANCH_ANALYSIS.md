# Edit history (audit trail) + branch analysis

## Deploy
Run `python manage.py migrate` (adds `inventory.0026_inventorychangelog`). Nothing else to configure.

## Edit history (Inventory -> Edit History)
Records, per branch: product registered / edited / removed / permanently deleted / merged, batch added / edited,
and bulk-upload changes to existing products or batches. Each row stores who, when, IP, the reason typed on the
edit form, and old -> new value of every changed field. Rows are append-only (no edit/delete in the app or admin).
Per-product view: Products -> Batches -> Edit History.
Not logged on purpose: sales, and brand-new batch rows created by Excel upload (see Restock History).

## Branch analysis (Intelligence Report, 2+ branches)
Ranks branches by sales with share of group sales, profit, margin, change vs previous day, debt, and plain flags
(loss, margin below group average, heavy credit, high returns, heavy expenses, sales drop). Old saved reports
without this data still open with the previous simple branch table.

## Employees no longer see expected cash (v97.42)
Removed the "Your expected cash today" card and the "Cash History" button from the employee dashboard, and
`dashboard:my_cash_history` now answers employees with 403. Management still opens it per employee
(`?employee=<id>` from Cash Control). All cash-control screens were already management-only.
