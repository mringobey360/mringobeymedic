"""JSON endpoints for direct-to-bucket multipart uploads (see directupload.py)."""
import json
import logging
import os
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import directupload as du
from .digital import DIGITAL_MAX_BYTES, validate_digital_upload
from .models import PendingUpload
from .movies import MOVIE_FILE_EXT

logger = logging.getLogger(__name__)


def direct_upload_enabled():
    return bool(getattr(settings, 'USE_S3', False))


def _client():
    import boto3
    from botocore.config import Config
    kw = dict(aws_access_key_id=settings.AWS_ACCESS_KEY_ID, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
              region_name=settings.AWS_S3_REGION_NAME or 'auto',
              config=Config(signature_version='s3v4', retries={'max_attempts': 3, 'mode': 'standard'},
                            s3={'addressing_style': getattr(settings, 'AWS_S3_ADDRESSING_STYLE', 'auto')}))
    if settings.AWS_S3_ENDPOINT_URL:
        kw['endpoint_url'] = settings.AWS_S3_ENDPOINT_URL
    return du.MultipartClient(boto3.client('s3', **kw), settings.AWS_STORAGE_BUCKET_NAME)


def _fail(message, code='error', status=400):
    return JsonResponse({'ok': False, 'code': code, 'message': message}, status=status)


def _body(request):
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        raise du.DirectUploadError('The request was not understood. Reload the page and try again.', 'bad_request')
    if not isinstance(data, dict):
        raise du.DirectUploadError('The request was not understood. Reload the page and try again.', 'bad_request')
    return data


def _store(request, store_id):
    from .views import _seller_store_for_user
    return _seller_store_for_user(request, store_id)


def _owned(request, store, upload_id):
    return get_object_or_404(PendingUpload, pk=upload_id, seller_store=store, user=request.user)


def _guard(view):
    """Common error handling: turn DirectUploadError / storage failures into friendly JSON."""
    def wrapper(request, store_id, *a, **kw):
        if not direct_upload_enabled():
            return _fail('Direct upload is not available because no storage bucket is configured. '
                         'The file will be sent with the form instead.', 'disabled', 409)
        try:
            return view(request, _store(request, store_id), *a, **kw)
        except du.DirectUploadError as e:
            return _fail(e.message, e.code, e.status)
        except Exception as e:  # storage / network failure on our side of the bucket
            if du.is_no_such_upload(e):
                return _fail('This upload expired on the server. Start it again.', 'expired', 410)
            logger.exception('Direct upload failure')
            return _fail('The storage service did not respond. Wait a moment and press Retry.', 'storage', 502)
    wrapper.__name__ = view.__name__
    return login_required(require_POST(wrapper))


@_guard
def upload_init(request, store):
    data = _body(request)
    filename = os.path.basename(str(data.get('filename') or ''))[:180]
    try:
        size = int(data.get('size') or 0)
    except (TypeError, ValueError):
        size = 0
    ext = os.path.splitext(filename)[1].lower()
    if ext not in MOVIE_FILE_EXT:
        raise du.DirectUploadError('A movie must be a video file (MP4, MOV, MKV, WebM, AVI, 3GP or MPEG).', 'bad_type')
    if size <= 0:
        raise du.DirectUploadError('The file is empty.', 'empty')
    if size > DIGITAL_MAX_BYTES:
        raise du.DirectUploadError('The file is larger than %d MB.' % (DIGITAL_MAX_BYTES // du.MB), 'too_big')
    client = _client()
    cutoff = timezone.now() - timedelta(days=du.RESUME_WINDOW_DAYS)
    # Same seller, same file, still open: pick up where it stopped.
    existing = PendingUpload.objects.filter(seller_store=store, user=request.user, filename=filename, size=size,
                                            status=PendingUpload.Status.UPLOADING, created_at__gte=cutoff).first()
    if existing:
        try:
            parts = client.list_parts(du.bucket_key(existing.storage_name), existing.s3_upload_id)
            return _init_payload(existing, parts, resumed=bool(parts))
        except Exception as e:
            if not du.is_no_such_upload(e):
                raise
            existing.status, existing.error = PendingUpload.Status.FAILED, 'expired'
            existing.save(update_fields=['status', 'error', 'updated_at'])
    name = du.new_storage_name(store.pk, filename)
    ctype = du.content_type_for(filename)
    upload_id = client.create(du.bucket_key(name), ctype)
    pending = PendingUpload.objects.create(
        seller_store=store, user=request.user, filename=filename, size=size, content_type=ctype,
        storage_name=name, s3_upload_id=upload_id, part_size=du.part_size_for(size))
    return _init_payload(pending, {}, resumed=False)


def _init_payload(p, parts, resumed):
    return JsonResponse({'ok': True, 'id': str(p.pk), 'part_size': p.part_size,
                         'total_parts': du.total_parts(p.size, p.part_size), 'resumed': resumed,
                         'done': [{'n': n, 'size': v['size']} for n, v in sorted(parts.items())]})


@_guard
def upload_sign(request, store):
    data = _body(request)
    p = _owned(request, store, data.get('id'))
    if p.status != PendingUpload.Status.UPLOADING:
        raise du.DirectUploadError('This upload is no longer active.', 'inactive', 409)
    n_parts = du.total_parts(p.size, p.part_size)
    try:
        numbers = sorted({int(n) for n in (data.get('parts') or [])})
    except (TypeError, ValueError):
        numbers = []
    if not numbers or len(numbers) > du.MAX_SIGN_BATCH or numbers[0] < 1 or numbers[-1] > n_parts:
        raise du.DirectUploadError('Invalid part numbers.', 'bad_request')
    client = _client()
    key = du.bucket_key(p.storage_name)
    return JsonResponse({'ok': True, 'urls': {str(n): client.sign_part(key, p.s3_upload_id, n) for n in numbers}})


@_guard
def upload_complete(request, store):
    data = _body(request)
    p = _owned(request, store, data.get('id'))
    if p.status == PendingUpload.Status.COMPLETE:
        return JsonResponse({'ok': True, 'token': str(p.pk), 'name': p.filename, 'size': p.size})
    if p.status != PendingUpload.Status.UPLOADING:
        raise du.DirectUploadError('This upload is no longer active.', 'inactive', 409)
    client = _client()
    key = du.bucket_key(p.storage_name)
    parts = client.list_parts(key, p.s3_upload_id)          # the bucket is the source of truth, not the browser
    problem = du.verify_parts(parts, p.size, p.part_size)
    if problem:
        raise du.DirectUploadError(problem, 'incomplete', 409)
    client.complete(key, p.s3_upload_id, parts)
    if client.object_size(key) != p.size:
        client.delete(key)
        _mark(p, PendingUpload.Status.FAILED, 'size mismatch')
        raise du.DirectUploadError('The file in storage does not match the file you chose. Upload it again.', 'mismatch', 409)
    problem = validate_digital_upload(du.HeadFile(p.filename, p.size, client.head_bytes(key, 16)))
    if problem:
        client.delete(key)
        _mark(p, PendingUpload.Status.FAILED, problem[:250])
        raise du.DirectUploadError(problem + ' The upload was discarded.', 'invalid_file', 422)
    _mark(p, PendingUpload.Status.COMPLETE, '')
    return JsonResponse({'ok': True, 'token': str(p.pk), 'name': p.filename, 'size': p.size})


@_guard
def upload_abort(request, store):
    data = _body(request)
    p = _owned(request, store, data.get('id'))
    if p.status == PendingUpload.Status.UPLOADING:
        try:
            _client().abort(du.bucket_key(p.storage_name), p.s3_upload_id)
        except Exception:
            logger.warning('Could not abort multipart upload %s', p.pk, exc_info=True)
        _mark(p, PendingUpload.Status.ABORTED, '')
    return JsonResponse({'ok': True})


def _mark(p, status, error):
    p.status, p.error = status, error[:255]
    p.save(update_fields=['status', 'error', 'updated_at'])
