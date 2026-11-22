# V30.27 — Weekly Meal Policy as Single Source of Truth

## Changes

- Removed the legacy **Weekend Meal Schedule (Sat & Sun)** field from the Shop Owner Salary & Meals form.
- `WeeklyMealSchedule` is now the single source of truth for normal weekly meal entitlement.
- Specific-date `MealAdjustment` remains the only supported exception mechanism.
- Existing employees missing any weekday schedule are backfilled safely during migration using their legacy weekend setting.
- New employees receive an explicit seven-day policy at account creation: Monday-Friday all three meals; Saturday-Sunday lunch only.
- Meal calculation no longer silently falls back to the deprecated weekend field. Missing policy data produces zero entitlement rather than an accidental meal charge.
- Meal allowance wording now clearly describes meals as operating costs, not reductions to sales.
- The legacy database field remains temporarily for backward-compatible data/export safety, but is not exposed in the user interface and is not used for new calculations.

## Policy resolution

1. Specific-date override, if present.
2. Weekly seven-day policy.
3. No entitlement if neither exists.

The legacy weekend field is not part of runtime entitlement calculation.
