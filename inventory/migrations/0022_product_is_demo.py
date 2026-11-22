from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0021_flexible_units'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='is_demo',
            field=models.BooleanField(
                db_index=True, default=False,
                help_text='Sample product loaded for a new Free Plan account. The owner can remove all sample products in one click.',
            ),
        ),
    ]
