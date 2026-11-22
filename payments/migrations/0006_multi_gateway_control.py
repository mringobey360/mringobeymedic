from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('payments', '0005_branch_renewal_and_ipn_url')]
    operations = [
        migrations.CreateModel(
            name='PaymentGatewaySettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_enabled', models.BooleanField(default=True)),
                ('active_gateway', models.CharField(choices=[('PESAPAL', 'PesaPal'), ('ABLINER', 'ABLiner')], default='PESAPAL', max_length=20)),
                ('abliner_enabled', models.BooleanField(default=False)),
                ('abliner_api_key', models.CharField(blank=True, max_length=255)),
                ('abliner_webhook_secret', models.CharField(blank=True, max_length=255)),
                ('abliner_base_url', models.URLField(blank=True, default='https://abliner.net')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'verbose_name': 'Payment Gateway Settings', 'verbose_name_plural': 'Payment Gateway Settings'},
        ),
        migrations.CreateModel(
            name='PaymentEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('gateway', models.CharField(max_length=20)),
                ('event_type', models.CharField(max_length=80)),
                ('provider_event_id', models.CharField(blank=True, max_length=180)),
                ('provider_status', models.CharField(blank=True, max_length=80)),
                ('message', models.CharField(blank=True, max_length=255)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('success', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('payment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='payments.pesapaltransaction')),
            ],
            options={'ordering': ['-created_at'], 'indexes': [models.Index(fields=['gateway', 'event_type'], name='payments_pay_gateway_8e2d9b_idx'), models.Index(fields=['provider_event_id'], name='payments_pay_provider_3d8b53_idx')],
                     'constraints': [models.UniqueConstraint(condition=~models.Q(provider_event_id=''), fields=('gateway', 'provider_event_id'), name='uniq_payment_provider_event')]},
        ),
        migrations.AddField(model_name='pesapaltransaction', name='gateway', field=models.CharField(db_index=True, default='PESAPAL', help_text='Gateway used for this transaction. Existing records remain pinned to their original provider.', max_length=20)),
        migrations.AddIndex(model_name='pesapaltransaction', index=models.Index(fields=['gateway', 'status', 'created_at'], name='payments_pay_gateway_4ccf9b_idx')),
    ]
