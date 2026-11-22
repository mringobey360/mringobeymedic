from django.db import migrations, models

# (model, fields, old_name, new_name) - new names are <= 30 chars (Django E034 limit)
RENAMES = [
    ("MealWorkday", ["employee", "-date"],
     "accounts_mea_employee_9e5a75_idx", "acc_mealwd_emp_date_idx"),
    ("MealLedger", ["pharmacy", "-date", "status"],
     "accounts_mea_pharmacy_5f5e0f_idx", "acc_mealled_ph_date_st_idx"),
    ("MealLedger", ["employee", "-date", "status"],
     "accounts_mea_employee_6a2f4b_idx", "acc_mealled_emp_date_st_idx"),
]


def rename_or_create_indexes(apps, schema_editor):
    """Production-safe: rename if the old index exists, skip if the new one
    already exists, otherwise create it (covers DBs where the index was missing)."""
    connection = schema_editor.connection
    for model_name, fields, old_name, new_name in RENAMES:
        model = apps.get_model("accounts", model_name)
        table = model._meta.db_table
        with connection.cursor() as cursor:
            existing = connection.introspection.get_constraints(cursor, table)
        if new_name in existing:
            continue
        new_index = models.Index(fields=fields, name=new_name)
        if old_name in existing:
            old_index = models.Index(fields=fields, name=old_name)
            schema_editor.rename_index(model, old_index, new_index)
        else:
            schema_editor.add_index(model, new_index)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_repair_missing_indexes")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(rename_or_create_indexes, noop_reverse),
            ],
            state_operations=[
                migrations.RenameIndex(
                    model_name="mealworkday",
                    new_name="acc_mealwd_emp_date_idx",
                    old_name="accounts_mea_employee_9e5a75_idx",
                ),
                migrations.RenameIndex(
                    model_name="mealledger",
                    new_name="acc_mealled_ph_date_st_idx",
                    old_name="accounts_mea_pharmacy_5f5e0f_idx",
                ),
                migrations.RenameIndex(
                    model_name="mealledger",
                    new_name="acc_mealled_emp_date_st_idx",
                    old_name="accounts_mea_employee_6a2f4b_idx",
                ),
            ],
        ),
    ]
