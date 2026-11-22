from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('pharmacies', '0001_initial'),
        ('sales', '0006_receipt_sms_tracking'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='status',
            field=models.CharField(
                choices=[('COMPLETED', 'Completed'), ('PARTIALLY_RETURNED', 'Partially returned'), ('VOIDED', 'Voided')],
                db_index=True, default='COMPLETED', max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='sale',
            name='voided_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='saleitem',
            name='returned_quantity',
            field=models.PositiveIntegerField(default=0, help_text='Total quantity returned/voided through approved return requests.'),
        ),
        migrations.AlterField(
            model_name='saleitem',
            name='quantity',
            field=models.PositiveIntegerField(help_text='NET quantity still counted as sold (original quantity minus approved returns).'),
        ),
        migrations.CreateModel(
            name='SaleReturnRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(
                    choices=[('PENDING', 'Pending approval'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected')],
                    db_index=True, default='PENDING', max_length=10,
                )),
                ('reason', models.TextField()),
                ('is_full_void', models.BooleanField(default=False, help_text='True when the request covers everything still counted in the sale.')),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decision_note', models.TextField(blank=True)),
                ('disposition', models.CharField(
                    choices=[('RESTOCK', 'Return to stock'), ('WRITE_OFF', 'Write off (damaged / cannot be resold)')],
                    default='RESTOCK', max_length=12,
                )),
                ('refund_amount', models.DecimalField(decimal_places=2, default=0, help_text='Value of the returned items at the price the customer was charged.', max_digits=12)),
                ('refund_due', models.DecimalField(decimal_places=2, default=0, help_text='Cash/mobile money to hand back to the customer (already-paid part only).', max_digits=12)),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sale_returns_decided', to=settings.AUTH_USER_MODEL)),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sale_return_requests', to='pharmacies.pharmacy')),
                ('requested_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sale_returns_requested', to=settings.AUTH_USER_MODEL)),
                ('sale', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='return_requests', to='sales.sale')),
            ],
            options={
                'ordering': ['-requested_at'],
                'indexes': [models.Index(fields=['pharmacy', 'status', '-requested_at'], name='salereturn_pharm_status_idx')],
            },
        ),
        migrations.CreateModel(
            name='SaleReturnLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField()),
                ('request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='sales.salereturnrequest')),
                ('sale_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='return_lines', to='sales.saleitem')),
            ],
        ),
    ]
