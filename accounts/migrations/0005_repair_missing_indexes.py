from django.db import migrations


def ensure_meal_pharmacy_index(apps, schema_editor):
    """Repair a production schema where the recorded meal index is missing."""
    connection = schema_editor.connection
    model = apps.get_model("accounts", "MealLedger")
    table = model._meta.db_table
    index_name = "accounts_mea_pharmacy_5f5e0f_idx"
    constraints = connection.introspection.get_constraints(connection.cursor(), table)
    if index_name in constraints and (constraints[index_name].get("index") or constraints[index_name].get("unique")):
        return

    # If a previous deployment created the same logical index under another name,
    # leave it alone rather than creating a duplicate.
    target_columns = [model._meta.get_field("pharmacy").column, "date", "status"]
    for info in constraints.values():
        if info.get("index") and info.get("columns") == target_columns:
            return

    index = next((idx for idx in model._meta.indexes if idx.name == index_name), None)
    if index is None:
        return
    schema_editor.add_index(model, index)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_attendance_verification")]
    operations = [migrations.RunPython(ensure_meal_pharmacy_index, migrations.RunPython.noop)]
