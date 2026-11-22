from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0008_manager_assistant'),
        ('pharmacies', '0007_professional_presence_settings'),
    ]

    operations = [
        migrations.AddField(
            model_name='managerassistant',
            name='branch_access_mode',
            field=models.CharField(
                choices=[('ALL', 'All Manager Branches'), ('SELECTED', 'Selected Branches')],
                default='ALL', max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='managerassistant',
            name='branches',
            field=models.ManyToManyField(
                blank=True, related_name='selected_manager_assistants',
                to='pharmacies.pharmacy',
            ),
        ),
    ]
