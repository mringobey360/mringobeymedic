from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0013_attendance_method_qr')]
    operations = [
        migrations.AddField(
            model_name='user', name='attendance_methods',
            field=models.CharField(default='GPS,MANUAL', max_length=24),
        ),
    ]
