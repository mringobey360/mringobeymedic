from django.core.management.base import BaseCommand
from accounts.models import ManagerAssistant


class Command(BaseCommand):
    help = 'Verify Manager Assistant operational access and branch scope.'

    def handle(self, *args, **options):
        qs = ManagerAssistant.objects.select_related('assistant', 'manager').all()
        self.stdout.write(f'Assistants: {qs.count()}')
        failures = 0
        for p in qs:
            branches = list(p.accessible_pharmacies().values_list('id', 'name')) if p.active else []
            full = '__ALL__' in (p.permissions or [])
            operational = p.assistant.has_assistant_permission('dashboard')
            ok = (not p.active) or (p.branch_access_mode == 'ALL' and full and operational)
            if not ok:
                failures += 1
            self.stdout.write(
                f'{p.assistant.username} | manager={p.manager.username} | active={p.active} | '
                f'mode={p.branch_access_mode} | full={full} | operational={operational} | branches={branches}'
            )
        if failures:
            raise SystemExit(f'{failures} assistant record(s) failed verification.')
        self.stdout.write(self.style.SUCCESS('Manager Assistant access verification passed.'))
