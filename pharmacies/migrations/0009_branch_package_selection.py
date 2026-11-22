from decimal import Decimal
from django.db import migrations, models
import django.db.models.deletion


def backfill_branch_packages(apps, schema_editor):
    BranchAddOn = apps.get_model('pharmacies', 'BranchAddOn')
    SignupRequest = apps.get_model('hq', 'SignupRequest')
    Subscription = apps.get_model('hq', 'Subscription')
    for addon in BranchAddOn.objects.filter(billing_amount=Decimal('0')):
        addon.billing_amount = addon.monthly_price
        addon.package_tier = 'STARTER'
        addon.billing_plan = 'MONTHLY'
        addon.save(update_fields=['billing_amount', 'package_tier', 'billing_plan'])


def reverse_backfill(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('hq', '0007_merge_0006_0006'),
        ('pharmacies', '0008_professional_attendance_policy'),
    ]

    operations = [
        migrations.AddField(
            model_name='branchaddon', name='package_tier',
            field=models.CharField(choices=[('STARTER', 'Starter'), ('BUSINESS', 'Business'), ('PROFESSIONAL', 'Professional'), ('ENTERPRISE', 'Enterprise')], default='STARTER', max_length=15, help_text='Core package selected specifically for this branch.'),
        ),
        migrations.AddField(
            model_name='branchaddon', name='billing_plan',
            field=models.CharField(choices=[('MONTHLY', 'Kila Mwezi'), ('YEARLY', 'Kila Mwaka')], default='MONTHLY', max_length=10, help_text='Billing period selected specifically for this branch.'),
        ),
        migrations.AddField(
            model_name='branchaddon', name='billing_amount',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), help_text='Price snapshot for the selected branch package and billing period.', max_digits=12),
        ),
        migrations.RunPython(backfill_branch_packages, reverse_backfill),
    ]
