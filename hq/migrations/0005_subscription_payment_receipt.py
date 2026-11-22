from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [('hq', '0004_receipt_sms_package')]
    operations = [
        migrations.AddField(
            model_name='subscription', name='payment_reference',
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.CreateModel(
            name='SubscriptionPaymentReceipt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('access_token', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ('receipt_number', models.CharField(editable=False, max_length=40, unique=True)),
                ('sms_status', models.CharField(choices=[('NOT_SENT', 'Not sent'), ('SENT', 'Sent'), ('FAILED', 'Failed')], default='NOT_SENT', max_length=12)),
                ('sms_sent_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('sms_attempts', models.PositiveIntegerField(default=0, editable=False)),
                ('sms_last_error', models.TextField(blank=True, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('subscription', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='payment_receipt', to='hq.subscription')),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
