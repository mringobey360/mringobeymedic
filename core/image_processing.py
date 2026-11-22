"""Server-side image normalisation for every uploaded photo.

Goals
-----
* Nothing is ever cropped. Photos are only scaled down (never up) and, for profile
  photos, placed on a square canvas with a soft blurred backdrop so the WHOLE photo is kept.
* Small, fast files for weak mobile networks: EXIF rotation is applied, metadata (including
  GPS) is stripped, large photos are resized and saved as progressive JPEG (renders in passes
  while it downloads). Transparent PNG logos stay PNG.
* Fails safe: if anything goes wrong the original upload is stored untouched.

This module only depends on Pillow so it can be unit-tested without Django.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageOps

# Refuse decompression bombs (a tiny file that expands to gigabytes of pixels).
MAX_PIXELS = 60_000_000


@dataclass(frozen=True)
class Profile:
    name: str
    max_side: int            # longest side after resizing (never upscaled)
    quality: int = 82
    square: bool = False     # place on a square canvas, whole photo visible
    keep_alpha: bool = True  # keep PNG transparency (logos); otherwise flatten on white


PRODUCT = Profile('product', max_side=1600, quality=82)
LISTING = Profile('listing', max_side=1920, quality=82)
AVATAR = Profile('avatar', max_side=640, quality=86, square=True, keep_alpha=False)
LOGO = Profile('logo', max_side=1000, quality=88, keep_alpha=True)
STOCK = Profile('stock', max_side=900, quality=80, keep_alpha=False)   # POS / stock-list photos: light on weak connections

# (app_label.model_name, field_name) -> profile. Any other ImageField uses PRODUCT.
FIELD_PROFILES = {
    ('accounts.user', 'profile_photo'): AVATAR,
    ('marketplace.product', 'image'): PRODUCT,
    ('marketplace.productimage', 'image'): PRODUCT,
    ('marketplace.listingimage', 'image'): LISTING,
    ('marketplace.sellerstore', 'logo'): LOGO,
    ('pharmacies.pharmacy', 'logo'): LOGO,
    ('inventory.product', 'image'): STOCK,
}
DEFAULT_PROFILE = PRODUCT


def profile_for(model_label: str, field_name: str) -> Profile:
    return FIELD_PROFILES.get((model_label.lower(), field_name), DEFAULT_PROFILE)


def _has_alpha(img: Image.Image) -> bool:
    if img.mode in ('RGBA', 'LA'):
        return img.getchannel('A').getextrema()[0] < 255
    return img.mode == 'P' and 'transparency' in img.info


def _to_rgb(img: Image.Image, keep_alpha: bool) -> Image.Image:
    """Return RGB (or RGBA when transparency must survive)."""
    if img.mode == 'P':
        img = img.convert('RGBA')
    if img.mode in ('RGBA', 'LA'):
        if keep_alpha and _has_alpha(img):
            return img.convert('RGBA')
        base = Image.new('RGB', img.size, (255, 255, 255))
        base.paste(img.convert('RGBA'), mask=img.convert('RGBA').getchannel('A'))
        return base
    if img.mode in ('I;16', 'I', 'F'):
        img = img.point(lambda v: v * (1 / 256)).convert('L') if img.mode == 'I;16' else img.convert('L')
    return img.convert('RGB')


def _square_with_backdrop(img: Image.Image, side: int) -> Image.Image:
    """Whole photo, centred, on a blurred and softened copy of itself (nothing is cut off)."""
    img = img.convert('RGB')
    w, h = img.size
    scale = side / max(w, h)
    fg_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    fg = img.resize(fg_size, Image.LANCZOS) if fg_size != img.size else img
    if fg_size[0] == fg_size[1]:
        return fg.resize((side, side), Image.LANCZOS) if fg.size != (side, side) else fg
    bg = ImageOps.fit(img, (side, side), Image.BILINEAR)
    bg = bg.resize((64, 64), Image.BILINEAR).resize((side, side), Image.BILINEAR)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=max(6, side // 24)))
    veil = Image.new('RGB', (side, side), (255, 255, 255))
    bg = Image.blend(bg, veil, 0.18)
    bg.paste(fg, ((side - fg_size[0]) // 2, (side - fg_size[1]) // 2))
    return bg


@dataclass
class Result:
    data: bytes
    ext: str            # '.jpg' or '.png'
    content_type: str
    width: int
    height: int
    changed: bool


def optimize(data: bytes, profile: Profile = DEFAULT_PROFILE) -> Result | None:
    """Normalise one image. Returns None when the original should be kept as is
    (animated, unreadable, unsupported, or no smaller/better result)."""
    try:
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        src = Image.open(io.BytesIO(data))
        src.load()
    except Exception:
        return None
    if getattr(src, 'is_animated', False) or getattr(src, 'n_frames', 1) > 1:
        return None
    if (src.format or '').upper() not in ('JPEG', 'PNG', 'WEBP', 'MPO', 'BMP', 'GIF', 'TIFF'):
        return None

    try:
        oriented = ImageOps.exif_transpose(src)
    except Exception:
        oriented = src
    rotated = oriented is not src and oriented.size != src.size or bool(src.getexif().get(0x0112, 1) not in (0, 1))
    img = _to_rgb(oriented, profile.keep_alpha)
    transparent = img.mode == 'RGBA'

    w, h = img.size
    if profile.square:
        side = min(profile.max_side, max(w, h))
        side = max(side, 64)
        out_img = _square_with_backdrop(img, side)
        already_square = (w == h)
    else:
        scale = min(1.0, profile.max_side / max(w, h))
        out_img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS) if scale < 1 else img
        already_square = True
    resized = out_img.size != (w, h)

    buf = io.BytesIO()
    if transparent:
        out_img.save(buf, 'PNG', optimize=True)
        ext, ctype = '.png', 'image/png'
    else:
        out_img.convert('RGB').save(buf, 'JPEG', quality=profile.quality, optimize=True,
                                    progressive=True, subsampling='4:2:0')
        ext, ctype = '.jpg', 'image/jpeg'
    out = buf.getvalue()

    needs_change = resized or rotated or (profile.square and not already_square) \
        or (src.format or '').upper() not in ('JPEG', 'PNG') or src.info.get('exif') is not None \
        or src.info.get('progressive') is None and (src.format or '').upper() == 'JPEG' and len(data) > 150_000
    if not needs_change and len(out) >= len(data):
        return None
    if not resized and not rotated and not (profile.square and not already_square) and len(out) >= len(data):
        return None
    return Result(out, ext, ctype, out_img.size[0], out_img.size[1], True)
