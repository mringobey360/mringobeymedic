"""Shrink existing Marketplace product photos and shop logos (new uploads are optimised automatically).

Usage:  python manage.py optimize_marketplace_images --dry-run
        python manage.py optimize_marketplace_images
"""
from django.core.management.base import BaseCommand

from marketplace.models import Product, SellerStore, optimize_image_field


class Command(BaseCommand):
    help = 'Resize and recompress large existing Marketplace images to speed up the storefront.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only report what would change.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        done = skipped = failed = 0
        jobs = [(p, 'image', 1000) for p in Product.objects.exclude(image='').only('id', 'image')]
        jobs += [(s, 'logo', 600) for s in SellerStore.objects.exclude(logo='').only('id', 'logo')]
        for obj, attr, max_px in jobs:
            field = getattr(obj, attr)
            old_name = field.name
            try:
                if dry:
                    self.stdout.write(f'would check {old_name}')
                    continue
                storage = field.storage
                field.open('rb')
                changed = optimize_image_field(field, max_px=max_px, force=True)
                if changed:
                    # Save only this column (does not trigger Product.save re-optimisation).
                    type(obj).objects.filter(pk=obj.pk).update(**{attr: field.name})
                    try:
                        storage.delete(old_name)
                    except Exception:
                        pass
                    done += 1
                    self.stdout.write(self.style.SUCCESS(f'optimised {old_name} -> {field.name}'))
                else:
                    skipped += 1
            except Exception as exc:  # keep going; one bad file must not stop the batch
                failed += 1
                self.stderr.write(f'failed {old_name}: {exc}')
        self.stdout.write(f'Done. optimised={done} skipped={skipped} failed={failed}')
