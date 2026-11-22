from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0033_single_device_login')]

    operations = [
        migrations.AlterField(
            model_name='user', name='role',
            field=models.CharField(choices=[('ADMIN', 'Shop Owner'), ('ASSISTANT', 'Manager Assistant'), ('EMPLOYEE', 'Shop Employee'), ('BRANCH_DISPLAY', 'Branch Attendance Display')], default='EMPLOYEE', max_length=20),
        ),
    ]
