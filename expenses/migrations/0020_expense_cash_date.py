"""V54.68: Expense.cash_date - the business day an approved expense left the drawer.
Existing rows stay NULL and keep using expense_date (no history is rewritten)."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('expenses', '0019_relocate_late_meal_payments')]
    operations = [
        migrations.AddField(model_name='expense', name='cash_date', field=models.DateField(blank=True, null=True)),
    ]
