from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('expenses', '0008_provisional_opening_control')]

    operations = [
        migrations.AddField(
            model_name='cashcount',
            name='correction_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='cashcount',
            name='supersedes',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='corrections', to='expenses.cashcount'),
        ),
    ]
