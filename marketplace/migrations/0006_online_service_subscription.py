from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('marketplace', '0005_seller_store_and_merchant_payment_profile')]
    operations = [migrations.CreateModel(name='OnlineServiceSubscription', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('service', models.CharField(choices=[('ONLINE_SELLING','Online Selling'),('ORDER_SMS','Seller Order Alert SMS')], max_length=24)),
        ('status', models.CharField(choices=[('PENDING','Awaiting payment'),('ACTIVE','Active'),('EXPIRED','Expired'),('CANCELLED','Cancelled')], db_index=True, default='PENDING', max_length=12)),
        ('monthly_amount', models.DecimalField(decimal_places=2, max_digits=10)),
        ('sms_quota', models.PositiveIntegerField(default=0, help_text='Monthly seller order-alert SMS allowance; does not apply to customer payment receipts.')),
        ('sms_used', models.PositiveIntegerField(default=0)),
        ('period_start', models.DateTimeField(blank=True, null=True)),
        ('period_end', models.DateTimeField(blank=True, db_index=True, null=True)),
        ('last_payment_reference', models.CharField(blank=True, max_length=120)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('updated_at', models.DateTimeField(auto_now=True)),
        ('store', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='service_subscriptions', to='marketplace.sellerstore')),
    ], options={'ordering':['-created_at']}), migrations.AddIndex(model_name='onlineservicesubscription', index=models.Index(fields=['store','service','status'], name='mp_store_svc_status_idx'))]
