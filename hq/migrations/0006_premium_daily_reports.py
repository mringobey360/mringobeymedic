from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('hq', '0005_subscription_payment_receipt')]
    operations = [migrations.AddField(
        model_name='subscription', name='premium_daily_reports_enabled',
        field=models.BooleanField(default=False, help_text='Allow Premium Daily Sales Intelligence for this account. Professional/Enterprise tiers include it automatically.'),
    )]
