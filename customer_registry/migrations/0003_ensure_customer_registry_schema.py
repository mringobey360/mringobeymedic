from django.db import migrations


def ensure_tables(apps, schema_editor):
    """Repair production databases where migrations were recorded but tables are missing."""
    CustomerContact = apps.get_model('customer_registry', 'CustomerContact')
    CustomerCampaign = apps.get_model('customer_registry', 'CustomerCampaign')
    CustomerCampaignRecipient = apps.get_model('customer_registry', 'CustomerCampaignRecipient')

    existing = set(schema_editor.connection.introspection.table_names())

    # Create in dependency order. create_model() uses Django's current migration
    # state, so it also creates indexes and constraints without hard-coded SQL.
    for model in (CustomerContact, CustomerCampaign, CustomerCampaignRecipient):
        table = model._meta.db_table
        if table not in existing:
            schema_editor.create_model(model)
            existing.add(table)


def reverse_noop(apps, schema_editor):
    # Deliberately do not drop production tables during rollback.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('customer_registry', '0002_repair_customer_registry_tables'),
    ]

    operations = [
        migrations.RunPython(ensure_tables, reverse_noop),
    ]
