from django.db import migrations


class Migration(migrations.Migration):
    """The PIN now lives on UserPin; a TrustedDevice only identifies a browser."""

    dependencies = [
        ('accounts', '0027_userpin'),
    ]

    operations = [
        migrations.RemoveField(model_name='trusteddevice', name='pin_hash'),
        migrations.RemoveField(model_name='trusteddevice', name='failed_attempts'),
        migrations.RemoveField(model_name='trusteddevice', name='is_active'),
    ]
