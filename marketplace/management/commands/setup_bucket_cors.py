"""Allow browsers on your site to upload movie parts straight to the storage bucket.

Run once after the bucket is connected (and again if your domain changes):

    python manage.py setup_bucket_cors                      # uses CSRF_TRUSTED_ORIGINS
    python manage.py setup_bucket_cors --origin https://mringobey.store
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Set the bucket CORS rules needed for direct (browser to bucket) uploads.'

    def add_arguments(self, parser):
        parser.add_argument('--origin', action='append', default=[], help='Site origin, e.g. https://example.com (repeatable).')

    def handle(self, *args, **opts):
        if not getattr(settings, 'USE_S3', False):
            raise CommandError('No bucket is configured (AWS_STORAGE_BUCKET_NAME / AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY).')
        origins = [o.rstrip('/') for o in opts['origin']] or [o.rstrip('/') for o in getattr(settings, 'CSRF_TRUSTED_ORIGINS', []) if o.startswith('http')]
        if not origins:
            raise CommandError('Give at least one site origin with --origin https://your-domain')
        from marketplace.direct_upload_views import _client
        rules = {'CORSRules': [{
            'AllowedOrigins': origins,
            'AllowedMethods': ['PUT', 'GET', 'HEAD'],
            'AllowedHeaders': ['*'],
            'ExposeHeaders': ['ETag'],          # the browser must read each part's ETag
            'MaxAgeSeconds': 3600,
        }]}
        _client().c.put_bucket_cors(Bucket=settings.AWS_STORAGE_BUCKET_NAME, CORSConfiguration=rules)
        self.stdout.write(self.style.SUCCESS('CORS set for: ' + ', '.join(origins)))
