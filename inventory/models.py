from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
import os
import secrets
import uuid
from django.db import models
from django.db.models import F, Q
from decimal import Decimal
from django.utils import timezone


# Stock is always drawn oldest-expiry first (FEFO). Batches WITHOUT an expiry date
# (products that never expire) are used after every dated batch, oldest received first.
FEFO_ORDER = (F('expiry_date').asc(nulls_last=True), 'received_at', 'pk')

def product_image_path(instance, filename):
    """Branch-scoped, unguessable file name: product_images/<branch id>/<random>.<ext>."""
    ext = os.path.splitext(filename or '')[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp'):
        ext = '.jpg'
    return f"product_images/{instance.pharmacy_id or 0}/{uuid.uuid4().hex}{ext}"


UNIT_MAX_LENGTH = 30   # standard units are short codes (TABLET); custom units are stored as typed (e.g. "Carton of 24")


def unit_label(code):
    """Readable name of a unit: standard codes map to their label, custom units are shown as typed."""
    code = (code or '').strip()
    return dict(Product.Unit.choices).get(code, code)


class Category(models.Model):
    """A category owned by one pharmacy branch. Categories are tenant-scoped."""
    pharmacy = models.ForeignKey(
        'pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='categories'
    )
    name = models.CharField(max_length=100)

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['pharmacy', 'name'], name='uniq_category_per_pharmacy'),
        ]


    def __str__(self):
        return self.name


class UnitCatalog(models.Model):
    """Reusable custom units owned by a single pharmacy branch."""
    pharmacy = models.ForeignKey(
        'pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='custom_units'
    )
    name = models.CharField(max_length=UNIT_MAX_LENGTH)
    normalized_name = models.CharField(max_length=UNIT_MAX_LENGTH, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['pharmacy', 'normalized_name'], name='uniq_unit_per_pharmacy'),
        ]

    def save(self, *args, **kwargs):
        self.name = ' '.join((self.name or '').split())
        self.normalized_name = self.name.casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Product(models.Model):
    """
    A catalog entry — WHAT the product is (name, category, unit, reorder level).
    It does NOT hold price, quantity, or expiry directly; those live on
    ProductBatch, because a single product can have several batches in stock
    at once (bought at different times, different prices, different expiry
    dates), and mixing them together would make stock and profit analysis
    unreliable. Scoped to a single Pharmacy branch so catalogs never mix.
    """
    class Unit(models.TextChoices):
        TABLET = 'TABLET', 'Tablet'
        CAPSULE = 'CAPSULE', 'Capsule'
        BOTTLE = 'BOTTLE', 'Bottle'
        BOX = 'BOX', 'Box'
        PACK = 'PACK', 'Pack'
        STRIP = 'STRIP', 'Strip'
        VIAL = 'VIAL', 'Vial'
        PIECE = 'PIECE', 'Piece'
        TUBE = 'TUBE', 'Tube'
        CARTON = 'CARTON', 'Carton'
        SACHET = 'SACHET', 'Sachet'
        PAIR = 'PAIR', 'Pair'
        JAR = 'JAR', 'Jar'
        CAN = 'CAN', 'Can'
        ROLL = 'ROLL', 'Roll'
        PACKET = 'PACKET', 'Packet'
        BAG = 'BAG', 'Bag'
        DOZEN = 'DOZEN', 'Dozen'
        CRATE = 'CRATE', 'Crate'
        SET = 'SET', 'Set'
        BUNDLE = 'BUNDLE', 'Bundle'

    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=200)
    sku = models.CharField(
        max_length=40, blank=True, null=True,
        help_text="Stable stock-keeping unit. Leave blank to generate automatically."
    )
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    # Standard units are listed in Unit; a branch may also add its own (stored as typed), so there is no `choices` here.
    unit = models.CharField(max_length=UNIT_MAX_LENGTH, default=Unit.TABLET, help_text='Smallest/base unit used for stock and sales.')
    requires_expiry = models.BooleanField(
        default=True,
        help_text=(
            "This product has an expiry date (e.g. medicines). When on, every new batch "
            "must have an expiry date. Turn off for items that do not expire, "
            "such as cosmetics and accessories."
        ),
    )
    reorder_level = models.PositiveIntegerField(
        default=10,
        help_text="Owner gets a low-stock alert when total quantity across all batches falls to/below this number."
    )
    # Manager-controlled POS discount policy. The seller can only apply a
    # discount within this range; the server validates it again at checkout.
    discount_enabled = models.BooleanField(
        default=False,
        help_text="Allow shop employees to apply a discount to this product in the POS."
    )
    class DiscountType(models.TextChoices):
        PERCENT = 'PERCENT', 'Percentage (%)'
        AMOUNT = 'AMOUNT', 'Fixed amount (TZS per unit)'

    discount_type = models.CharField(
        max_length=8, choices=DiscountType.choices, default=DiscountType.PERCENT,
        help_text="Percentage of the price, or a fixed TZS amount taken off each unit."
    )
    min_discount_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        help_text="Minimum TZS discount per unit (used when the discount type is Fixed amount)."
    )
    max_discount_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        help_text="Maximum TZS discount per unit (used when the discount type is Fixed amount)."
    )
    min_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.00'),
        help_text="Minimum discount percentage a seller may apply."
    )
    max_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.00'),
        help_text="Maximum discount percentage a seller may apply."
    )
    barcode = models.CharField(
        max_length=32, blank=True, null=True,
        help_text="Optional product barcode. Leave blank to generate a unique Code-128 barcode automatically."
    )

    # Optional photo. Managers add it when they want staff to recognise the item faster
    # (POS search, stock list). Everything works the same without one.
    image = models.ImageField(
        upload_to=product_image_path, blank=True, null=True,
        help_text="Optional product photo (JPG, PNG or WebP, up to 5 MB).",
    )

    is_active = models.BooleanField(default=True)
    is_demo = models.BooleanField(
        default=False, db_index=True,
        help_text="Sample product loaded for a new Free Plan account. The owner can remove all sample products in one click.",
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='products_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [models.Index(fields=['name'])]
        unique_together = [('pharmacy', 'name'), ('pharmacy', 'barcode')]

    def get_unit_display(self):
        return unit_label(self.unit)

    @property
    def image_url(self):
        """URL of the product photo, or '' when none / storage unavailable. Never raises."""
        try:
            return self.image.url if self.image else ''
        except Exception:
            return ''

    def _generate_barcode(self):
        # Human-readable, scan-friendly Code-128 value. Prefix PM identifies
        # values created by this system while keeping them short enough for labels.
        while True:
            value = 'PM' + ''.join(str(secrets.randbelow(10)) for _ in range(12))
            if not Product.objects.filter(pharmacy=self.pharmacy, barcode=value).exists():
                return value

    def _generate_sku(self):
        while True:
            value = f"SKU-{secrets.randbelow(10**10):010d}"
            if not Product.objects.filter(pharmacy=self.pharmacy, sku=value).exists():
                return value

    def save(self, *args, **kwargs):
        # Enforce tenant ownership at the model layer too, not only in forms.
        # This protects imports, scripts, admin actions and future API endpoints.
        if self.category_id and self.pharmacy_id:
            category_pharmacy_id = Category.objects.filter(pk=self.category_id).values_list('pharmacy_id', flat=True).first()
            if category_pharmacy_id != self.pharmacy_id:
                raise ValidationError({'category': 'This category belongs to another shop. Choose a category from the active shop.'})
        if self.pharmacy_id and not self.sku:
            self.sku = self._generate_sku()
        if self.pharmacy_id and not self.barcode:
            self.barcode = self._generate_barcode()
        super().save(*args, **kwargs)

    @property
    def effective_barcode(self):
        """Return the product barcode, with a stable fallback for legacy rows.

        Older databases may contain products created before automatic barcode
        generation was introduced. The barcode UI/API should still have a
        stable Code-128-safe value for those rows without changing the database
        just by rendering a page.
        """
        if self.barcode:
            return self.barcode
        if self.pk and self.pharmacy_id:
            return f"PM{self.pharmacy_id:04d}{self.pk:08d}"
        # Unsaved products are not normally printable/searchable, but keeping
        # a deterministic-safe value avoids AttributeError in previews.
        return "PMUNSAVED"

    def __str__(self):
        return f"{self.name} ({self.pharmacy.name})"

    # ---- Batch-aware aggregates -------------------------------------------------
    def active_batches(self):
        """Non-depleted batches, earliest expiry first (FEFO — First Expired, First Out).
        Batches with no expiry date come last, oldest first."""
        return self.batches.filter(quantity__gt=0).order_by(*FEFO_ORDER)

    def sellable_batches(self):
        """Active batches that are not yet expired — the only ones the POS may sell from.
        A batch with no expiry date never expires, so it is always sellable."""
        today = timezone.localdate()
        return self.active_batches().filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=today))

    @property
    def total_quantity(self):
        """Total units across ALL active batches (including expired ones still sitting in stock)."""
        return sum(b.quantity for b in self.active_batches())

    @property
    def sellable_quantity(self):
        """Total units available to actually sell (excludes expired batches)."""
        return sum(b.quantity for b in self.sellable_batches())

    @property
    def next_batch(self):
        """The batch that will be sold next under FEFO — used for 'current' price display."""
        return self.sellable_batches().first()

    @property
    def current_selling_price(self):
        batch = self.next_batch
        if batch:
            return batch.selling_price
        latest = self.batches.order_by('-received_at').first()
        return latest.selling_price if latest else None

    @property
    def current_purchase_price(self):
        return self.weighted_average_cost

    @property
    def weighted_average_cost(self):
        """Current WAC per base unit across sellable stock."""
        batches = list(self.sellable_batches())
        total_qty = sum(b.quantity for b in batches)
        if not total_qty:
            return None
        total_cost = sum((b.purchase_price * b.quantity for b in batches), start=Decimal('0'))
        return (total_cost / Decimal(total_qty)).quantize(Decimal('0.01'))

    @property
    def nearest_expiry_date(self):
        batch = self.active_batches().first()
        return batch.expiry_date if batch else None

    @property
    def is_low_stock(self):
        return self.total_quantity <= self.reorder_level

    @property
    def is_expiring_soon(self):
        batch = self.active_batches().first()
        return batch.is_expiring_soon if batch else False

    @property
    def is_expired(self):
        """True only when there is NO sellable stock left — everything remaining has expired."""
        return self.total_quantity > 0 and self.sellable_quantity == 0

    @property
    def days_to_expiry(self):
        batch = self.active_batches().first()
        return batch.days_to_expiry if batch else None


class StockImport(models.Model):
    """One auditable stock-import/receipt event."""
    class ImportMode(models.TextChoices):
        OPENING = 'OPENING', 'Opening Stock'
        RESTOCK = 'RESTOCK', 'Restock / Purchase'

    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='stock_imports')
    mode = models.CharField(max_length=10, choices=ImportMode.choices)
    reference = models.CharField(max_length=40)
    file_name = models.CharField(max_length=255, blank=True)
    file_sha256 = models.CharField(max_length=64, blank=True, default='', help_text='Fingerprint used to prevent accidental duplicate uploads.')
    row_count = models.PositiveIntegerField(default=0)
    new_products = models.PositiveIntegerField(default=0)
    new_batches = models.PositiveIntegerField(default=0)
    topped_up = models.PositiveIntegerField(default=0)
    reused_batches = models.PositiveIntegerField(default=0, help_text='Opening-stock batch rows safely reused without adding stock again.')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='stock_imports_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['pharmacy', 'reference'], name='inventory_stockimport_pharmacy_reference_uniq')]

    def __str__(self):
        return f"{self.reference} — {self.get_mode_display()}"


class ProductBatch(models.Model):
    """
    One specific delivery/lot of a product — its own batch number, cost,
    price, and expiry date. Selling always draws down the earliest-expiry
    batch first (FEFO). Opening imports always create a distinct batch row.
    Restocking may top up an existing live batch only when the complete lot
    identity matches; otherwise a distinct batch is created.
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='batches')
    stock_import = models.ForeignKey('StockImport', on_delete=models.SET_NULL, null=True, blank=True, related_name='batches')
    batch_number = models.CharField(max_length=100, blank=True)
    purchase_unit = models.CharField(max_length=UNIT_MAX_LENGTH, blank=True, default='', help_text="Unit used for this purchase, e.g. Box, Carton, Bottle. Leave blank when purchased as the base unit.")
    purchase_quantity = models.PositiveIntegerField(default=1, help_text="Number of purchase units received, e.g. 5 boxes.")
    units_per_purchase_unit = models.PositiveIntegerField(default=1, help_text="Base units contained in one purchase unit at the time of receipt.")
    purchase_unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Purchase price for one purchase unit, e.g. TSh 4,200 per box.")
    initial_quantity = models.PositiveIntegerField(help_text="Base units originally received in this batch.")
    quantity = models.PositiveIntegerField(help_text="Base units remaining from this batch right now.")
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, help_text="Calculated purchase cost per base unit, used for profit/WAC.")
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)
    # Manager-controlled limit for THIS lot only. Null = follow the product's discount policy.
    # The POS and the server both apply it per batch, on top of the never-below-cost guard.
    discount_cap_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0'))],
        help_text="Optional. The most that can be taken off ONE unit of this batch (TZS), including any VIP discount. "
                  "Leave blank to follow the product's discount policy. Enter 0 to allow no discount on this batch.",
    )
    expiry_date = models.DateField(
        null=True, blank=True,
        help_text="Optional. Leave blank for products that do not expire.",
    )

    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='batches_received')
    received_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['expiry_date']  # code that decides which batch to sell uses FEFO_ORDER explicitly
        indexes = [models.Index(fields=['expiry_date'])]

    def _generate_batch_number(self):
        """Generate a unique, human-readable batch/lot reference for this product.

        Batch numbers are scoped to a product because the same supplier lot number
        may legitimately appear on different products. Generated values are only
        used when the user/import leaves the batch number blank.
        """
        prefix = f"BATCH-{timezone.localdate():%Y%m%d}-"
        while True:
            value = prefix + secrets.token_hex(4).upper()
            if not ProductBatch.objects.filter(product=self.product, batch_number=value).exists():
                return value

    def save(self, *args, **kwargs):
        # A blank batch number must never reach the database for a real batch.
        # This covers manual registration, restock, bulk imports and edits.
        if self.product_id and not (self.batch_number or '').strip():
            self.batch_number = self._generate_batch_number()
        elif self.batch_number:
            self.batch_number = str(self.batch_number).strip()
        super().save(*args, **kwargs)

    def __str__(self):
        label = self.batch_number or f"Batch #{self.pk}"
        return f"{self.product.name} — {label} ({self.quantity} left)"

    @property
    def is_depleted(self):
        return self.quantity <= 0

    @property
    def has_expiry(self):
        return self.expiry_date is not None

    @property
    def is_expired(self):
        return self.expiry_date is not None and self.expiry_date < timezone.localdate()

    @property
    def days_to_expiry(self):
        """Days until expiry, or None when this batch has no expiry date."""
        if self.expiry_date is None:
            return None
        return (self.expiry_date - timezone.localdate()).days

    @property
    def is_expiring_soon(self):
        from django.conf import settings as dj_settings
        days = self.days_to_expiry
        return days is not None and 0 <= days <= dj_settings.EXPIRY_WARNING_DAYS

    def get_purchase_unit_display(self):
        return unit_label(self.purchase_unit)

    def prepare_packaging_cost(self):
        """Convert purchase-unit pricing into base-unit stock/cost."""
        units = int(self.units_per_purchase_unit or 1)
        qty = int(self.purchase_quantity or 1)
        if units < 1 or qty < 1:
            raise ValueError('Purchase quantity and units per purchase unit must be at least 1.')
        if self.purchase_unit_price is not None:
            self.purchase_price = (Decimal(self.purchase_unit_price) / Decimal(units)).quantize(Decimal('0.01'))
        self.initial_quantity = qty * units
        if self.quantity is None or self.quantity == 0 or self.quantity == self.initial_quantity:
            self.quantity = self.initial_quantity
        return self

    @property
    def total_purchase_cost(self):
        if self.purchase_unit_price is not None:
            return Decimal(self.purchase_unit_price) * Decimal(self.purchase_quantity)
        return Decimal(self.purchase_price) * Decimal(self.initial_quantity)

    @property
    def profit_margin_per_unit(self):
        return self.selling_price - self.purchase_price


class StockMovement(models.Model):
    """Audit trail: every time stock changes (new batch, sale, adjustment)."""
    class MovementType(models.TextChoices):
        NEW_BATCH = 'NEW_BATCH', 'New Batch Received'
        SALE = 'SALE', 'Sale'
        ADJUSTMENT = 'ADJUSTMENT', 'Manual Adjustment'
        BULK_UPLOAD = 'BULK_UPLOAD', 'Bulk Upload'
        SALE_RETURN = 'SALE_RETURN', 'Sale Return (Restocked)'
        WRITE_OFF = 'WRITE_OFF', 'Returned Item Written Off'
        TRANSFER_OUT = 'TRANSFER_OUT', 'Branch Transfer Out'
        TRANSFER_IN = 'TRANSFER_IN', 'Branch Transfer In'

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='movements')
    batch = models.ForeignKey(ProductBatch, on_delete=models.SET_NULL, null=True, blank=True, related_name='movements')
    movement_type = models.CharField(max_length=15, choices=MovementType.choices)
    quantity_change = models.IntegerField(help_text="Positive for additions, negative for removals.")
    note = models.CharField(max_length=255, blank=True)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.product.name}: {self.quantity_change:+d} ({self.movement_type})"


class BranchStockTransfer(models.Model):
    """Auditable, atomic transfer of a specific stock batch between branches."""
    class Status(models.TextChoices):
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    reference = models.CharField(max_length=32, unique=True, editable=False)
    source_pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.PROTECT, related_name='stock_transfers_out')
    destination_pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.PROTECT, related_name='stock_transfers_in')
    # Legacy single-batch columns (V54.21-V54.24). Bulk transfers (V54.25+) leave them empty
    # and record every line in BranchStockTransferItem instead.
    source_batch = models.ForeignKey(ProductBatch, on_delete=models.PROTECT, null=True, blank=True, related_name='transfers_out')
    destination_batch = models.ForeignKey(ProductBatch, on_delete=models.PROTECT, null=True, blank=True, related_name='transfers_in')
    quantity = models.PositiveIntegerField(help_text='Total units moved across all items.')
    item_count = models.PositiveIntegerField(default=0, help_text='Number of batches (lines) in this transfer.')
    idempotency_key = models.CharField(max_length=64, null=True, blank=True, unique=True, editable=False, help_text='Client token that makes retries/double submits safe.')
    source_quantity_after = models.PositiveIntegerField(default=0, help_text='Source batch quantity immediately after this transfer.')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.COMPLETED)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='branch_stock_transfers')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['source_pharmacy', '-created_at']), models.Index(fields=['destination_pharmacy', '-created_at'])]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = 'ST-' + timezone.now().strftime('%Y%m%d') + '-' + secrets.token_hex(3).upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.reference}: {self.source_pharmacy} → {self.destination_pharmacy}'


class BranchStockTransferItem(models.Model):
    """One batch line inside a (bulk) branch stock transfer, with a frozen snapshot of its values."""
    transfer = models.ForeignKey(BranchStockTransfer, on_delete=models.CASCADE, related_name='items')
    source_batch = models.ForeignKey(ProductBatch, on_delete=models.PROTECT, related_name='transfer_items_out')
    destination_batch = models.ForeignKey(ProductBatch, on_delete=models.PROTECT, related_name='transfer_items_in')
    product_name = models.CharField(max_length=200)
    unit = models.CharField(max_length=UNIT_MAX_LENGTH, blank=True)
    source_batch_number = models.CharField(max_length=100, blank=True)
    quantity = models.PositiveIntegerField()
    source_quantity_after = models.PositiveIntegerField(default=0)
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)
    expiry_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['product_name', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['transfer', 'source_batch'], name='uniq_transfer_item_source_batch'),
            models.CheckConstraint(condition=Q(quantity__gte=1), name='transfer_item_quantity_gte_1'),
        ]

    @property
    def unit_display(self):
        return unit_label(self.unit) if self.unit else 'units'

    @property
    def line_cost(self):
        return self.purchase_price * self.quantity

    def __str__(self):
        return f'{self.transfer.reference}: {self.product_name} x{self.quantity}'


class BarcodeCatalog(models.Model):
    """Shared memory of barcode look-ups (Open Food Facts / UPCitemdb).

    One row per barcode, shared by all branches (a barcode names the same pack everywhere).
    It stores ONLY public identity data (name, brand, size, picture link) - never prices or stock.
    `found=False` rows are a short "not found" memory so the free APIs are not asked again and again.
    """
    SOURCE_CHOICES = [
        ('openfoodfacts', 'Open Food Facts'),
        ('openbeautyfacts', 'Open Beauty Facts'),
        ('openproductsfacts', 'Open Products Facts'),
        ('upcitemdb', 'UPCitemdb'),
        ('smartpos', 'SmartPOS catalog'),
    ]

    barcode = models.CharField(max_length=32, unique=True)
    found = models.BooleanField(default=True)
    name = models.CharField(max_length=200, blank=True)
    brand = models.CharField(max_length=100, blank=True)
    quantity = models.CharField(max_length=60, blank=True)
    image_url = models.URLField(max_length=500, blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, blank=True)
    looked_up_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-looked_up_at']

    def __str__(self):
        return f"{self.barcode} - {self.name or 'not found'}"


from django.db.models.signals import post_delete  # noqa: E402
from django.dispatch import receiver  # noqa: E402


@receiver(post_delete, sender=Product, dispatch_uid='inventory.delete_product_image')
def _delete_product_image_file(sender, instance, **kwargs):
    """Do not leave orphaned photos in storage when a product is permanently deleted."""
    name = getattr(instance.image, 'name', '') if instance.image else ''
    if name:
        try:
            instance.image.storage.delete(name)
        except Exception:  # pragma: no cover - storage hiccups must not break deletion
            pass
