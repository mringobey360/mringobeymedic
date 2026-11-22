from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def copy_existing_pins(apps, schema_editor):
    """Move PINs created by the first device-PIN release (per device) to the per-user PIN."""
    TrustedDevice = apps.get_model('accounts', 'TrustedDevice')
    UserPin = apps.get_model('accounts', 'UserPin')
    seen = set()
    for device in TrustedDevice.objects.filter(is_active=True).order_by('-last_used_at', '-created_at'):
        if device.user_id in seen:
            continue
        seen.add(device.user_id)
        UserPin.objects.create(user_id=device.user_id, pin_hash=device.pin_hash)
    TrustedDevice.objects.filter(is_active=False).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0026_trusteddevice'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserPin',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('pin_hash', models.CharField(max_length=128)),
                ('failed_attempts', models.PositiveSmallIntegerField(default=0)),
                ('locked_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='device_pin', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.RunPython(copy_existing_pins, migrations.RunPython.noop),
    ]
