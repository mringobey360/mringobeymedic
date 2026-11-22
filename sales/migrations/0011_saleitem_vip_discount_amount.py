from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('sales', '0010_debtpayment')]
    operations = [migrations.AddField(model_name='saleitem', name='vip_discount_amount', field=models.DecimalField(decimal_places=2, default=0, help_text='VIP loyalty discount total, recorded separately for audit.', max_digits=12))]
