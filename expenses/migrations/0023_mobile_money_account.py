from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('pharmacies', '0004_branchaddon_payment_reference'),
        ('expenses', '0022_cashday_actual_mobile_received'),
    ]

    operations = [
        migrations.RenameField(model_name='cashday', old_name='actual_mobile_received', new_name='actual_mobile_balance'),
        migrations.AddField(model_name='cashday', name='opening_mobile',
                            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
        migrations.CreateModel(
            name='CashTransfer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('transfer_date', models.DateField(db_index=True, default=django.utils.timezone.localdate)),
                ('direction', models.CharField(choices=[('CASH_TO_MOBILE', 'Drawer cash -> Mobile money'), ('MOBILE_TO_CASH', 'Mobile money -> Drawer cash')], max_length=20)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=14)),
                ('reference', models.CharField(blank=True, max_length=100)),
                ('note', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cash_transfers', to=settings.AUTH_USER_MODEL)),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cash_transfers', to='pharmacies.pharmacy')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(model_name='cashtransfer',
                            index=models.Index(fields=['pharmacy', 'transfer_date'], name='expenses_ctransfer_pharm_idx')),
    ]
