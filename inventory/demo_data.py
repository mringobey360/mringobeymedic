"""Sample (demo) products for new Free Plan accounts.

A brand-new account should never open on an empty screen. At registration the customer's own
business type decides which 10 sample products are loaded (each with stock, prices and, for
medicines/food, expiry dates), so they can make a test sale the second they sign in.

Every sample product carries ``Product.is_demo = True`` and the suffix " (Sample)", so:
  * the owner can remove all of them in one click (``remove_demo_products``), and
  * sales made only with sample products never count toward the Free Plan sales allowance.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import Category, Product, ProductBatch, StockMovement

SAMPLE_SUFFIX = ' (Sample)'

# (name, category, unit, buying price, selling price, quantity, days until expiry or None)
_P = {
    'PHARMACY': [
        ('Paracetamol 500mg', 'Pain & Fever', 'TABLET', 40, 100, 500, 540),
        ('Amoxicillin 500mg', 'Antibiotics', 'CAPSULE', 150, 300, 300, 420),
        ('ORS Sachet', 'Rehydration', 'SACHET', 400, 800, 120, 600),
        ('Ibuprofen 400mg', 'Pain & Fever', 'TABLET', 60, 150, 400, 480),
        ('Cough Syrup 100ml', 'Cold & Cough', 'BOTTLE', 1800, 3000, 40, 365),
        ('Vitamin C 1000mg', 'Vitamins', 'TABLET', 120, 250, 200, 700),
        ('Antacid Tablets', 'Stomach', 'STRIP', 500, 1000, 90, 450),
        ('Zinc Sulphate 20mg', 'Vitamins', 'TABLET', 100, 200, 250, 500),
        ('Hand Sanitizer 100ml', 'Hygiene', 'BOTTLE', 1500, 2500, 36, 720),
        ('Adhesive Plasters (Box)', 'First Aid', 'BOX', 1200, 2000, 30, None),
    ],
    'RETAIL': [
        ('Sugar 1kg', 'Groceries', 'PACKET', 2600, 3200, 60, None),
        ('Cooking Oil 1L', 'Groceries', 'BOTTLE', 5200, 6000, 40, None),
        ('Rice 1kg', 'Groceries', 'PACKET', 2400, 3000, 80, None),
        ('Wheat Flour 1kg', 'Groceries', 'PACKET', 2000, 2500, 70, None),
        ('Bar Soap', 'Household', 'PIECE', 1300, 1800, 100, None),
        ('Mineral Water 500ml', 'Drinks', 'BOTTLE', 500, 800, 120, None),
        ('Soda 350ml', 'Drinks', 'BOTTLE', 700, 1000, 144, None),
        ('Salt 1kg', 'Groceries', 'PACKET', 700, 1000, 60, None),
        ('Exercise Book', 'Stationery', 'PIECE', 600, 1000, 90, None),
        ('Matches (Dozen)', 'Household', 'DOZEN', 1500, 2200, 30, None),
    ],
    'WHOLESALE': [
        ('Sugar 50kg Bag', 'Bulk Groceries', 'BAG', 125000, 138000, 30, None),
        ('Rice 25kg Bag', 'Bulk Groceries', 'BAG', 58000, 65000, 40, None),
        ('Cooking Oil 20L Jerrycan', 'Bulk Groceries', 'PIECE', 78000, 86000, 25, None),
        ('Wheat Flour 24kg Bag', 'Bulk Groceries', 'BAG', 46000, 52000, 35, None),
        ('Soda Crate (24)', 'Drinks', 'CRATE', 16000, 18500, 50, None),
        ('Mineral Water Carton (12)', 'Drinks', 'CARTON', 5200, 6500, 80, None),
        ('Bar Soap Carton (24)', 'Household', 'CARTON', 28000, 33000, 40, None),
        ('Biscuits Carton (48)', 'Snacks', 'CARTON', 36000, 42000, 30, 300),
        ('Tomato Paste Carton (24)', 'Bulk Groceries', 'CARTON', 30000, 36000, 20, 500),
        ('Salt 25kg Bag', 'Bulk Groceries', 'BAG', 12000, 15000, 45, None),
    ],
    'SUPERMARKET': [
        ('Fresh Milk 1L', 'Dairy', 'PIECE', 1800, 2400, 60, 14),
        ('Bread (Loaf)', 'Bakery', 'PIECE', 1600, 2200, 40, 4),
        ('Eggs (Tray of 30)', 'Dairy', 'PIECE', 10500, 12500, 20, 21),
        ('Cooking Oil 1L', 'Groceries', 'BOTTLE', 5200, 6000, 50, None),
        ('Sugar 1kg', 'Groceries', 'PACKET', 2600, 3200, 70, None),
        ('Tomato Sauce 400g', 'Groceries', 'BOTTLE', 2200, 3000, 40, 400),
        ('Toothpaste 100ml', 'Personal Care', 'TUBE', 2000, 2800, 50, 700),
        ('Shampoo 400ml', 'Personal Care', 'BOTTLE', 5500, 7000, 24, 700),
        ('Laundry Powder 1kg', 'Household', 'PACKET', 4200, 5500, 36, None),
        ('Yoghurt 500ml', 'Dairy', 'PIECE', 1900, 2600, 30, 21),
    ],
    'RESTAURANT': [
        ('Chips Plate', 'Meals', 'Plate', 2500, 5000, 50, None),
        ('Chicken & Rice', 'Meals', 'Plate', 4500, 8000, 40, None),
        ('Beef Stew & Ugali', 'Meals', 'Plate', 4000, 7000, 40, None),
        ('Pilau', 'Meals', 'Plate', 3000, 6000, 45, None),
        ('Fresh Juice Glass', 'Drinks', 'Glass', 800, 2000, 60, None),
        ('Soda 350ml', 'Drinks', 'BOTTLE', 700, 1500, 96, None),
        ('Tea with Milk', 'Drinks', 'Cup', 300, 1000, 100, None),
        ('Samosa', 'Snacks', 'PIECE', 250, 600, 120, None),
        ('Mandazi (Pack of 4)', 'Snacks', 'PACK', 500, 1000, 80, None),
        ('Mineral Water 500ml', 'Drinks', 'BOTTLE', 500, 1000, 120, None),
    ],
    'HARDWARE': [
        ('Cement 50kg Bag', 'Building Materials', 'BAG', 16500, 18500, 100, None),
        ('Iron Sheet Gauge 28', 'Roofing', 'PIECE', 24000, 28000, 60, None),
        ('Nails 3 inch (1kg)', 'Fasteners', 'PACKET', 3200, 4500, 80, None),
        ('Paint 4L White', 'Paint', 'CAN', 28000, 35000, 25, None),
        ('Claw Hammer', 'Tools', 'PIECE', 7000, 10000, 20, None),
        ('PVC Pipe 1 inch', 'Plumbing', 'PIECE', 6500, 9000, 40, None),
        ('Padlock 50mm', 'Security', 'PIECE', 4000, 6500, 35, None),
        ('Steel Bar 12mm', 'Building Materials', 'PIECE', 21000, 25000, 70, None),
        ('Electric Cable 2.5mm (Roll)', 'Electrical', 'ROLL', 95000, 115000, 10, None),
        ('Paint Brush 4 inch', 'Paint', 'PIECE', 2000, 3500, 50, None),
    ],
    'ELECTRONICS': [
        ('Phone Charger 20W', 'Chargers & Cables', 'PIECE', 6000, 12000, 40, None),
        ('USB-C Cable 1m', 'Chargers & Cables', 'PIECE', 2000, 5000, 80, None),
        ('Earphones Wired', 'Audio', 'PIECE', 3000, 7000, 50, None),
        ('Bluetooth Speaker', 'Audio', 'PIECE', 25000, 40000, 15, None),
        ('Power Bank 10000mAh', 'Power', 'PIECE', 22000, 35000, 20, None),
        ('Phone Case', 'Accessories', 'PIECE', 2000, 6000, 70, None),
        ('Screen Protector', 'Accessories', 'PIECE', 1000, 4000, 100, None),
        ('Flash Disk 32GB', 'Storage', 'PIECE', 9000, 15000, 30, None),
        ('Memory Card 64GB', 'Storage', 'PIECE', 13000, 22000, 25, None),
        ('LED Bulb 9W', 'Lighting', 'PIECE', 1500, 3000, 90, None),
    ],
    'FASHION': [
        ('T-Shirt Cotton', 'Tops', 'PIECE', 6000, 15000, 50, None),
        ('Jeans Trousers', 'Bottoms', 'PIECE', 18000, 40000, 25, None),
        ('Ladies Dress', 'Dresses', 'PIECE', 15000, 35000, 20, None),
        ('Sneakers', 'Shoes', 'PAIR', 25000, 55000, 18, None),
        ('Leather Belt', 'Accessories', 'PIECE', 5000, 12000, 30, None),
        ('Baseball Cap', 'Accessories', 'PIECE', 3000, 8000, 40, None),
        ('Socks (Pack of 3)', 'Accessories', 'PACK', 3000, 7000, 60, None),
        ('Kitenge Fabric (6 yards)', 'Fabric', 'PIECE', 18000, 30000, 25, None),
        ('Handbag', 'Bags', 'PIECE', 15000, 32000, 15, None),
        ('Shirt Formal', 'Tops', 'PIECE', 12000, 28000, 22, None),
    ],
    'BEAUTY': [
        ('Hair Relaxer Kit', 'Hair Care', 'PIECE', 8000, 15000, 20, 700),
        ('Shampoo 400ml', 'Hair Care', 'BOTTLE', 5000, 9000, 30, 700),
        ('Hair Conditioner 400ml', 'Hair Care', 'BOTTLE', 5500, 10000, 25, 700),
        ('Body Lotion 400ml', 'Skin Care', 'BOTTLE', 6000, 11000, 35, 700),
        ('Nail Polish', 'Nails', 'PIECE', 1500, 4000, 60, 900),
        ('Hair Extensions Pack', 'Hair Pieces', 'PACK', 12000, 25000, 20, None),
        ('Face Wash 150ml', 'Skin Care', 'BOTTLE', 5000, 9500, 28, 700),
        ('Hair Oil 200ml', 'Hair Care', 'BOTTLE', 3000, 6500, 40, 700),
        ('Perfume Spray 100ml', 'Fragrance', 'BOTTLE', 9000, 20000, 18, 900),
        ('Hair Clips (Set)', 'Accessories', 'SET', 1000, 3000, 50, None),
    ],
    'AGROVET': [
        ('Maize Seed 2kg', 'Seeds', 'PACKET', 9000, 12500, 60, 300),
        ('NPK Fertilizer 50kg', 'Fertilizer', 'BAG', 72000, 85000, 30, None),
        ('Pesticide 1L', 'Crop Protection', 'BOTTLE', 18000, 25000, 24, 540),
        ('Animal Feed 25kg', 'Animal Feed', 'BAG', 28000, 34000, 40, 180),
        ('Dewormer 100ml', 'Veterinary', 'BOTTLE', 6500, 10000, 30, 540),
        ('Vegetable Seeds Pack', 'Seeds', 'PACKET', 1000, 2000, 120, 300),
        ('Sprayer 16L', 'Equipment', 'PIECE', 38000, 52000, 8, None),
        ('Tick Spray 500ml', 'Veterinary', 'BOTTLE', 9500, 14000, 25, 540),
        ('Vitamin Supplement 250ml', 'Veterinary', 'BOTTLE', 8000, 12500, 20, 540),
        ('Garden Hoe', 'Equipment', 'PIECE', 8000, 12000, 20, None),
    ],
    'OTHER': [
        ('Sample Item A', 'General', 'PIECE', 1000, 1500, 50, None),
        ('Sample Item B', 'General', 'PIECE', 2000, 3000, 40, None),
        ('Sample Item C', 'General', 'PIECE', 3000, 4500, 30, None),
        ('Sample Item D', 'General', 'PIECE', 4000, 6000, 25, None),
        ('Sample Item E', 'General', 'PIECE', 5000, 7500, 20, None),
        ('Sample Item F', 'Services', 'PIECE', 500, 1000, 100, None),
        ('Sample Item G', 'Services', 'PIECE', 1500, 2500, 60, None),
        ('Sample Item H', 'General', 'PACK', 6000, 9000, 15, None),
        ('Sample Item I', 'General', 'BOX', 10000, 15000, 12, None),
        ('Sample Item J', 'General', 'PIECE', 800, 1200, 80, None),
    ],
}


def catalog_for(business_type):
    return _P.get(business_type) or _P['OTHER']


@transaction.atomic
def load_demo_products(pharmacy, business_type, user=None):
    """Create the 10 sample products (with one stocked batch each) for a new account.

    Safe to call twice: it does nothing if the branch already has sample products.
    """
    if Product.objects.filter(pharmacy=pharmacy, is_demo=True).exists():
        return 0
    today = timezone.localdate()
    categories = {}
    created = 0
    for name, cat, unit, cost, price, qty, expiry_days in catalog_for(business_type):
        category = categories.get(cat)
        if category is None:
            category, _ = Category.objects.get_or_create(pharmacy=pharmacy, name=cat)
            categories[cat] = category
        product = Product.objects.create(
            pharmacy=pharmacy, name=name + SAMPLE_SUFFIX, category=category, unit=unit,
            requires_expiry=expiry_days is not None, reorder_level=max(5, qty // 5),
            created_by=user, is_demo=True,
        )
        batch = ProductBatch.objects.create(
            product=product, batch_number=f'SAMPLE-{product.pk:04d}',
            purchase_quantity=qty, units_per_purchase_unit=1, initial_quantity=qty, quantity=qty,
            purchase_price=Decimal(cost), selling_price=Decimal(price),
            expiry_date=(today + timedelta(days=expiry_days)) if expiry_days else None,
            received_by=user, note='Sample data. You can remove it any time.',
        )
        StockMovement.objects.create(
            product=product, batch=batch, movement_type=StockMovement.MovementType.NEW_BATCH,
            quantity_change=qty, note='Sample stock', performed_by=user,
        )
        created += 1
    return created


@transaction.atomic
def remove_demo_products(pharmacy):
    """Remove every sample product of a branch.

    A sample product that was already used in a test sale cannot be erased without breaking
    that sale's history, so it is archived (hidden) instead. Returns (deleted, archived).
    """
    deleted = archived = 0
    for product in Product.objects.filter(pharmacy=pharmacy, is_demo=True):
        if product.sale_items.exists():
            if product.is_active:
                product.is_active = False
                product.save(update_fields=['is_active', 'updated_at'])
            archived += 1
        else:
            product.delete()
            deleted += 1
    demo_categories = {row[1] for rows in _P.values() for row in rows}
    Category.objects.filter(pharmacy=pharmacy, name__in=demo_categories, products__isnull=True).delete()
    return deleted, archived


def has_demo_products(pharmacy):
    return Product.objects.filter(pharmacy=pharmacy, is_demo=True, is_active=True).exists()
