from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0015_branch_dashboard_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='discount_type',
            field=models.CharField(
                choices=[('PERCENT', 'Percentage (%)'), ('AMOUNT', 'Fixed amount (TZS per unit)')],
                default='PERCENT', max_length=8,
                help_text='Percentage of the price, or a fixed TZS amount taken off each unit.',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='min_discount_amount',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0.00'), max_digits=12,
                help_text='Minimum TZS discount per unit (used when the discount type is Fixed amount).',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='max_discount_amount',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0.00'), max_digits=12,
                help_text='Maximum TZS discount per unit (used when the discount type is Fixed amount).',
            ),
        ),
    ]
