"""Private storage for paid digital products (PDF books, videos, audio, courses).

Files stored here are NEVER served from MEDIA_URL. Buyers reach them only through
`marketplace.digital_views.download_file`, which re-checks that the order is paid first.

* S3 / Cloudflare R2 configured -> private bucket prefix, signed URLs that expire in 5 minutes.
* No bucket configured          -> a local folder outside MEDIA_ROOT (mount a persistent volume on it).
"""
import os
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage


def private_storage():
    if getattr(settings, 'USE_S3', False):
        from storages.backends.s3 import S3Storage
        return S3Storage(location='private', querystring_auth=True, querystring_expire=300,
                         default_acl='private', file_overwrite=False, custom_domain=False)
    root = getattr(settings, 'PRIVATE_MEDIA_ROOT', None) or (Path(settings.BASE_DIR) / 'private_media')
    return FileSystemStorage(location=str(root))


def digital_upload_to(instance, filename):
    """Random, non-guessable storage name; the seller's original file name is kept separately for display."""
    ext = os.path.splitext(filename or '')[1].lower()[:10]
    return f'digital/store-{instance.seller_store_id or 0}/{uuid.uuid4().hex}{ext}'


def preview_upload_to(instance, filename):
    """Random, non-guessable name for the PUBLIC free sample, so its raw file cannot be found by guessing a URL."""
    ext = os.path.splitext(filename or '')[1].lower()[:10]
    return f'marketplace/previews/{uuid.uuid4().hex}{ext}'
