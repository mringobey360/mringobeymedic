# Professional Meal Ledger & Debt Rules

## Salary

Monthly salary is fixed and is never reduced, increased, or otherwise changed by meal records, sales, meal debt, or the daily meal calculation.

## Meal entitlement

An employee's weekly 7-day policy or a specific-date override decides which meals are eligible.

## Workday protection

Employees start their workday. A first sale can also establish the workday automatically. A meal whose cut-off has already passed before the employee started is not eligible.

Example: employee starts at 12:00 and breakfast cut-off is 10:00 -> breakfast is not recorded and no breakfast debt is created.

## Sales funding

Meal funding is based on whether the pharmacy branch recorded at least one completed sale during the employee's workday/date. Sales are branch revenue, so another employee's sale can fund the branch meal cost.

## Debt

At meal cut-off the record can become earned when the branch has sales. At workday close, any eligible meal that reached its cut-off but has no branch sale becomes `Meal Debt`.

Meal debt:

- does not reduce salary;
- does not reduce net profit because it was not funded/paid;
- remains visible in the employee's Meal History;
- is visible to the owner in the payroll/meal report;
- creates an in-app DEBT notification for active branch owners/managers.

## Automatic finalization

Run `python manage.py accrue_meals` from a scheduler/cron. The command safely repeats and closes previous workdays that were left open, preventing meal records from staying pending forever.
