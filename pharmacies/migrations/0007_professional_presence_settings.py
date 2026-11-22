from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('pharmacies', '0006_branch_location_audit')]
    operations = [
        migrations.AddField(
            model_name='pharmacy',
            name='presence_check_interval_minutes',
            field=models.PositiveIntegerField(default=15, help_text='Minimum interval between automatic employee presence checks while the workday page is active.'),
        ),
    ]
