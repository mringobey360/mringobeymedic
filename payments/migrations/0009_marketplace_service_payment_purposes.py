from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('payments', '0008_layaway_sms_package_payment')]
    operations = [migrations.AlterField(model_name='pesapaltransaction', name='purpose', field=models.CharField(choices=[('SUBSCRIPTION','New Subscription'),('SUBSCRIPTION_RENEWAL','Subscription Renewal'),('BRANCH_ADDON','Branch Add-on'),('BRANCH_ADDON_RENEWAL','Branch Add-on Renewal'),('SMS_DEBT_PACKAGE','Premium Debt SMS Package'),('SMS_RECEIPT_PACKAGE','Premium Receipt SMS Package'),('SMS_MARKETING_PACKAGE','Customer Marketing SMS Package'),('DAILY_REPORT_PACKAGE','Intelligent Report SMS Package'),('LAYAWAY_SMS_PACKAGE','Layaway SMS Package'),('MARKETPLACE_ORDER','Marketplace Order'),('MARKETPLACE_ONLINE_SELLING','Marketplace Online Selling Subscription'),('MARKETPLACE_ORDER_SMS','Marketplace Seller Order SMS Add-on')], max_length=30))]
