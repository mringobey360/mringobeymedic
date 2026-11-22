from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0005_populate_category_pharmacy'),
    ]

    # This runs in a fresh migration transaction after the data UPDATEs from
    # 0005 have committed, avoiding PostgreSQL's "pending trigger events"
    # error when changing the FK from nullable to NOT NULL.
    operations = [
        migrations.AlterField(
            model_name='category',
            name='pharmacy',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='categories',
                to='pharmacies.pharmacy',
            ),
        ),
        migrations.AddConstraint(
            model_name='category',
            constraint=models.UniqueConstraint(
                fields=('pharmacy', 'name'),
                name='uniq_category_per_pharmacy',
            ),
        ),
    ]
