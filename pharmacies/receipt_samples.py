"""Sample receipt lines that match the kind of business a branch runs.

Used by the Receipt Design live preview and the "Preview sample PDF" so a hardware
shop sees cement and nails, a boutique sees clothes, a salon sees cosmetics, etc.
Each row: (name, unit label, quantity, unit price TZS, expiry in months or None).
`expiry` is only set for goods that really expire (medicine, food, cosmetics, farm inputs).
"""
import datetime
from decimal import Decimal

SAMPLE_ITEMS = {
    'PHARMACY': [
        ('Paracetamol 500mg', 'Tablet', 2, 1500, 14),
        ('Amoxicillin 500mg Capsules', 'Strip', 1, 4500, 8),
        ('Vitamin C 1000mg', 'Tablet', 3, 2000, 18),
    ],
    'RETAIL': [
        ('Sugar 1kg', 'Packet', 2, 3200, None),
        ('Cooking Oil 1L', 'Bottle', 1, 6000, None),
        ('Bar Soap (Large)', 'Piece', 3, 1500, None),
    ],
    'WHOLESALE': [
        ('Rice 25kg Bag', 'Bag', 2, 72000, None),
        ('Cooking Oil 20L', 'Jerrycan', 1, 98000, None),
        ('Sugar 50kg Bag', 'Bag', 1, 150000, None),
    ],
    'SUPERMARKET': [
        ('Fresh Milk 1L', 'Piece', 4, 2400, 1),
        ('Bread (Loaf)', 'Piece', 2, 2200, 1),
        ('Toothpaste 100ml', 'Tube', 1, 2800, 20),
    ],
    'RESTAURANT': [
        ('Chicken & Chips', 'Plate', 2, 9000, None),
        ('Fresh Juice 500ml', 'Glass', 2, 3000, None),
        ('Chapati', 'Piece', 4, 500, None),
    ],
    'HARDWARE': [
        ('Cement 50kg Bag', 'Bag', 10, 18500, None),
        ('Nails 3 inch (1kg)', 'Packet', 2, 4500, None),
        ('Paint 4L White', 'Can', 1, 35000, None),
    ],
    'ELECTRONICS': [
        ('Phone Charger 20W', 'Piece', 1, 25000, None),
        ('USB-C Cable 1m', 'Piece', 2, 8000, None),
        ('Phone Screen Protector', 'Piece', 1, 5000, None),
    ],
    'FASHION': [
        ('Cotton T-Shirt', 'Piece', 2, 25000, None),
        ('Kitenge Dress', 'Piece', 1, 55000, None),
        ('Leather Belt', 'Piece', 1, 18000, None),
    ],
    'BEAUTY': [
        ('Body Lotion 400ml', 'Bottle', 1, 12000, 20),
        ('Hair Relaxer Kit', 'Box', 1, 18000, 18),
        ('Lipstick (Matte)', 'Piece', 2, 9000, 24),
    ],
    'AGROVET': [
        ('Maize Seeds 2kg', 'Packet', 3, 14000, 10),
        ('NPK Fertilizer 50kg', 'Bag', 1, 85000, 24),
        ('Pesticide 1L', 'Bottle', 1, 28000, 18),
    ],
    'ONLINE_SELLER': [
        ('Wireless Earbuds', 'Piece', 1, 45000, None),
        ('Phone Case', 'Piece', 2, 8000, None),
        ('Delivery Fee', 'Service', 1, 5000, None),
    ],
    'REAL_ESTATE': [
        ('Monthly Rent - 2 Bedroom', 'Month', 1, 450000, None),
        ('Service Charge', 'Month', 1, 30000, None),
        ('Water & Electricity Deposit', 'Fee', 1, 50000, None),
    ],
    'OTHER': [
        ('Product A', 'Piece', 2, 5000, None),
        ('Product B', 'Piece', 1, 12000, None),
        ('Service Fee', 'Service', 1, 3000, None),
    ],
}


_BATCHES = ('B-2291', 'A-1180', 'C-3047')


def _add_months(day, months):
    total = day.month - 1 + months
    year, month = day.year + total // 12, total % 12 + 1
    return day.replace(year=year, month=month, day=1)


def sample_lines(business_type, today=None):
    """Rows for `business_type` as dicts (falls back to OTHER)."""
    today = today or datetime.date.today()
    rows = []
    for i, (name, unit, qty, price, exp) in enumerate(SAMPLE_ITEMS.get(business_type) or SAMPLE_ITEMS['OTHER']):
        expiry = _add_months(today, exp) if exp else None
        rows.append({
            'name': name, 'unit': unit, 'quantity': qty, 'unit_price': Decimal(price),
            'subtotal': Decimal(price * qty), 'batch': _BATCHES[i % 3],
            'expiry': expiry,
        })
    return rows


def sample_total(rows):
    return sum((r['subtotal'] for r in rows), Decimal(0))
