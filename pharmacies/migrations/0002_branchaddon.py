import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('pharmacies', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='BranchAddOn',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('monthly_price', models.DecimalField(decimal_places=2, help_text='Price at the time this add-on was requested (kept even if the system-wide price later changes).', max_digits=12)),
                ('status', models.CharField(choices=[('PENDING_PAYMENT', 'Pending Payment'), ('ACTIVE', 'Active'), ('CANCELLED', 'Cancelled')], default='PENDING_PAYMENT', max_length=20)),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('activated_at', models.DateTimeField(blank=True, null=True)),
                ('next_billing_date', models.DateField(blank=True, help_text='Set on activation/renewal. Flat monthly billing — no proration.', null=True)),
                ('cancelled_at', models.DateTimeField(blank=True, null=True)),
                ('confirmed_by', models.ForeignKey(blank=True, help_text='Superadmin who confirmed the mobile-money payment.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='confirmed_branch_addons', to=settings.AUTH_USER_MODEL)),
                ('owner', models.ForeignKey(help_text='The Shop Owner/Supervisor billed for this additional branch.', on_delete=django.db.models.deletion.CASCADE, related_name='branch_addons', to=settings.AUTH_USER_MODEL)),
                ('pharmacy', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='addon', to='pharmacies.pharmacy')),
            ],
            options={
                'ordering': ['-requested_at'],
            },
        ),
    ]
