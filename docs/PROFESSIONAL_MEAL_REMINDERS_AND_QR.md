# Professional Meal Reminders + QR Attendance

## Daily flow
1. Employee scans the branch QR to check in.
2. During an eligible breakfast/lunch/dinner window, the same rotating QR becomes that meal checkpoint.
3. After the final configured meal window, the QR becomes the end-of-shift check-out.
4. Duplicate or out-of-window checkpoints are rejected.

## Accounting rule
- Monthly salary is a fixed reference amount.
- Earned meals are stored in `MealLedger` and are a branch operating cost.
- Meal cost is reflected in the sales/profit calculation.
- Meal cost and meal debt are never deducted from `User.monthly_salary`.
- Payroll gross/net excludes `earned_meals`; that field is informational only.

## Background reminders
The app creates durable in-app notifications and can send Web Push reminders through VAPID. This works when the employee has granted browser notification permission and the browser/OS permits background notifications, including when the app tab is closed or the user is logged out.

Set these Railway variables:
- `WEBPUSH_VAPID_PUBLIC_KEY`
- `WEBPUSH_VAPID_PRIVATE_KEY`
- `WEBPUSH_VAPID_CLAIMS_EMAIL` (for example `mailto:admin@yourdomain.com`)

The web process already runs Celery Beat through `start.sh`; the reminder task runs once per minute. Redis must be configured for production Celery scheduling.

## Timing settings
Managers can configure breakfast, lunch and dinner start/end times plus an optional reminder lead time. Example:
- Breakfast: 07:00–10:00
- Lunch: 12:00–15:00
- Dinner: 18:00–21:00
- Reminder lead: 0 minutes (at start)

The browser/OS controls final notification sound. When the app is open, the existing SmartPOS bell/chime also provides foreground feedback.
