from django.core.management.base import BaseCommand
from django.db import connection

from customer_registry.models import (
    CustomerContact,
    CustomerCampaign,
    CustomerCampaignRecipient,
)


class Command(BaseCommand):
    help = "Ensure customer registry tables and production columns exist without deleting production data."

    def handle(self, *args, **options):
        models = (CustomerContact, CustomerCampaign, CustomerCampaignRecipient)
        existing_tables = set(connection.introspection.table_names())
        created_tables = []
        repaired_columns = []

        with connection.schema_editor() as editor:
            # Legacy production databases may have been created by an older
            # schema-repair command before Django migrations were introduced.
            # Create a completely missing table first, then reconcile missing
            # columns against the current Django model.
            for model in models:
                table = model._meta.db_table
                if table not in existing_tables:
                    editor.create_model(model)
                    existing_tables.add(table)
                    created_tables.append(table)
                    continue

                existing_columns = {
                    c.name for c in connection.introspection.get_table_description(
                        connection.cursor(), table
                    )
                }
                for field in model._meta.local_concrete_fields:
                    column = field.column
                    if column in existing_columns:
                        continue

                    # schema_editor.add_field uses the field definition from the
                    # model, including defaults/nullability, so existing rows get
                    # a valid value where the field has a default.
                    editor.add_field(model, field)
                    existing_columns.add(column)
                    repaired_columns.append(f"{table}.{column}")

        if created_tables:
            self.stdout.write(
                self.style.SUCCESS(
                    "Created missing tables: " + ", ".join(created_tables)
                )
            )
        if repaired_columns:
            self.stdout.write(
                self.style.SUCCESS(
                    "Repaired missing columns: " + ", ".join(repaired_columns)
                )
            )
        if not created_tables and not repaired_columns:
            self.stdout.write(
                self.style.SUCCESS("Customer registry database schema is up to date.")
            )
