from django.db import migrations


def create_table_if_missing(apps, schema_editor):
    """Create the preference table only when it is not already there.

    Brand-new databases get it from 0001. Databases that had an older, differently
    shaped `dashboard` table/migration history (which made 0001 a no-op) get the
    correct table here. Any legacy table is left untouched.
    """
    model = apps.get_model('dashboard', 'DashboardPreference')
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        existing = set(connection.introspection.table_names(cursor))
    if model._meta.db_table not in existing:
        schema_editor.create_model(model)


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_table_if_missing, migrations.RunPython.noop),
    ]
