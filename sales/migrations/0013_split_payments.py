from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('sales', '0012_debtpayment_payment_method')]

    operations = [
        migrations.AddField(model_name='sale', name='cash_paid',
                            field=models.DecimalField(decimal_places=2, default=0, max_digits=12)),
        migrations.AddField(model_name='sale', name='mobile_paid',
                            field=models.DecimalField(decimal_places=2, default=0, max_digits=12)),
        migrations.AlterField(
            model_name='sale', name='payment_method',
            field=models.CharField(choices=[('CASH', 'Cash'), ('MOBILE_MONEY', 'Mobile Money'),
                                            ('DEBT', 'Debt / Credit'), ('SPLIT', 'Cash + Mobile Money')],
                                   default='CASH', max_length=15),
        ),
    ]
