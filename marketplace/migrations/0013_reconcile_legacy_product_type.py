"""Remove obsolete V61-era product_type column left by a previous deployment.

Product.kind is the supported discriminator. This migration is idempotent on
fresh installs and existing PostgreSQL databases, and retains all product rows.
"""
from django.db import migrations


def reconcile(apps, schema_editor):
    connection = schema_editor.connection
    table = apps.get_model('marketplace', 'Product')._meta.db_table
    with connection.cursor() as cursor:
        tables = connection.introspection.table_names(cursor)
        if table not in tables:
            return
        columns = {c.name for c in connection.introspection.get_table_description(cursor, table)}
        if 'product_type' not in columns:
            return
        if 'kind' not in columns:
            raise RuntimeError('Refusing to remove legacy product_type before Product.kind exists.')
        q = connection.ops.quote_name
        cursor.execute(f'UPDATE {q(table)} SET {q("kind")} = {q("product_type")} WHERE {q("kind")} IS NULL AND {q("product_type")} IN (%s, %s)', ['DIGITAL', 'PHYSICAL'])
        # No Django model uses this legacy column. Removing it prevents NOT NULL
        # violations on INSERT for both digital and physical products.
        cursor.execute(f'ALTER TABLE {q(table)} DROP COLUMN {q("product_type")}')


class Migration(migrations.Migration):
    dependencies = [('marketplace', '0012_digital_products')]
    operations = [migrations.RunPython(reconcile, migrations.RunPython.noop)]
