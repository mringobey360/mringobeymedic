from django.db import migrations


FULL_ACCESS = '__ALL__'


def grant_full_access(apps, schema_editor):
    ManagerAssistant = apps.get_model('accounts', 'ManagerAssistant')
    User = apps.get_model('accounts', 'User')
    all_permissions = [
        'dashboard', 'sales', 'inventory', 'customers', 'debts', 'returns',
        'expenses', 'employees', 'attendance', 'presence', 'chat',
        'announcements', 'reports',
    ]
    for profile in ManagerAssistant.objects.filter(active=True, assistant__is_active=True):
        profile.permissions = [FULL_ACCESS] + all_permissions
        profile.save(update_fields=['permissions', 'updated_at'])


def revoke_full_access(apps, schema_editor):
    ManagerAssistant = apps.get_model('accounts', 'ManagerAssistant')
    all_permissions = {
        'dashboard', 'sales', 'inventory', 'customers', 'debts', 'returns',
        'expenses', 'employees', 'attendance', 'presence', 'chat',
        'announcements', 'reports',
    }
    for profile in ManagerAssistant.objects.all():
        profile.permissions = [p for p in (profile.permissions or []) if p != FULL_ACCESS]
        profile.save(update_fields=['permissions', 'updated_at'])


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0023_professional_meal_scan_reopen'),
    ]

    operations = [
        migrations.RunPython(grant_full_access, revoke_full_access),
    ]
