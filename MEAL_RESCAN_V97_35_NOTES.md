# V97.35 - Meal rescan request, boss approval and credit, chip-based request form (complete)

## Employee
- Request form (rescan and "report a problem") now uses reason chips: Busy at work / Network problem /
  Device problem / Other. The explanation box appears only for "Other"; the button stays disabled until a
  reason is chosen (and 3+ characters for Other). Touch-friendly chips, 16px inputs (no iOS zoom), full-width button.
- Same form in the Meals card and in the Action column of Today's Meal Status (templates/dashboard/_meal_request_form.html).
- Server: details are required only for "Other"; the boss sees "reason chosen from the list" otherwise.
- QR scan passing the workplace GPS check re-verifies attendance stuck in REVIEW; REVIEW employees can
  Verify Presence, scan (GPS branches), report a problem or request a rescan.

## Boss / manager
- "Approve & credit meal": records the meal as if scanned in time (audited). Normal accounting applies:
  EARNED when the employee's sales fund it, otherwise meal DEBT that clears on the next sale. Salary untouched.
- Alternatives kept: Reopen for scan, Verify directly, Return, Reject.
- Each request shows the employee's attendance state with safe-action guidance.

## Files
accounts/attendance_services.py, accounts/meal_services.py, accounts/views.py, dashboard/views.py,
templates/dashboard/employee_dashboard.html, templates/dashboard/_meal_request_form.html (new),
templates/accounts/meal_issues_review.html, accounts/tests/test_meal_rescan_review.py. No migrations.
