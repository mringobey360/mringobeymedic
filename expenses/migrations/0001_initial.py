# Generated for SmartPOS financial-control module.
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ('accounts', '0003_meal_time_ledger'),
        ('pharmacies', '0004_branchaddon_payment_reference'),
    ]
    operations = [
        migrations.CreateModel(
            name='ExpenseCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80)),
                ('is_active', models.BooleanField(default=True)),
                ('is_system', models.BooleanField(default=False, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('pharmacy', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='expense_categories', to='pharmacies.pharmacy')),
            ],
            options={'ordering':['name']},
        ),
        migrations.CreateModel(
            name='CashDay',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(default=django.utils.timezone.localdate)),
                ('opening_cash', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('opening_note', models.CharField(blank=True, max_length=255)),
                ('status', models.CharField(choices=[('OPEN','Open'),('CLOSED','Closed')], default='OPEN', max_length=10)),
                ('actual_closing_cash', models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ('closing_note', models.CharField(blank=True, max_length=255)),
                ('opened_at', models.DateTimeField(auto_now_add=True)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('closed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='closed_cash_days', to='accounts.user')),
                ('opened_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='opened_cash_days', to='accounts.user')),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cash_days', to='pharmacies.pharmacy')),
            ],
            options={'ordering':['-date']},
        ),
        migrations.CreateModel(
            name='Expense',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('expense_date', models.DateField(default=django.utils.timezone.localdate)),
                ('title', models.CharField(max_length=160)),
                ('description', models.TextField(blank=True)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=14)),
                ('payment_source', models.CharField(choices=[('CASH_SALES','Sales cash / petty cash'),('MOBILE_MONEY','Mobile money'),('BANK','Bank account'),('OTHER','Other')], default='CASH_SALES', max_length=20)),
                ('reference', models.CharField(blank=True, max_length=100)),
                ('receipt', models.FileField(blank=True, null=True, upload_to='expense_receipts/%Y/%m/')),
                ('submitted_at', models.DateTimeField(auto_now_add=True)),
                ('status', models.CharField(choices=[('PENDING','Pending approval'),('APPROVED','Approved'),('REJECTED','Rejected'),('CANCELLED','Cancelled')], db_index=True, default='PENDING', max_length=12)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decision_note', models.TextField(blank=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='expenses', to='expenses.expensecategory')),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='decided_expenses', to='accounts.user')),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='expenses', to='pharmacies.pharmacy')),
                ('submitted_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='submitted_expenses', to='accounts.user')),
            ],
            options={'ordering':['-expense_date','-submitted_at']},
        ),
        migrations.AddConstraint(model_name='expensecategory', constraint=models.UniqueConstraint(fields=('pharmacy','name'), name='unique_expense_category_per_pharmacy')),
        migrations.AddConstraint(model_name='cashday', constraint=models.UniqueConstraint(fields=('pharmacy','date'), name='unique_cash_day_per_pharmacy_date')),
        migrations.AddIndex(model_name='cashday', index=models.Index(fields=['pharmacy','-date'], name='expenses_ca_pharmacy_9f0a2d_idx')),
        migrations.AddIndex(model_name='expense', index=models.Index(fields=['pharmacy','-expense_date'], name='expenses_ex_pharmacy_8c2b0a_idx')),
        migrations.AddIndex(model_name='expense', index=models.Index(fields=['pharmacy','status'], name='expenses_ex_pharmacy_5f1a1e_idx')),
    ]
