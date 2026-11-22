from django.db import migrations

FULL_ACCESS = '__ALL__'


def normalize_assistants(apps, schema_editor):
    ManagerAssistant = apps.get_model('accounts', 'ManagerAssistant')
    all_permissions = [
        'dashboard', 'sales', 'inventory', 'customers', 'debts', 'returns',
        'expenses', 'employees', 'attendance', 'presence', 'chat',
        'announcements', 'reports',
    ]
    for profile in ManagerAssistant.objects.select_related('assistant').all():
        profile.permissions = [FULL_ACCESS] + all_permissions
        # Full operational assistants follow the manager's branch portfolio
        # dynamically. This preserves the stated product policy for existing
        # assistants and ensures future branches become available automatically.
        profile.branch_access_mode = 'ALL'
        profile.assistant.is_staff = False
        profile.assistant.is_superuser = False
        profile.assistant.save(update_fields=['is_staff', 'is_superuser'])
        profile.save(update_fields=['permissions', 'branch_access_mode', 'updated_at'])


def reverse_normalize(apps, schema_editor):
    ManagerAssistant = apps.get_model('accounts', 'ManagerAssistant')
    for profile in ManagerAssistant.objects.all():
        profile.permissions = [p for p in (profile.permissions or []) if p != FULL_ACCESS]
        profile.save(update_fields=['permissions', 'updated_at'])


class Migration(migrations.Migration):
    dependencies = [('accounts', '0024_assistant_full_operational_access')]
    operations = [migrations.RunPython(normalize_assistants, reverse_normalize)]
