"""Optimise every newly uploaded ImageField file before it is stored.

Connected once for all models (see CoreConfig.ready). Only files that were just uploaded
(not yet committed to storage) are touched. If anything fails the original upload is kept,
so a photo upload can never be blocked by this step.
"""
import logging
import os

from django.core.files.base import ContentFile
from django.db import models
from django.db.models.signals import pre_save

from . import image_processing as ip

logger = logging.getLogger(__name__)
_IMAGE_FIELDS = {}


def _image_fields(model):
    fields = _IMAGE_FIELDS.get(model)
    if fields is None:
        fields = [f for f in model._meta.get_fields()
                  if isinstance(f, models.ImageField) and getattr(f, 'concrete', False)]
        _IMAGE_FIELDS[model] = fields
    return fields


def optimize_new_uploads(sender, instance, raw=False, **kwargs):
    if raw:  # fixtures / loaddata
        return
    for field in _image_fields(sender):
        try:
            current = getattr(instance, field.attname, None)
            if not current or getattr(current, '_committed', True):
                continue
            upload = current.file
            try:
                upload.seek(0)
            except Exception:
                pass
            original = upload.read()
            try:
                upload.seek(0)
            except Exception:
                pass
            profile = ip.profile_for(sender._meta.label, field.name)
            result = ip.optimize(original, profile)
            if result is None:
                continue
            stem = os.path.splitext(os.path.basename(current.name or 'image'))[0] or 'image'
            setattr(instance, field.attname, ContentFile(result.data, name=stem[:80] + result.ext))
            logger.info('image optimised %s.%s: %d -> %d bytes (%dx%d)', sender._meta.label, field.name,
                        len(original), len(result.data), result.width, result.height)
        except Exception:
            logger.exception('image optimisation skipped for %s.%s', sender._meta.label, field.name)


def connect():
    pre_save.connect(optimize_new_uploads, dispatch_uid='core.optimize_new_uploads')
