import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from core.backup_utils import build_backup_zip


class Command(BaseCommand):
    help = (
        "Creates a complete, timestamped backup: a full database dump (JSON fixture) "
        "PLUS all uploaded media (profile photos, etc.), bundled into a single .zip file. "
        "Saved to BACKUP_DIR on local disk AND pushed to the 'backups/' folder of whichever "
        "storage backend is active (S3 bucket in production, so it survives even if this "
        "container/volume is wiped). Old backups beyond --keep are automatically deleted "
        "from both locations. Meant to be run daily by a scheduled job (see docs/BACKUPS.md)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--keep', type=int, default=14,
            help="Number of most recent backups to keep (older ones are deleted). Default: 14."
        )
        parser.add_argument(
            '--skip-local', action='store_true',
            help="Don't write a copy to local disk — useful on Railway cron, where the "
                 "container is destroyed right after the command exits anyway."
        )

    def handle(self, *args, **options):
        filename, zip_bytes = build_backup_zip()
        size_mb = len(zip_bytes) / (1024 * 1024)

        if not options['skip_local']:
            backup_dir = settings.BACKUP_DIR
            os.makedirs(backup_dir, exist_ok=True)
            archive_path = os.path.join(backup_dir, filename)
            with open(archive_path, 'wb') as f:
                f.write(zip_bytes)
            self.stdout.write(self.style.SUCCESS(f"Local backup written: {archive_path} ({size_mb:.2f} MB)"))

            # Rotate old local backups
            keep = options['keep']
            backups = sorted(
                [f for f in os.listdir(backup_dir) if f.startswith('pharmacy_backup_') and f.endswith('.zip')]
            )
            if len(backups) > keep:
                for old_file in backups[:-keep]:
                    os.remove(os.path.join(backup_dir, old_file))
                    self.stdout.write(f"Removed old local backup: {old_file}")

        # Off-box copy: pushed to whichever storage backend Django is
        # currently configured with (the S3-compatible bucket in production,
        # per docs/STORAGE.md — same one media files use). This is the copy
        # that actually protects you if the server/phone is lost, stolen, or
        # its disk is wiped on the next deploy.
        remote_name = f'backups/{filename}'
        try:
            default_storage.save(remote_name, ContentFile(zip_bytes))
            self.stdout.write(self.style.SUCCESS(f"Off-box backup saved to storage: {remote_name} ({size_mb:.2f} MB)"))

            keep = options['keep']
            try:
                _dirs, remote_files = default_storage.listdir('backups')
            except (FileNotFoundError, NotImplementedError):
                remote_files = []
            remote_backups = sorted(f for f in remote_files if f.startswith('pharmacy_backup_') and f.endswith('.zip'))
            if len(remote_backups) > keep:
                for old_file in remote_backups[:-keep]:
                    default_storage.delete(f'backups/{old_file}')
                    self.stdout.write(f"Removed old off-box backup: {old_file}")
        except Exception as exc:
            self.stderr.write(self.style.WARNING(
                f"Could not save off-box backup ({exc}). The local copy above (if any) is all you have "
                "for this run — configure an S3-compatible bucket or a persistent volume so backups "
                "survive a redeploy."
            ))

        if options['skip_local']:
            self.stdout.write(
                "Reminder: no local copy was written (--skip-local). If the off-box save above failed, "
                "this run produced NO backup at all."
            )
