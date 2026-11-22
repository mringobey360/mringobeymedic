from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [
        ('marketplace', '0006_online_service_subscription'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='OnlineServiceRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('service', models.CharField(choices=[('ONLINE_SELLING','Online Selling'),('ORDER_SMS','Seller Order Alert SMS')], max_length=24)),
                ('status', models.CharField(choices=[('PENDING','Pending review'),('AWAITING_PAYMENT','Awaiting payment'),('APPROVED','Approved by admin'),('ACTIVE','Active'),('REJECTED','Rejected'),('CANCELLED','Cancelled')], db_index=True, default='PENDING', max_length=20)),
                ('amount', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('customer_note', models.CharField(blank=True, max_length=500)),
                ('admin_note', models.CharField(blank=True, max_length=500)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('requested_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='marketplace_service_requests', to=settings.AUTH_USER_MODEL)),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_marketplace_service_requests', to=settings.AUTH_USER_MODEL)),
                ('store', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='service_requests', to='marketplace.sellerstore')),
                ('subscription', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='service_request', to='marketplace.onlineservicesubscription')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(model_name='onlineservicerequest', index=models.Index(fields=['status', 'created_at'], name='mp_svc_req_status_created_idx')),
    ]
