"""Read-only production schema audit: detect stale required columns that break inserts."""
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Audit managed model tables for required DB columns unknown to Django (read-only).'

    def handle(self, *args, **options):
        problems = []
        with connection.cursor() as cursor:
            tables = set(connection.introspection.table_names(cursor))
            for model in apps.get_models():
                if not model._meta.managed or model._meta.proxy:
                    continue
                table = model._meta.db_table
                if table not in tables:
                    problems.append(f'MISSING TABLE {table}')
                    continue
                expected = {f.column for f in model._meta.local_concrete_fields}
                description = connection.introspection.get_table_description(cursor, table)
                actual = {c.name for c in description}
                for name in sorted(expected - actual):
                    problems.append(f'MISSING MODEL COLUMN {table}.{name}')
                for col in description:
                    if col.name not in expected and col.null_ok is False and col.default is None:
                        problems.append(f'UNMAPPED REQUIRED COLUMN {table}.{col.name} (potential INSERT failure)')
        if problems:
            for problem in problems:
                self.stdout.write(self.style.WARNING(problem))
            self.stdout.write(self.style.WARNING(f'{len(problems)} potential schema problems found. No changes made.'))
        else:
            self.stdout.write(self.style.SUCCESS('No obvious schema mismatches found.'))
