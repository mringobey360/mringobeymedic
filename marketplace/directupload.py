"""Direct-to-bucket (S3 / R2 / Railway bucket) multipart uploads for large files such as movies.

Why: pushing a 1 GB movie through the web server (phone -> web -> gunicorn -> bucket) is slow,
ties up a worker and fails on weak mobile networks. Here the browser sends 8 MB parts straight to the
bucket with short-lived signed URLs; the web server only signs, verifies and records.

This module does not import Django, so it can be unit-tested with a fake S3 client.
"""
import math
import os
import uuid
from io import BytesIO

MB = 1024 * 1024
MIN_PART = 8 * MB            # S3 requires >= 5 MB for every part except the last
MAX_PARTS = 9000             # S3 limit is 10,000
SIGN_TTL = 3600              # a signed part URL is valid for one hour
MAX_SIGN_BATCH = 40
RESUME_WINDOW_DAYS = 3
PRIVATE_PREFIX = 'private'   # must match storage.private_storage(location='private')


class DirectUploadError(Exception):
    """Carries a message that is safe to show to the seller."""

    def __init__(self, message, code='error', status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def part_size_for(size):
    """8 MB parts, grown in whole MB when a file would need more than MAX_PARTS parts."""
    needed = math.ceil(size / MAX_PARTS) if size else MIN_PART
    return max(MIN_PART, int(math.ceil(needed / MB)) * MB)


def total_parts(size, part_size):
    return max(1, int(math.ceil(size / part_size)))


def new_storage_name(store_id, filename):
    """Random, non-guessable name; identical shape to storage.digital_upload_to."""
    ext = os.path.splitext(filename or '')[1].lower()[:10]
    return f'digital/store-{int(store_id or 0)}/{uuid.uuid4().hex}{ext}'


def bucket_key(storage_name):
    return f'{PRIVATE_PREFIX}/{storage_name}'


def content_type_for(filename):
    import mimetypes
    return mimetypes.guess_type(filename or '')[0] or 'application/octet-stream'


class HeadFile:
    """Just enough of a file object for validate_digital_upload(): name, size, seek/read."""

    def __init__(self, name, size, head):
        self.name, self.size = name, size
        self._buf = BytesIO(head)

    def seek(self, *a):
        return self._buf.seek(*a)

    def read(self, *a):
        return self._buf.read(*a)


class MultipartClient:
    """Thin wrapper over a boto3 S3 client. ``client`` is injected so tests can fake it."""

    def __init__(self, client, bucket):
        self.c, self.bucket = client, bucket

    def create(self, key, content_type):
        r = self.c.create_multipart_upload(Bucket=self.bucket, Key=key, ContentType=content_type)
        return r['UploadId']

    def sign_part(self, key, upload_id, number):
        return self.c.generate_presigned_url(
            'upload_part', ExpiresIn=SIGN_TTL,
            Params={'Bucket': self.bucket, 'Key': key, 'UploadId': upload_id, 'PartNumber': number})

    def list_parts(self, key, upload_id):
        """All parts the bucket really has, as {number: {'etag', 'size'}} (follows pagination)."""
        out, marker = {}, 0
        while True:
            kw = dict(Bucket=self.bucket, Key=key, UploadId=upload_id, MaxParts=1000)
            if marker:
                kw['PartNumberMarker'] = marker
            r = self.c.list_parts(**kw)
            for p in r.get('Parts', []):
                out[int(p['PartNumber'])] = {'etag': p['ETag'], 'size': int(p['Size'])}
            if not r.get('IsTruncated'):
                return out
            marker = int(r.get('NextPartNumberMarker') or 0)
            if not marker:
                return out

    def complete(self, key, upload_id, parts):
        ordered = [{'PartNumber': n, 'ETag': parts[n]['etag']} for n in sorted(parts)]
        self.c.complete_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id,
                                         MultipartUpload={'Parts': ordered})

    def abort(self, key, upload_id):
        self.c.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)

    def object_size(self, key):
        return int(self.c.head_object(Bucket=self.bucket, Key=key)['ContentLength'])

    def head_bytes(self, key, n=16):
        r = self.c.get_object(Bucket=self.bucket, Key=key, Range=f'bytes=0-{n - 1}')
        return r['Body'].read()

    def delete(self, key):
        self.c.delete_object(Bucket=self.bucket, Key=key)


def is_no_such_upload(exc):
    """True when the bucket says this multipart upload no longer exists (expired or aborted)."""
    resp = getattr(exc, 'response', None) or {}
    return (resp.get('Error') or {}).get('Code') in ('NoSuchUpload', 'NoSuchKey', '404')


def verify_parts(parts, size, part_size):
    """Check that the bucket holds every part with the right size. Returns an error message or None."""
    n_parts = total_parts(size, part_size)
    missing = [n for n in range(1, n_parts + 1) if n not in parts]
    if missing:
        return 'Some parts did not reach the server (%d missing). Resume the upload to send them.' % len(missing)
    for n in range(1, n_parts + 1):
        expected = part_size if n < n_parts else size - part_size * (n_parts - 1)
        if parts[n]['size'] != expected:
            return 'Part %d arrived incomplete. Resume the upload to send it again.' % n
    return None
