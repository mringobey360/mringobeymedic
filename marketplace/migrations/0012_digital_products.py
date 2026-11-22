"""Digital downloads: Product.kind / digital_file* , OrderItem.is_digital and the DownloadGrant table.

Written to be SAFE TO RE-RUN on a database that already contains some of these columns or the table
(for example from an earlier deployment of the same feature): the model state is always updated, but
a column/table is created only when it is missing from the real database.
"""
import uuid

import django.db.models.deletion
from django.db import migrations, models

import marketplace.storage


def _columns(schema_editor, table):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        return {col.name for col in connection.introspection.get_table_description(cursor, table)}


def create_missing(apps, schema_editor):
    connection = schema_editor.connection
    for model_name, field_names in (
        ('Product', ('kind', 'digital_file', 'digital_file_name', 'digital_file_size')),
        ('OrderItem', ('is_digital',)),
    ):
        model = apps.get_model('marketplace', model_name)
        existing = _columns(schema_editor, model._meta.db_table)
        for name in field_names:
            field = model._meta.get_field(name)
            if field.column not in existing:
                schema_editor.add_field(model, field)
    grant = apps.get_model('marketplace', 'DownloadGrant')
    if grant._meta.db_table not in connection.introspection.table_names():
        schema_editor.create_model(grant)


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0011_product_gallery_verified_reports'),
    ]

    operations = [
        # 1) Model state (never touches the database).
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AddField(
                    model_name='product', name='kind',
                    field=models.CharField(choices=[('PHYSICAL', 'Physical product'), ('DIGITAL', 'Digital download')], db_index=True, default='PHYSICAL', max_length=10),
                ),
                migrations.AddField(
                    model_name='product', name='digital_file',
                    field=models.FileField(blank=True, max_length=255, storage=marketplace.storage.private_storage, upload_to=marketplace.storage.digital_upload_to),
                ),
                migrations.AddField(
                    model_name='product', name='digital_file_name',
                    field=models.CharField(blank=True, max_length=180),
                ),
                migrations.AddField(
                    model_name='product', name='digital_file_size',
                    field=models.PositiveBigIntegerField(default=0),
                ),
                migrations.AlterField(
                    model_name='product', name='stock',
                    field=models.PositiveIntegerField(default=0, help_text='Units on the shelf. Not used for digital downloads.'),
                ),
                migrations.AddField(
                    model_name='orderitem', name='is_digital',
                    field=models.BooleanField(default=False, help_text='Snapshot at purchase time: delivered by download link, not by courier.'),
                ),
                migrations.CreateModel(
                    name='DownloadGrant',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                        ('max_downloads', models.PositiveSmallIntegerField(default=10)),
                        ('download_count', models.PositiveSmallIntegerField(default=0)),
                        ('revoked', models.BooleanField(default=False)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('last_downloaded_at', models.DateTimeField(blank=True, null=True)),
                        ('order_item', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='download', to='marketplace.orderitem')),
                    ],
                ),
            ],
        ),
        # 2) Real database: create only what is missing.
        migrations.RunPython(create_missing, migrations.RunPython.noop),
    ]
