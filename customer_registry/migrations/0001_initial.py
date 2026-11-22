from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.core.validators import RegexValidator

class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('pharmacies', '0005_attendance_geofence'),
    ]
    operations = [

        migrations.CreateModel(
            name='CustomerCampaign',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=120)),
                ('message', models.TextField(max_length=1000)),
                ('status', models.CharField(choices=[('QUEUED','Queued'),('SENDING','Sending'),('COMPLETED','Completed'),('PARTIAL','Partially sent'),('FAILED','Failed')], default='QUEUED', max_length=12)),
                ('total_recipients', models.PositiveIntegerField(default=0)),
                ('sent_count', models.PositiveIntegerField(default=0)),
                ('failed_count', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='customer_campaigns_created',to=settings.AUTH_USER_MODEL)),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='customer_campaigns',to='pharmacies.pharmacy')),
            ],
            options={'ordering':['-created_at']},
        ),
        migrations.CreateModel(
            name='CustomerContact',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=150)),
                ('phone', models.CharField(max_length=20, validators=[RegexValidator(r'^\+[0-9]{9,14}$', 'Use international format, e.g. +255712345678.')])),
                ('is_active', models.BooleanField(default=True)),
                ('notes', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='registered_customer_contacts', to=settings.AUTH_USER_MODEL)),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='customer_contacts', to='pharmacies.pharmacy')),
            ],
            options={
                'ordering': ['name', 'id'],
            },
        ),
        migrations.CreateModel(
            name='CustomerCampaignRecipient',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=150)),
                ('phone', models.CharField(max_length=20)),
                ('message', models.TextField()),
                ('status', models.CharField(choices=[('PENDING','Pending'),('SENT','Sent'),('FAILED','Failed')],default='PENDING',max_length=10)),
                ('error', models.TextField(blank=True)),
                ('sent_at', models.DateTimeField(blank=True,null=True)),
                ('campaign', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='recipients',to='customer_registry.customercampaign')),
                ('customer', models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='campaign_recipients',to='customer_registry.customercontact')),
            ],
        ),
        migrations.AddIndex(model_name='customercampaign', index=models.Index(fields=['pharmacy','-created_at'], name='customer_ca_pharmac_9a4e9d_idx')),
        migrations.AddIndex(model_name='customercampaignrecipient', index=models.Index(fields=['campaign','status'], name='customer_ca_campaign_8e0a9f_idx')),

        migrations.AddConstraint(
            model_name='customercontact',
            constraint=models.UniqueConstraint(fields=('pharmacy','phone'), name='uniq_customer_phone_per_branch'),
        ),
        migrations.AddIndex(model_name='customercontact', index=models.Index(fields=['pharmacy','is_active'], name='customer_re_pharmac_2f4c4d_idx')),
        migrations.AddIndex(model_name='customercontact', index=models.Index(fields=['pharmacy','name'], name='customer_re_pharmac_5c7c65_idx')),
        migrations.AddIndex(model_name='customercontact', index=models.Index(fields=['pharmacy','phone'], name='customer_re_pharmac_6a7c34_idx')),
    ]
