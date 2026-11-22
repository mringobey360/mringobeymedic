from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0003_product_discount_policy'),
        ('pharmacies', '0001_initial'),
    ]

    # Keep this migration schema-only. PostgreSQL can reject ALTER TABLE when
    # the same transaction has pending trigger events from earlier UPDATEs.
    # Data migration and NOT NULL enforcement therefore happen in later
    # migrations, each with its own transaction boundary.
    operations = [
        migrations.AlterField(
            model_name='category',
            name='name',
            field=models.CharField(max_length=100),
        ),
        migrations.AddField(
            model_name='category',
            name='pharmacy',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='categories',
                to='pharmacies.pharmacy',
            ),
        ),
    ]
