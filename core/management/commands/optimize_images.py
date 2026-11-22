"""Optimise images that were uploaded BEFORE automatic optimisation existed.

    python manage.py optimize_images                 # dry run: shows what would change
    python manage.py optimize_images --apply         # really rewrite files
    python manage.py optimize_images --apply --model accounts.User --limit 50

Safe by design: dry run by default, one file at a time, the old file is deleted only after
the new one is stored and the database row points to it. Photos are never cropped.
"""
import os

from django.apps import apps
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import models

from core import image_processing as ip


class Command(BaseCommand):
    help = 'Resize/compress existing uploaded images (profile photos, marketplace photos, logos).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually rewrite files (default is a dry run).')
        parser.add_argument('--model', help='Limit to one model, e.g. accounts.User or marketplace.Product.')
        parser.add_argument('--limit', type=int, default=0, help='Stop after N rewritten files.')
        parser.add_argument('--min-saving', type=float, default=0.10,
                            help='Skip unless the file shrinks by at least this fraction (default 0.10).')

    def handle(self, *args, **opts):
        apply_changes = opts['apply']
        wanted = (opts['model'] or '').lower()
        done = saved_total = scanned = failed = 0
        for model in apps.get_models():
            if wanted and model._meta.label_lower != wanted:
                continue
            for field in [f for f in model._meta.get_fields()
                          if isinstance(f, models.ImageField) and getattr(f, 'concrete', False)]:
                profile = ip.profile_for(model._meta.label, field.name)
                qs = model._default_manager.exclude(**{field.name: ''}).exclude(**{field.name + '__isnull': True})
                for obj in qs.only('pk', field.name).iterator(chunk_size=100):
                    name = getattr(obj, field.attname).name
                    if not name:
                        continue
                    scanned += 1
                    try:
                        with field.storage.open(name, 'rb') as fh:
                            original = fh.read()
                        result = ip.optimize(original, profile)
                        if result is None:
                            continue
                        saving = 1 - len(result.data) / max(len(original), 1)
                        needs_square = profile.square
                        if saving < opts['min_saving'] and not needs_square:
                            continue
                        if needs_square and saving < 0 and result.width == result.height and \
                                len(original) < 40_000:
                            continue
                        label = f'{model._meta.label}.{field.name} #{obj.pk} {name}'
                        self.stdout.write(f'{label}: {len(original)//1024} KB -> {len(result.data)//1024} KB '
                                          f'({result.width}x{result.height})')
                        if apply_changes:
                            folder = os.path.dirname(name)
                            stem = os.path.splitext(os.path.basename(name))[0]
                            new_name = field.storage.save(os.path.join(folder, stem + result.ext),
                                                          ContentFile(result.data))
                            model._default_manager.filter(pk=obj.pk).update(**{field.name: new_name})
                            if new_name != name:
                                field.storage.delete(name)
                        done += 1
                        saved_total += max(len(original) - len(result.data), 0)
                        if opts['limit'] and done >= opts['limit']:
                            return self._summary(apply_changes, scanned, done, saved_total, failed)
                    except Exception as exc:  # keep going; one bad file must not stop the run
                        failed += 1
                        self.stderr.write(f'skipped {model._meta.label}.{field.name} #{obj.pk}: {exc}')
        self._summary(apply_changes, scanned, done, saved_total, failed)

    def _summary(self, applied, scanned, done, saved, failed):
        mode = 'rewritten' if applied else 'would be rewritten (dry run, use --apply)'
        self.stdout.write(self.style.SUCCESS(
            f'Scanned {scanned} images; {done} {mode}; ~{saved // 1024} KB saved; {failed} skipped.'))
