from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('expenses', '0001_initial'),
        ('accounts', '0003_meal_time_ledger'),
    ]

    operations = [
        migrations.AddField(
            model_name='cashday',
            name='opening_source',
            field=models.CharField(
                choices=[
                    ('MANUAL', 'Manual opening'),
                    ('CARRY_FORWARD', 'Carried forward from previous closing'),
                    ('OVERRIDE', 'Manager override'),
                ],
                default='MANUAL',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='cashday',
            name='carried_from',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='carried_to_days',
                to='expenses.cashday',
            ),
        ),
        migrations.CreateModel(
            name='CashOpeningAdjustment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('previous_amount', models.DecimalField(decimal_places=2, max_digits=14)),
                ('new_amount', models.DecimalField(decimal_places=2, max_digits=14)),
                ('reason', models.TextField()),
                ('adjusted_at', models.DateTimeField(auto_now_add=True)),
                ('adjusted_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cash_opening_adjustments', to='accounts.user')),
                ('cash_day', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='opening_adjustments', to='expenses.cashday')),
            ],
            options={'ordering': ['-adjusted_at']},
        ),
        migrations.AddIndex(
            model_name='cashopeningadjustment',
            index=models.Index(fields=['cash_day', '-adjusted_at'], name='expenses_ca_cash_day_8f4d2a_idx'),
        ),
    ]
