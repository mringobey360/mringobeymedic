import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('layaway', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('pharmacies', '0015_pharmacy_attendance_qr_requires_location'),
    ]
    operations = [
        migrations.AlterField(
            model_name='layawayplan', name='status',
            field=models.CharField(choices=[('ACTIVE','Active'),('PAID_PENDING_HANDOVER','Paid — pending handover'),('COMPLETED','Completed'),('NEEDS_RESOLUTION','Needs resolution'),('CANCELLED','Cancelled')], db_index=True, default='ACTIVE', max_length=24),
        ),
        migrations.CreateModel(
            name='LayawaySMSPackage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=30, unique=True)),
                ('name', models.CharField(max_length=100)),
                ('tagline', models.CharField(blank=True, max_length=180)),
                ('price', models.DecimalField(decimal_places=2, max_digits=10)),
                ('sms_credits', models.PositiveIntegerField()),
                ('is_featured', models.BooleanField(default=False)),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
            ], options={'ordering': ['sort_order', 'price']},
        ),
        migrations.CreateModel(
            name='LayawaySMSWallet',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('trial_used', models.PositiveSmallIntegerField(default=0)),
                ('paid_credits', models.PositiveIntegerField(default=0)),
                ('paid_used', models.PositiveIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('pharmacy', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='layaway_sms_wallet', to='pharmacies.pharmacy')),
            ],
        ),
        migrations.CreateModel(
            name='LayawaySMSPurchase',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('payment_reference', models.CharField(blank=True, max_length=150)),
                ('customer_note', models.TextField(blank=True)),
                ('admin_note', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('PENDING','Pending verification'),('APPROVED','Approved'),('REJECTED','Rejected')], default='PENDING', max_length=12)),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('credits_granted', models.BooleanField(default=False)),
                ('package', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='purchases', to='layaway.layawaysmspackage')),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='layaway_sms_purchases', to='pharmacies.pharmacy')),
                ('requested_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='layaway_sms_purchases', to=settings.AUTH_USER_MODEL)),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_layaway_sms_purchases', to=settings.AUTH_USER_MODEL)),
            ], options={'ordering': ['-requested_at']},
        ),
    ]
