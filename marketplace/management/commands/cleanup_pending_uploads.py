"""Remove abandoned direct uploads so the bucket does not fill with half-sent movies.

Schedule daily (Railway cron): python manage.py cleanup_pending_uploads
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from marketplace import directupload as du
from marketplace.models import PendingUpload

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Abort stale multipart uploads and delete finished uploads that were never attached to a product.'

    def handle(self, *args, **opts):
        from marketplace.direct_upload_views import _client
        now = timezone.now()
        client = _client()
        aborted = deleted = 0
        for p in PendingUpload.objects.filter(status=PendingUpload.Status.UPLOADING, created_at__lt=now - timedelta(days=du.RESUME_WINDOW_DAYS)):
            try:
                client.abort(du.bucket_key(p.storage_name), p.s3_upload_id)
            except Exception:
                log.warning('abort failed for %s', p.pk, exc_info=True)
            p.status = PendingUpload.Status.ABORTED
            p.save(update_fields=['status', 'updated_at'])
            aborted += 1
        for p in PendingUpload.objects.filter(status=PendingUpload.Status.COMPLETE, consumed=False, created_at__lt=now - timedelta(days=2)):
            try:
                client.delete(du.bucket_key(p.storage_name))
            except Exception:
                log.warning('delete failed for %s', p.pk, exc_info=True)
                continue
            p.delete()
            deleted += 1
        PendingUpload.objects.filter(status__in=[PendingUpload.Status.FAILED, PendingUpload.Status.ABORTED], updated_at__lt=now - timedelta(days=7)).delete()
        self.stdout.write(f'aborted {aborted} stale uploads, deleted {deleted} unattached files')
