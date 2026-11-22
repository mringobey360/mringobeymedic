from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('pharmacies', '0001_initial'),
        ('sales', '0009_sale_customer_contact'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DebtPayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('paid_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('debt', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payments', to='sales.debt')),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='debt_payments', to='pharmacies.pharmacy')),
                ('recorded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='recorded_debt_payments', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-paid_at'], 'indexes': [models.Index(fields=['pharmacy', 'paid_at'], name='debtpay_pharm_paid_idx')]},
        ),
    ]
