from django.db import migrations


def remove_constraint_if_exists(apps, schema_editor):
    """Remove the SKU uniqueness constraint only when it exists.

    Some production databases reached this migration with the constraint
    absent because the earlier SKU migration was partially applied or the
    constraint was never created. PostgreSQL otherwise raises UndefinedObject.
    """
    Product = apps.get_model("inventory", "Product")
    table = Product._meta.db_table
    name = "uniq_product_sku_per_pharmacy"
    with schema_editor.connection.cursor() as cursor:
        constraints = schema_editor.connection.introspection.get_constraints(cursor, table)
    if name in constraints:
        schema_editor.execute(
            "ALTER TABLE %s DROP CONSTRAINT %s" % (
                schema_editor.quote_name(table),
                schema_editor.quote_name(name),
            )
        )


class Migration(migrations.Migration):
    dependencies = [("inventory", "0008_stockimport_file_fingerprint")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(remove_constraint_if_exists, migrations.RunPython.noop)],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="product",
                    name="uniq_product_sku_per_pharmacy",
                ),
            ],
        ),
    ]
