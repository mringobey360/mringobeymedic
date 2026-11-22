# V97.34 - Meal rescan request, boss approval and credit (complete)

## Employee
- Missed meal row shows "Request rescan" in the Action column: reason + short explanation, sent to the boss.
- Row shows request reference/time while waiting; weekly request limit shown; approved meals move to Meal History.
- QR scan that passes the workplace GPS check re-verifies attendance stuck in REVIEW; REVIEW employees can
  Verify Presence, scan (GPS branches), report a problem or request a rescan.

## Boss / manager
- "Approve & credit meal": records the meal as if scanned in time (audited, name + time). Normal accounting
  applies: EARNED when the employee's sales fund it, otherwise meal DEBT that clears on the next sale.
  Salary is never touched.
- Alternatives kept: Reopen for scan, Verify directly (with reason), Return, Reject.
- Each request shows the employee's attendance state (verified / needs review) and safe-action guidance.
- Direct verify and reopen work for checked-in employees in REVIEW; REJECTED / not checked in are refused.
- Bulk outage verification still covers verified attendance only.

## Files
accounts/attendance_services.py, accounts/meal_services.py, accounts/views.py, dashboard/views.py,
templates/dashboard/employee_dashboard.html, templates/accounts/meal_issues_review.html,
accounts/tests/test_meal_rescan_review.py. No migrations.
