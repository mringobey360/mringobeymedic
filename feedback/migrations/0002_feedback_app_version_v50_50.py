from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('feedback', '0001_initial')]
    operations = [
        migrations.AlterField(
            model_name='feedback',
            name='app_version',
            field=models.CharField(default='V50.50', max_length=32),
        ),
    ]
