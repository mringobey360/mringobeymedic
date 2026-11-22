from decimal import Decimal

from django.db import migrations, models


TIER_CHOICES = [
    ('STARTER', 'Starter'),
    ('BUSINESS', 'Business'),
    ('PROFESSIONAL', 'Professional'),
    ('ENTERPRISE', 'Enterprise'),
]


def add_missing_saas_columns(apps, schema_editor):
    """Add only columns that are missing from an existing production DB.

    This migration is intentionally idempotent because some SmartPOS
    production databases already contain ``tier`` from an earlier deployment.
    Django's migration state is still updated below, without attempting to add
    an already-existing PostgreSQL column a second time.
    """
    connection = schema_editor.connection
    introspection = connection.introspection

    def existing_columns(model):
        with connection.cursor() as cursor:
            return {c.name for c in introspection.get_table_description(cursor, model._meta.db_table)}

    PaymentSettings = apps.get_model('hq', 'PaymentSettings')
    Subscription = apps.get_model('hq', 'Subscription')
    SignupRequest = apps.get_model('hq', 'SignupRequest')

    # IMPORTANT: this migration runs against the historical model state from
    # 0005. The new fields do NOT exist on that historical state yet, so do
    # not call model._meta.get_field(field_name) here. Doing so causes
    # FieldDoesNotExist on production databases. Define the database fields
    # explicitly, then let SeparateDatabaseAndState update Django's state.
    additions = [
        (PaymentSettings, 'starter_monthly_price', models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('15000'))),
        (PaymentSettings, 'starter_yearly_price', models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('150000'))),
        (PaymentSettings, 'business_monthly_price', models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('30000'))),
        (PaymentSettings, 'business_yearly_price', models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('300000'))),
        (PaymentSettings, 'professional_monthly_price', models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('50000'))),
        (PaymentSettings, 'professional_yearly_price', models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('500000'))),
        (PaymentSettings, 'enterprise_monthly_price', models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('100000'))),
        (PaymentSettings, 'enterprise_yearly_price', models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1000000'))),
        (Subscription, 'tier', models.CharField(max_length=15, choices=TIER_CHOICES, default='STARTER')) ,
        (SignupRequest, 'tier', models.CharField(max_length=15, choices=TIER_CHOICES, default='STARTER')),
    ]

    for model, field_name, field in additions:
        if field_name in existing_columns(model):
            continue
        # Bind the field to the historical model before passing it to Django's
        # schema editor. This mirrors migrations.AddField without requiring
        # the field to exist in the pre-0006 historical state.
        field.set_attributes_from_name(field_name)
        schema_editor.add_field(model, field)


def seed_saas_pricing(apps, schema_editor):
    PaymentSettings = apps.get_model('hq', 'PaymentSettings')
    obj, _ = PaymentSettings.objects.get_or_create(
        pk=1,
        defaults={
            'lipa_namba': 'CHANGE-ME',
            'payee_name': 'CHANGE-ME',
            'support_phone': 'CHANGE-ME',
            'owner_notification_phone': 'CHANGE-ME',
        },
    )
    values = {
        'starter_monthly_price': Decimal('15000'),
        'starter_yearly_price': Decimal('150000'),
        'business_monthly_price': Decimal('30000'),
        'business_yearly_price': Decimal('300000'),
        'professional_monthly_price': Decimal('50000'),
        'professional_yearly_price': Decimal('500000'),
        'enterprise_monthly_price': Decimal('100000'),
        'enterprise_yearly_price': Decimal('1000000'),
    }
    for field, value in values.items():
        setattr(obj, field, value)
    obj.save(update_fields=list(values))


class Migration(migrations.Migration):
    dependencies = [('hq', '0005_subscription_payment_receipt')]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(add_missing_saas_columns, migrations.RunPython.noop)],
            state_operations=[
                migrations.AddField(
                    model_name='paymentsettings',
                    name='starter_monthly_price',
                    field=models.DecimalField(decimal_places=2, default=Decimal('15000'), max_digits=12),
                ),
                migrations.AddField(
                    model_name='paymentsettings',
                    name='starter_yearly_price',
                    field=models.DecimalField(decimal_places=2, default=Decimal('150000'), max_digits=12),
                ),
                migrations.AddField(
                    model_name='paymentsettings',
                    name='business_monthly_price',
                    field=models.DecimalField(decimal_places=2, default=Decimal('30000'), max_digits=12),
                ),
                migrations.AddField(
                    model_name='paymentsettings',
                    name='business_yearly_price',
                    field=models.DecimalField(decimal_places=2, default=Decimal('300000'), max_digits=12),
                ),
                migrations.AddField(
                    model_name='paymentsettings',
                    name='professional_monthly_price',
                    field=models.DecimalField(decimal_places=2, default=Decimal('50000'), max_digits=12),
                ),
                migrations.AddField(
                    model_name='paymentsettings',
                    name='professional_yearly_price',
                    field=models.DecimalField(decimal_places=2, default=Decimal('500000'), max_digits=12),
                ),
                migrations.AddField(
                    model_name='paymentsettings',
                    name='enterprise_monthly_price',
                    field=models.DecimalField(decimal_places=2, default=Decimal('100000'), max_digits=12),
                ),
                migrations.AddField(
                    model_name='paymentsettings',
                    name='enterprise_yearly_price',
                    field=models.DecimalField(decimal_places=2, default=Decimal('1000000'), max_digits=12),
                ),
                migrations.AddField(
                    model_name='subscription',
                    name='tier',
                    field=models.CharField(choices=TIER_CHOICES, default='STARTER', max_length=15),
                ),
                migrations.AddField(
                    model_name='signuprequest',
                    name='tier',
                    field=models.CharField(choices=TIER_CHOICES, default='STARTER', max_length=15),
                ),
            ],
        ),
        migrations.RunPython(seed_saas_pricing, migrations.RunPython.noop),
    ]
