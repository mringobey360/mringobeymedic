from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('expenses', '0020_expense_cash_date')]

    operations = [
        migrations.AddField(
            model_name='cashclosingsnapshot', name='cash_debt_collected',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
    ]
