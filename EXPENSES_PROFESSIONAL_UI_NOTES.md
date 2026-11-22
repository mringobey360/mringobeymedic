# v93 – Professional Expenses UI

- expenses/templates/expenses/list.html – redesigned register page (summary strip, filters, table, mobile list)
- expenses/templates/expenses/form.html – redesigned Record Expense form with live summary panel
- expenses/templates/expenses/_expense_styles.html – NEW shared scoped styles (xp- prefix)
- expenses/templates/expenses/_empty_state.html – cleaner empty state
- expenses/forms.py – category placeholder "Select a category"

Fixes: dropdown menus no longer clipped by card overflow and show every option; duplicate "TZS" prefix on Amount removed.
