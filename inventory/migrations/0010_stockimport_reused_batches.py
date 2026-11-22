from django.db import migrations, models


FIELD_SQL = """
ALTER TABLE inventory_stockimport
ADD COLUMN IF NOT EXISTS reused_batches integer;
UPDATE inventory_stockimport
SET reused_batches = 0
WHERE reused_batches IS NULL;
ALTER TABLE inventory_stockimport
ALTER COLUMN reused_batches SET DEFAULT 0;
ALTER TABLE inventory_stockimport
ALTER COLUMN reused_batches SET NOT NULL;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0009_remove_product_uniq_product_sku_per_pharmacy"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(FIELD_SQL, reverse_sql="""
                ALTER TABLE inventory_stockimport DROP COLUMN IF EXISTS reused_batches;
            """)],
            state_operations=[
                migrations.AddField(
                    model_name="stockimport",
                    name="reused_batches",
                    field=models.PositiveIntegerField(
                        default=0,
                        help_text="Opening-stock batch rows safely reused without adding stock again.",
                    ),
                ),
            ],
        ),
    ]
