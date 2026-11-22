from django.db import migrations, models


def add_sku_column_if_missing(apps, schema_editor):
    """Add SKU only when the live database does not already have it."""
    Product = apps.get_model("inventory", "Product")
    table = Product._meta.db_table
    existing_columns = {c.name for c in schema_editor.connection.introspection.get_table_description(
        schema_editor.connection.cursor(), table
    )}
    if "sku" not in existing_columns:
        # NOTE: inside SeparateDatabaseAndState the historical model does NOT
        # have "sku" yet (state is applied after the database operation), so
        # Product._meta.get_field("sku") raises FieldDoesNotExist. Issue the
        # ADD COLUMN directly instead.
        qn = schema_editor.quote_name
        schema_editor.execute(
            "ALTER TABLE %s ADD COLUMN %s varchar(40) NULL" % (qn(table), qn("sku"))
        )


def populate_skus(apps, schema_editor):
    Product = apps.get_model("inventory", "Product")
    for product in Product.objects.filter(sku__isnull=True).order_by("pk").iterator():
        product.sku = f"SKU-{product.pharmacy_id:04d}-{product.pk:08d}"
        product.save(update_fields=["sku"])


def repair_duplicate_skus(apps, schema_editor):
    """Repair duplicate non-empty SKUs per pharmacy before enforcing uniqueness.

    This is only needed for legacy/partially migrated databases. The first
    row keeps its existing SKU; later duplicates receive a deterministic SKU
    based on pharmacy + primary key.
    """
    Product = apps.get_model("inventory", "Product")
    from django.db.models import Count

    duplicate_groups = (
        Product.objects.exclude(sku__isnull=True)
        .exclude(sku="")
        .values("pharmacy_id", "sku")
        .annotate(n=Count("pk"))
        .filter(n__gt=1)
        .order_by("pharmacy_id", "sku")
    )
    for group in duplicate_groups.iterator():
        rows = list(Product.objects.filter(
            pharmacy_id=group["pharmacy_id"], sku=group["sku"]
        ).order_by("pk").values_list("pk", flat=True))
        for pk in rows[1:]:
            Product.objects.filter(pk=pk).update(
                sku=f"SKU-{group['pharmacy_id']:04d}-{pk:08d}"
            )


def add_unique_constraint_if_missing(apps, schema_editor):
    Product = apps.get_model("inventory", "Product")
    table = Product._meta.db_table
    constraints = schema_editor.connection.introspection.get_constraints(
        schema_editor.connection.cursor(), table
    )
    target_fields = ["pharmacy_id", "sku"]
    already_unique = any(
        bool(info.get("unique")) and list(info.get("columns") or []) == target_fields
        for info in constraints.values()
    )
    if not already_unique:
        schema_editor.add_constraint(
            Product,
            models.UniqueConstraint(
                fields=("pharmacy", "sku"),
                name="uniq_product_sku_per_pharmacy",
            ),
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("inventory", "0006_enforce_category_tenant_scope")]

    operations = [
        # Keep Django's migration state in sync without blindly issuing ADD COLUMN.
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(add_sku_column_if_missing, noop)],
            state_operations=[
                migrations.AddField(
                    model_name="product",
                    name="sku",
                    field=models.CharField(
                        blank=True,
                        max_length=40,
                        null=True,
                        help_text="Stable stock-keeping unit. Leave blank to generate automatically.",
                    ),
                ),
            ],
        ),
        migrations.RunPython(populate_skus, noop),
        migrations.RunPython(repair_duplicate_skus, noop),
        migrations.RunPython(add_unique_constraint_if_missing, noop),
        # The unique index above is intentionally represented as a Django constraint
        # in migration state. Existing databases get the equivalent PostgreSQL index.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AddConstraint(
                    model_name="product",
                    constraint=models.UniqueConstraint(
                        fields=("pharmacy", "sku"),
                        name="uniq_product_sku_per_pharmacy",
                    ),
                ),
            ],
        ),
    ]
