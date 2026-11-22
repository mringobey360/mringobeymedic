from django.db import models
from django.conf import settings
from datetime import timedelta
from django.utils import timezone
from core.encrypted_fields import EncryptedTextField
from django.utils.text import slugify
import uuid
from decimal import Decimal

from .storage import digital_upload_to, preview_upload_to, private_storage

import logging
import os
from io import BytesIO

_img_logger = logging.getLogger(__name__)


def _normalise_jfif(field_file):
    """A .jfif photo is an ordinary JPEG: store it as .jpg so every browser and storage backend serves it as an image."""
    try:
        if field_file and not getattr(field_file, '_committed', True):
            base, ext = os.path.splitext(field_file.name)
            if ext.lower() in ('.jfif', '.jpe'):
                field_file.name = base + '.jpg'
    except Exception:
        pass


def optimize_image_field(field_file, max_px=1000, quality=82, force=False):
    """Shrink a freshly uploaded photo so shoppers on mobile data download a few KB, not several MB.

    Only runs for a new, uncommitted upload (or when `force` is used by the management command).
    Never raises: if anything fails the original upload is kept untouched.
    """
    if not field_file:
        return False
    if not force and getattr(field_file, '_committed', True):
        return False
    try:
        from PIL import Image, ImageOps
        from django.core.files.base import ContentFile
        f = field_file.file
        f.seek(0)
        img = Image.open(f)
        fmt = (img.format or 'JPEG').upper()
        if fmt == 'GIF' or getattr(img, 'is_animated', False):
            f.seek(0)
            return False
        size = getattr(f, 'size', 0) or 0
        small = img.width <= max_px and img.height <= max_px
        if small and size and size <= 250 * 1024:
            f.seek(0)
            return False
        img = ImageOps.exif_transpose(img)
        img.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = BytesIO()
        has_alpha = img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info)
        if has_alpha:
            img.convert('RGBA').save(buf, 'PNG', optimize=True)
            ext = '.png'
        else:
            img.convert('RGB').save(buf, 'JPEG', quality=quality, optimize=True, progressive=True)
            ext = '.jpg'
        if size and buf.tell() >= size and small:
            f.seek(0)
            return False
        base = os.path.splitext(os.path.basename(field_file.name))[0]
        field_file.save(base + ext, ContentFile(buf.getvalue()), save=False)
        return True
    except Exception:
        _img_logger.warning('Marketplace image optimisation skipped', exc_info=True)
        try:
            field_file.file.seek(0)
        except Exception:
            pass
        return False


class Category(models.Model):
    """Product category.

    `seller_store` empty  -> platform/legacy category (managed by the platform owner).
    `seller_store` set    -> private to that shop; never shown to or usable by another shop.
    """
    seller_store = models.ForeignKey('SellerStore', null=True, blank=True, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Categories'
        constraints = [
            models.UniqueConstraint(fields=['name'], condition=models.Q(seller_store__isnull=True), name='mk_category_platform_name_uniq'),
            models.UniqueConstraint(fields=['seller_store', 'name'], condition=models.Q(seller_store__isnull=False), name='mk_category_store_name_uniq'),
        ]
    def __str__(self):
        return f'{self.name} ({self.seller_store.public_name})' if self.seller_store_id else self.name

class Product(models.Model):
    class Kind(models.TextChoices):
        PHYSICAL = 'PHYSICAL', 'Physical product'
        DIGITAL = 'DIGITAL', 'Digital download'
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='products')
    name = models.CharField(max_length=180)
    seller_name = models.CharField(max_length=150, blank=True, default='', db_index=True, help_text='Legacy display name; account-linked products use the linked seller store.')
    seller_store = models.ForeignKey('SellerStore', null=True, blank=True, on_delete=models.SET_NULL, related_name='products')
    slug = models.SlugField(max_length=220, unique=True)
    description = models.TextField()
    price = models.DecimalField(max_digits=13, decimal_places=2)
    compare_at_price = models.DecimalField(max_digits=13, decimal_places=2, null=True, blank=True, help_text='Old price before the discount. Shown crossed out next to the current price.')
    image = models.ImageField(upload_to='marketplace/products/', blank=True)
    stock = models.PositiveIntegerField(default=0, help_text='Units on the shelf. Not used for digital downloads.')
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PHYSICAL, db_index=True)
    # Paid digital goods live in PRIVATE storage and are never reachable by a public URL.
    digital_file = models.FileField(upload_to=digital_upload_to, storage=private_storage, blank=True, max_length=255)
    digital_file_name = models.CharField(max_length=180, blank=True)
    digital_file_size = models.PositiveBigIntegerField(default=0)
    # ---- movies: a digital product with a public trailer and a cinema-style card ----
    is_movie = models.BooleanField(default=False, db_index=True, help_text='A film sold as a digital download. Shown with a trailer and a movie card.')
    trailer_file = models.FileField(upload_to='marketplace/trailers/', blank=True, max_length=255, help_text='Short public trailer (MP4/WebM). The full movie stays private until the buyer pays.')
    movie_year = models.PositiveSmallIntegerField(null=True, blank=True)
    movie_minutes = models.PositiveSmallIntegerField(null=True, blank=True, help_text='Running time in minutes.')
    movie_genres = models.CharField(max_length=120, blank=True, help_text='Comma separated, e.g. Action, Drama.')
    movie_quality = models.CharField(max_length=4, blank=True)
    # ---- free preview for digital downloads: public sample + "what's inside" + file facts ----
    preview_file = models.FileField(upload_to=preview_upload_to, blank=True, max_length=255, help_text='Short PUBLIC sample (PDF, MP3, MP4 or WebM). The full product stays private until the buyer pays.')
    whats_inside = models.TextField(blank=True, default='', help_text='One item per line. Shown to buyers as a checklist.')
    content_summary = models.CharField(max_length=80, blank=True, default='', help_text='Size of the full product, e.g. "120 pages" or "8 lessons - 2h 30m".')
    content_language = models.CharField(max_length=30, blank=True, default='', help_text='e.g. Kiswahili, English.')
    featured = models.BooleanField(default=False)
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=0, help_text='Average of visible customer ratings (maintained automatically).')
    rating_count = models.PositiveIntegerField(default=0, help_text='Number of visible customer ratings (maintained automatically).')
    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True, help_text="Removed from seller catalogue; retained for order and download history.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-featured','-created_at']

    # ---- availability: physical goods use stock, digital goods are available while a file is attached ----
    @property
    def is_digital(self):
        return self.kind == self.Kind.DIGITAL

    @property
    def available_qty(self):
        if self.is_digital:
            return 1 if self.digital_file else 0
        return max(0, int(self.stock))

    @property
    def in_stock(self):
        return self.available_qty > 0

    @property
    def max_cart_qty(self):
        """Most units one buyer can put in the cart (a digital item is bought once per order)."""
        return min(self.available_qty, 1 if self.is_digital else 99)

    @property
    def digital_format(self):
        import os
        from .digital import DIGITAL_TYPES
        return DIGITAL_TYPES.get(os.path.splitext(self.digital_file_name or '')[1].lower(), '')

    @property
    def has_preview(self):
        """A free sample file is attached (movies use their trailer instead)."""
        return bool(self.is_digital and not self.is_movie and self.preview_file)

    @property
    def preview_kind(self):
        from .digital import preview_kind
        return preview_kind(self.preview_file.name) if self.preview_file else ''

    @property
    def preview_items(self):
        from .digital import parse_whats_inside
        return parse_whats_inside(self.whats_inside)

    @property
    def has_preview_panel(self):
        """Show the "Preview before you buy" panel: a sample and/or useful facts about the file."""
        return bool(self.is_digital and not self.is_movie and (
            self.preview_file or self.whats_inside.strip() or self.content_summary or self.content_language))

    @property
    def has_trailer(self):
        return bool(self.is_movie and self.trailer_file)

    @property
    def movie_duration(self):
        from .movies import format_duration
        return format_duration(self.movie_minutes)

    @property
    def movie_genre_list(self):
        from .movies import split_genres
        return split_genres(self.movie_genres)

    @property
    def movie_quality_label(self):
        from .movies import QUALITY_CHOICES
        return dict(QUALITY_CHOICES).get(self.movie_quality, '') if self.movie_quality else ''

    @property
    def discount_percent(self):
        if self.compare_at_price and self.price and self.compare_at_price > self.price:
            return int(((self.compare_at_price - self.price) / self.compare_at_price * 100).quantize(Decimal('1')))
        return 0

    def clean(self):
        super().clean()
        # A shop sells only under its own categories; platform products only under platform categories.
        if self.category_id and self.category.seller_store_id != self.seller_store_id:
            from django.core.exceptions import ValidationError
            raise ValidationError({'category': 'Choose a category that belongs to the same shop as this product.'})
    def save(self, *args, **kwargs):
        _normalise_jfif(self.image)
        optimize_image_field(self.image)
        super().save(*args, **kwargs)
    def __str__(self): return self.name


class SellerStore(models.Model):
    """Public online store owned by one SmartPOS pharmacy/business account."""
    pharmacy = models.OneToOneField('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='marketplace_store')
    slug = models.SlugField(max_length=180, unique=True)
    public_name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    logo = models.ImageField(upload_to='marketplace/stores/', blank=True)
    is_published = models.BooleanField(default=False)
    verified = models.BooleanField(default=False, help_text='Identity / business checked by the platform owner. Shows a Verified badge on listings.')
    instagram_url = models.URLField(max_length=200, blank=True, default='', help_text='Seller Instagram profile (cleaned automatically).')
    tiktok_url = models.URLField(max_length=200, blank=True, default='', help_text='Seller TikTok profile (cleaned automatically).')
    facebook_url = models.URLField(max_length=200, blank=True, default='', help_text='Seller Facebook page or profile (cleaned automatically).')
    whatsapp_url = models.URLField(max_length=200, blank=True, default='', help_text='Seller WhatsApp chat link, built from the number they enter.')
    youtube_url = models.URLField(max_length=200, blank=True, default='', help_text='Seller YouTube channel (cleaned automatically).')
    social_verified = models.JSONField(default=dict, blank=True, help_text='Platform -> the exact link SmartPOS verified. A badge shows only while the seller\'s link is unchanged.')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['public_name']

    def save(self, *args, **kwargs):
        optimize_image_field(self.logo, max_px=600)
        from .social import prune_verified
        self.social_verified = prune_verified(self)   # a changed link silently loses its badge
        super().save(*args, **kwargs)

    @property
    def verified_social_keys(self):
        return {l['key'] for l in self.social_links if l['verified']}

    @property
    def social_links(self):
        """Validated social profile links with icon data, for display on public pages."""
        from .social import build_social_links
        return build_social_links(self)

    def __str__(self):
        return self.public_name


class MerchantPaymentProfile(models.Model):
    """Per-business provider credentials; secrets are encrypted at rest."""
    class Provider(models.TextChoices):
        ABLINER = 'ABLINER', 'ABLiner'
        PESAPAL = 'PESAPAL', 'Pesapal'

    store = models.OneToOneField(SellerStore, on_delete=models.CASCADE, related_name='payment_profile')
    provider = models.CharField(max_length=12, choices=Provider.choices, blank=True)
    abliner_api_key = EncryptedTextField(blank=True)
    abliner_webhook_secret = EncryptedTextField(blank=True)
    abliner_base_url = models.URLField(default='https://abliner.net', blank=True)
    pesapal_environment = models.CharField(max_length=10, choices=[('SANDBOX','Sandbox'),('LIVE','Live')], default='SANDBOX')
    pesapal_consumer_key = EncryptedTextField(blank=True)
    pesapal_consumer_secret = EncryptedTextField(blank=True)
    verified = models.BooleanField(default=False, help_text='Set only after provider-side merchant approval and a verified integration test.')
    enabled = models.BooleanField(default=False)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.store.public_name} payment profile'

    @property
    def ready_for_direct_payment(self):
        if not (self.enabled and self.verified and self.provider):
            return False
        if self.provider == self.Provider.ABLINER:
            return bool(self.abliner_api_key and self.abliner_webhook_secret)
        if self.provider == self.Provider.PESAPAL:
            return bool(self.pesapal_consumer_key and self.pesapal_consumer_secret)
        return False


class Order(models.Model):
    class Status(models.TextChoices):
        NEW='new','New'; CONTACTED='contacted','Contacted'; CONFIRMED='confirmed','Confirmed'; OUT_FOR_DELIVERY='out_for_delivery','Out for delivery'; FULFILLED='fulfilled','Delivered'; CANCELLED='cancelled','Cancelled'
    seller_store = models.ForeignKey('SellerStore', null=True, blank=True, on_delete=models.SET_NULL, related_name='orders')
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    customer_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=25)
    location = models.CharField(max_length=250)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    class PaymentStatus(models.TextChoices):
        UNPAID='unpaid','Unpaid'; PENDING='pending','Payment pending'; PAID='paid','Paid'; FAILED='failed','Failed'; REFUNDED='refunded','Refunded'
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)
    payment_reference = models.CharField(max_length=120, blank=True)
    payment_method = models.CharField(max_length=60, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    seller_note = models.TextField(blank=True, help_text='Internal note for the shop; never shown to the customer.')
    cancel_reason = models.CharField(max_length=300, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    stock_restored = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['seller_store', 'status', 'created_at'], name='mp_order_store_status_idx')]
    def __str__(self): return str(self.reference)

    # Allowed status moves for the shop's delivery workflow.
    FLOW = {
        'new': ('contacted', 'confirmed', 'cancelled'),
        'contacted': ('confirmed', 'cancelled'),
        'confirmed': ('out_for_delivery', 'cancelled'),
        'out_for_delivery': ('fulfilled', 'cancelled'),
        'fulfilled': (),
        'cancelled': (),
    }

    def allowed_next(self):
        return [(v, dict(self.Status.choices)[v]) for v in self.FLOW.get(self.status, ())]

    @property
    def is_open(self):
        return self.status not in (self.Status.FULFILLED, self.Status.CANCELLED)

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    # SET_NULL: a product can be deleted from the admin; the order line keeps its own name/price snapshot.
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=180)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=13, decimal_places=2)
    is_digital = models.BooleanField(default=False, help_text='Snapshot at purchase time: delivered by download link, not by courier.')
    def __str__(self): return self.name


class DownloadGrant(models.Model):
    """Buyer's right to download one paid digital item.

    Created only after payment is verified. The unguessable `token` is the only thing in the link;
    every download re-checks the order is still paid and the grant is not revoked or used up.
    """
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    order_item = models.OneToOneField(OrderItem, on_delete=models.CASCADE, related_name='download')
    max_downloads = models.PositiveSmallIntegerField(default=10)
    download_count = models.PositiveSmallIntegerField(default=0)
    revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_downloaded_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.order_item.name} ({self.download_count}/{self.max_downloads})'

    @property
    def remaining(self):
        return max(0, self.max_downloads - self.download_count)

    @property
    def usable(self):
        order = self.order_item.order
        return (not self.revoked and self.remaining > 0
                and order.payment_status == Order.PaymentStatus.PAID
                and order.status != Order.Status.CANCELLED)

class OrderStatusEvent(models.Model):
    """Audit trail of every step an order goes through."""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='events')
    status = models.CharField(max_length=20, choices=Order.Status.choices)
    note = models.CharField(max_length=300, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='marketplace_order_events')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['created_at', 'pk']
    def __str__(self): return f'{self.order_id}: {self.status}'


class PaymentAttempt(models.Model):
    """Short-lived checkout snapshot; never appears as a customer order until paid."""
    class Status(models.TextChoices):
        PENDING='pending','Pending'; FAILED='failed','Failed'; PAID='paid','Paid'
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    customer_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=25)
    location = models.CharField(max_length=250)
    notes = models.TextField(blank=True)
    items_snapshot = models.JSONField(default=list)
    seller_store = models.ForeignKey('SellerStore', null=True, blank=True, on_delete=models.SET_NULL, related_name='payment_attempts')
    total = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payment_reference = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self): return f'{self.reference} ({self.status})'


class OnlineServiceSubscription(models.Model):
    """Paid monthly entitlement for seller online selling or seller order-alert SMS."""
    class Service(models.TextChoices):
        ONLINE_SELLING = 'ONLINE_SELLING', 'Online Selling'
        ORDER_SMS = 'ORDER_SMS', 'Seller Order Alert SMS'
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Awaiting payment'
        ACTIVE = 'ACTIVE', 'Active'
        EXPIRED = 'EXPIRED', 'Expired'
        CANCELLED = 'CANCELLED', 'Cancelled'
    store = models.ForeignKey(SellerStore, on_delete=models.CASCADE, related_name='service_subscriptions')
    service = models.CharField(max_length=24, choices=Service.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    monthly_amount = models.DecimalField(max_digits=10, decimal_places=2)
    sms_quota = models.PositiveIntegerField(default=0, help_text='Monthly seller order-alert SMS allowance; does not apply to customer payment receipts.')
    sms_used = models.PositiveIntegerField(default=0)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True, db_index=True)
    last_payment_reference = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['store','service','status'])]
    def __str__(self):
        return f'{self.store.public_name} — {self.get_service_display()} ({self.status})'
    @property
    def is_current(self):
        return self.status == self.Status.ACTIVE and bool(self.period_end and self.period_end > timezone.now())


class OnlineServiceRequest(models.Model):
    """Auditable customer request for Online Selling or seller SMS services."""
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending review'
        AWAITING_PAYMENT = 'AWAITING_PAYMENT', 'Awaiting payment'
        APPROVED = 'APPROVED', 'Approved by admin'
        ACTIVE = 'ACTIVE', 'Active'
        REJECTED = 'REJECTED', 'Rejected'
        CANCELLED = 'CANCELLED', 'Cancelled'

    store = models.ForeignKey(SellerStore, on_delete=models.CASCADE, related_name='service_requests')
    subscription = models.OneToOneField(OnlineServiceSubscription, on_delete=models.SET_NULL, null=True, blank=True, related_name='service_request')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='marketplace_service_requests')
    service = models.CharField(max_length=24, choices=OnlineServiceSubscription.Service.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    customer_note = models.CharField(max_length=500, blank=True)
    admin_note = models.CharField(max_length=500, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_marketplace_service_requests')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'created_at'])]

    def __str__(self):
        return f'{self.store.public_name} — {self.get_service_display()} ({self.get_status_display()})'


# ============================================================================
# Listings: property (rent / sale / land) and other "enquire, don't add to cart" adverts.
# Kept separate from Product so the shop/cart/checkout flow is untouched.
# ============================================================================
class Listing(models.Model):
    class Purpose(models.TextChoices):
        RENT = 'RENT', 'For rent'
        SALE = 'SALE', 'For sale'

    class PropertyType(models.TextChoices):
        HOUSE = 'HOUSE', 'House'
        APARTMENT = 'APARTMENT', 'Apartment / Flat'
        ROOM = 'ROOM', 'Room / Self-contained'
        HOSTEL = 'HOSTEL', 'Hostel / Guest room'
        FRAME = 'FRAME', 'Shop / Frame'
        OFFICE = 'OFFICE', 'Office space'
        WAREHOUSE = 'WAREHOUSE', 'Warehouse / Godown'
        LAND = 'LAND', 'Plot / Land'
        FARM = 'FARM', 'Farm / Shamba'
        OTHER = 'OTHER', 'Other'

    class PricePeriod(models.TextChoices):
        ONCE = 'ONCE', 'one-off price'
        MONTH = 'MONTH', 'per month'
        YEAR = 'YEAR', 'per year'
        DAY = 'DAY', 'per day'
        SQM = 'SQM', 'per sqm'

    class SizeUnit(models.TextChoices):
        SQM = 'SQM', 'sqm'
        ACRE = 'ACRE', 'acres'
        HECTARE = 'HECTARE', 'hectares'

    class TitleType(models.TextChoices):
        NONE = '', 'Not specified'
        TITLE_DEED = 'TITLE_DEED', 'Title deed (Hati miliki)'
        RESIDENTIAL = 'RESIDENTIAL', 'Residential licence (Leseni ya makazi)'
        CUSTOMARY = 'CUSTOMARY', 'Customary right (Hati ya kimila)'
        SALE_AGREEMENT = 'SALE_AGREEMENT', 'Sale agreement only'
        OTHER = 'OTHER', 'Other'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        PENDING = 'PENDING', 'Awaiting review'
        PUBLISHED = 'PUBLISHED', 'Published'
        RESERVED = 'RESERVED', 'Reserved'
        CLOSED = 'CLOSED', 'Sold / Rented'
        REJECTED = 'REJECTED', 'Needs changes'
        ARCHIVED = 'ARCHIVED', 'Archived'

    # What a visitor can see publicly.
    PUBLIC_STATUSES = ('PUBLISHED', 'RESERVED', 'CLOSED')

    AMENITY_CHOICES = [
        ('water', 'Water'), ('electricity', 'Electricity (TANESCO)'), ('tarmac', 'Tarmac road access'),
        ('fence', 'Fenced / Wall'), ('security', 'Security / Guard'), ('parking', 'Parking'),
        ('tiles', 'Tiles'), ('ceiling', 'Ceiling board'), ('master', 'Master bedroom'),
        ('kitchen', 'Fitted kitchen'), ('wifi', 'Internet ready'), ('ac', 'Air conditioning'),
        ('garden', 'Garden / Compound'), ('borehole', 'Borehole / Tank'), ('furnished', 'Furnished'),
        ('gated', 'Gated community'), ('drainage', 'Drainage / Sewer'), ('market', 'Near market / shops'),
        ('school', 'Near school'), ('transport', 'Near public transport'),
    ]

    store = models.ForeignKey(SellerStore, on_delete=models.CASCADE, related_name='listings')
    title = models.CharField(max_length=160)
    slug = models.SlugField(max_length=200, unique=True)
    purpose = models.CharField(max_length=4, choices=Purpose.choices, default=Purpose.RENT, db_index=True)
    property_type = models.CharField(max_length=12, choices=PropertyType.choices, default=PropertyType.HOUSE, db_index=True)
    description = models.TextField(blank=True)

    price = models.DecimalField(max_digits=14, decimal_places=2)
    price_period = models.CharField(max_length=6, choices=PricePeriod.choices, default=PricePeriod.MONTH)
    negotiable = models.BooleanField(default=False)
    installments = models.BooleanField(default=False, help_text='Payment in instalments is possible.')
    payment_terms = models.CharField(max_length=200, blank=True, help_text='e.g. "3 months rent in advance" or "30% deposit, balance in 6 months".')

    region = models.CharField(max_length=80, db_index=True)
    district = models.CharField(max_length=80, blank=True)
    ward = models.CharField(max_length=80, blank=True)
    street = models.CharField(max_length=120, blank=True)
    landmark = models.CharField(max_length=160, blank=True, help_text='A nearby landmark that helps people find it.')
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)

    size_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    size_unit = models.CharField(max_length=8, choices=SizeUnit.choices, default=SizeUnit.SQM)
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    bathrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    title_type = models.CharField(max_length=14, choices=TitleType.choices, blank=True, default='')
    surveyed = models.BooleanField(default=False, help_text='The plot has been surveyed.')
    amenities = models.JSONField(default=list, blank=True)

    contact_phone = models.CharField(max_length=25, blank=True)
    whatsapp_phone = models.CharField(max_length=25, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    review_note = models.CharField(max_length=300, blank=True)
    featured_until = models.DateTimeField(null=True, blank=True)
    views_count = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-published_at', '-created_at']
        indexes = [
            models.Index(fields=['status', 'purpose', 'property_type'], name='mp_listing_browse_idx'),
            models.Index(fields=['store', 'status'], name='mp_listing_store_idx'),
        ]

    def __str__(self):
        return self.title

    @property
    def is_public(self):
        return self.status in self.PUBLIC_STATUSES

    @property
    def is_land(self):
        return self.property_type in (self.PropertyType.LAND, self.PropertyType.FARM)

    @property
    def is_featured(self):
        return bool(self.featured_until and self.featured_until > timezone.now())

    @property
    def is_new(self):
        """Published within the last 7 days (shows a 'New' badge on cards)."""
        when = self.published_at or self.created_at
        return bool(when and self.status == 'PUBLISHED' and when > timezone.now() - timedelta(days=7))

    @property
    def location_line(self):
        parts = [self.street, self.ward, self.district, self.region]
        return ', '.join(p for p in parts if p)

    @property
    def amenity_labels(self):
        labels = dict(self.AMENITY_CHOICES)
        return [labels[a] for a in (self.amenities or []) if a in labels]

    @property
    def price_suffix(self):
        return {'MONTH': '/ month', 'YEAR': '/ year', 'DAY': '/ day', 'SQM': '/ sqm'}.get(self.price_period, '')

    @property
    def map_url(self):
        from urllib.parse import quote_plus
        if self.latitude is not None and self.longitude is not None:
            return f'https://www.google.com/maps?q={self.latitude},{self.longitude}'
        return 'https://www.google.com/maps/search/?api=1&query=' + quote_plus(f'{self.location_line}, Tanzania')

    @property
    def cover(self):
        imgs = list(self.images.all())
        return imgs[0] if imgs else None

    @property
    def contact_for_whatsapp(self):
        return self.whatsapp_phone or self.contact_phone


class ListingImage(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='marketplace/listings/')
    caption = models.CharField(max_length=120, blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position', 'pk']

    def save(self, *args, **kwargs):
        optimize_image_field(self.image, max_px=1600, quality=80)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.listing_id} #{self.position}'


class Inquiry(models.Model):
    """A customer enquiry (lead) on a listing: the property equivalent of an order."""
    class Status(models.TextChoices):
        NEW = 'new', 'New'
        CONTACTED = 'contacted', 'Contacted'
        VIEWING = 'viewing', 'Viewing booked'
        CLOSED = 'closed', 'Closed'

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name='inquiries')
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=25)
    message = models.TextField(blank=True, max_length=1000)
    visit_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW, db_index=True)
    seller_note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'Inquiries'

    def __str__(self):
        return f'{self.name} → {self.listing_id}'


class ProductImage(models.Model):
    """Extra photos for a product (the main `Product.image` stays the cover)."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='gallery')
    image = models.ImageField(upload_to='marketplace/products/')
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position', 'pk']

    def save(self, *args, **kwargs):
        _normalise_jfif(self.image)
        optimize_image_field(self.image)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.product_id} #{self.position}'


class ListingReport(models.Model):
    """A visitor's report of a suspicious advert (scam protection)."""
    class Reason(models.TextChoices):
        SCAM = 'SCAM', 'Looks like a scam / asks for money first'
        FAKE = 'FAKE', 'Fake or misleading photos / details'
        SOLD = 'SOLD', 'Already sold or rented'
        PRICE = 'PRICE', 'Wrong price'
        OTHER = 'OTHER', 'Other'

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name='reports')
    reason = models.CharField(max_length=6, choices=Reason.choices)
    details = models.CharField(max_length=500, blank=True)
    reporter_phone = models.CharField(max_length=25, blank=True)
    handled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['handled', '-created_at']

    def __str__(self):
        return f'{self.get_reason_display()} → {self.listing_id}'



class ProductReview(models.Model):
    """A star rating (and optional comment) from a verified buyer.

    One review per (product, order): the buyer proves the purchase with the Ticket ID and the
    checkout phone, exactly like Track Order. Submitting again for the same order edits the review.
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='reviews')
    rating = models.PositiveSmallIntegerField(help_text='1 (poor) to 5 (excellent)')
    customer_name = models.CharField(max_length=150)
    comment = models.TextField(blank=True, max_length=1000)
    is_visible = models.BooleanField(default=True, help_text='Untick to hide an inappropriate review; ratings are recalculated automatically.')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['product', 'order'], name='mp_one_review_per_order_product'),
                       models.CheckConstraint(condition=models.Q(rating__gte=1, rating__lte=5), name='mp_review_rating_1_to_5')]
        indexes = [models.Index(fields=['product', 'is_visible', '-created_at'], name='mp_review_product_idx')]

    def __str__(self):
        return f'{self.product} - {self.rating}/5'

    @property
    def display_name(self):
        """Privacy: show first name and the initial of the rest only (e.g. 'Asha M.')."""
        parts = (self.customer_name or '').split()
        if not parts:
            return 'Customer'
        return parts[0] if len(parts) == 1 else f'{parts[0]} {parts[-1][:1].upper()}.'


def refresh_product_rating(product_id):
    from django.db.models import Avg, Count
    agg = ProductReview.objects.filter(product_id=product_id, is_visible=True).aggregate(a=Avg('rating'), c=Count('id'))
    avg = Decimal(str(agg['a'] or 0)).quantize(Decimal('0.01'))
    Product.objects.filter(pk=product_id).update(rating_avg=avg, rating_count=agg['c'] or 0)


from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver


@receiver([post_save, post_delete], sender=ProductReview)
def _sync_product_rating(sender, instance, **kwargs):
    refresh_product_rating(instance.product_id)


class PendingUpload(models.Model):
    """A large file (movie) being sent straight from the browser to the bucket in parts.

    Lifecycle: UPLOADING -> COMPLETE (verified in the bucket) -> consumed when the product form is saved.
    Abandoned rows are cleaned by `manage.py cleanup_pending_uploads`.
    """
    class Status(models.TextChoices):
        UPLOADING = 'UPLOADING', 'Uploading'
        COMPLETE = 'COMPLETE', 'Complete'
        FAILED = 'FAILED', 'Failed'
        ABORTED = 'ABORTED', 'Cancelled'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seller_store = models.ForeignKey('SellerStore', on_delete=models.CASCADE, related_name='pending_uploads')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    filename = models.CharField(max_length=180)
    size = models.PositiveBigIntegerField()
    content_type = models.CharField(max_length=100, blank=True)
    storage_name = models.CharField(max_length=255)      # name inside the private storage (without the "private/" prefix)
    s3_upload_id = models.CharField(max_length=512, blank=True)
    part_size = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UPLOADING, db_index=True)
    error = models.CharField(max_length=255, blank=True)
    consumed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['seller_store', 'user', 'filename', 'size'], name='mk_pu_lookup_idx')]

    def __str__(self):
        return f'{self.filename} ({self.status})'
