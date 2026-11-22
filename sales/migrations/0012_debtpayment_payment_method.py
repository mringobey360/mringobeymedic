from django.db import migrations, models


class Migration(migrations.Migration):
    """Existing repayments become LEGACY (excluded from the cash ledger so history is not rewritten);
    every new repayment defaults to CASH and must state its method."""

    dependencies = [('sales', '0011_saleitem_vip_discount_amount')]

    operations = [
        migrations.AddField(
            model_name='debtpayment', name='payment_method',
            field=models.CharField(choices=[('CASH', 'Cash (into the drawer)'),
                                            ('MOBILE_MONEY', 'Mobile money / bank (not in the drawer)'),
                                            ('LEGACY', 'Recorded before payment methods existed')],
                                   db_index=True, default='LEGACY', max_length=15),
        ),
        migrations.AlterField(
            model_name='debtpayment', name='payment_method',
            field=models.CharField(choices=[('CASH', 'Cash (into the drawer)'),
                                            ('MOBILE_MONEY', 'Mobile money / bank (not in the drawer)'),
                                            ('LEGACY', 'Recorded before payment methods existed')],
                                   db_index=True, default='CASH', max_length=15),
        ),
    ]
