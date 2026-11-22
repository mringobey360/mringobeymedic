from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('expenses', '0007_automatic_day_end')]

    operations = [
        migrations.AddField(
            model_name='cashday',
            name='opening_is_provisional',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='cashday',
            name='opening_provisional_note',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='cashday',
            name='opening_reconciled_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
