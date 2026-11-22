import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def backfill_legacy(apps, schema_editor):
    MealLedger = apps.get_model('accounts', 'MealLedger')
    MealCashPayment = apps.get_model('expenses', 'MealCashPayment')
    rows = []
    for m in MealLedger.objects.filter(status='EARNED').iterator():
        rows.append(MealCashPayment(meal_id=m.pk, pharmacy_id=m.pharmacy_id, amount=m.amount,
                                    meal_date=m.date, is_legacy=True))
        if len(rows) >= 500:
            MealCashPayment.objects.bulk_create(rows); rows = []
    if rows:
        MealCashPayment.objects.bulk_create(rows)


class Migration(migrations.Migration):
    dependencies = [
        ('expenses', '0012_cash_reset_professional_fields'),
        ('accounts', '0031_workdayreopenrequest'),
        ('pharmacies', '__first__'),
    ]
    operations = [
        migrations.CreateModel(
            name='MealCashPayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('meal_date', models.DateField()),
                ('paid_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('reversed_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('is_legacy', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('meal', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cash_payments', to='accounts.mealledger')),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='meal_cash_payments', to='pharmacies.pharmacy')),
            ],
            options={'ordering': ['-paid_at', '-pk']},
        ),
        migrations.AddIndex(
            model_name='mealcashpayment',
            index=models.Index(fields=['pharmacy', 'paid_at'], name='exp_mealpay_ph_paid_idx'),
        ),
        migrations.RunPython(backfill_legacy, migrations.RunPython.noop),
    ]
