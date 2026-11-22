from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0029_pin_security_audit')]

    operations = [
        migrations.AddField(
            model_name='userpin',
            name='locked_until',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
