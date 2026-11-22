"""
Production data import: creates (or reuses) the owner account 'mringobey',
the two real pharmacy branches, and imports the real product catalog
(exact names/prices from inventory/seed_data/real_products.json) into EACH
branch as its own separate opening stock.

This does NOT create fake employees, customers, or sales — this is real
production seeding, not demo data. Add real employees yourself afterwards
from the Employees page.

Run with: python manage.py seed_real_pharmacy_data
Safe to re-run: skips branches/products that already exist by name.
"""
import json
import os
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from accounts.phone_utils import normalize_phone_number
from pharmacies.models import Pharmacy
from inventory.models import Category, Product, ProductBatch, StockMovement

BRANCH_NAMES = [
    "Mringobey Medics Paranga",
    "Mringobey Medics Kelema",
]

PRODUCTS_FIXTURE = os.path.join(
    settings.BASE_DIR, 'inventory', 'seed_data', 'real_products.json'
)


class Command(BaseCommand):
    help = "Imports the real product catalog into the two Mringobey Medics branches (production data, not demo)."

    @transaction.atomic
    def handle(self, *args, **options):
        with open(PRODUCTS_FIXTURE) as f:
            products_data = json.load(f)

        self.stdout.write(f"Loaded {len(products_data)} products from {PRODUCTS_FIXTURE}")

        owner = self._get_or_create_owner()
        categories = self._ensure_categories(pharmacy, products_data)

        for branch_name in BRANCH_NAMES:
            pharmacy = self._get_or_create_branch(branch_name, owner)
            created, skipped = self._import_products(pharmacy, categories, products_data, owner)
            self.stdout.write(self.style.SUCCESS(
                f"[{pharmacy.name}] {created} product(s) imported, {skipped} already existed (skipped)."
            ))

        self.stdout.write(self.style.SUCCESS(
            "\nDone. Log in as 'mringobey' and open each branch (switch via the sidebar) to review stock, "
            "then add real employees from the Employees page."
        ))

    def _get_or_create_owner(self):
        owner = User.objects.filter(username__iexact="mringobey").first()
        if owner:
            self.stdout.write("Owner 'mringobey' already exists — reusing.")
            return owner

        phone_number = normalize_phone_number("+255610117824")
        owner = User.objects.create_user(
            username="mringobey", password="mringobey@123",
            first_name="Mringo", last_name="Bey", phone_number=phone_number,
            role=User.Role.ADMIN, is_staff=False, is_superuser=False,
        )
        self.stdout.write(self.style.SUCCESS(f"Created owner account 'mringobey'."))
        return owner

    def _get_or_create_branch(self, name, owner):
        pharmacy, created = Pharmacy.objects.get_or_create(name=name)
        owner.managed_pharmacies.add(pharmacy)
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created branch '{name}'."))
        else:
            self.stdout.write(f"Branch '{name}' already exists — reusing.")
        return pharmacy

    def _ensure_categories(self, pharmacy, products_data):
        names = {p['category'] for p in products_data if p.get('category')}
        categories = {}
        for name in names:
            category, _ = Category.objects.get_or_create(pharmacy=pharmacy, name=name)
            categories[name] = category
        return categories

    def _import_products(self, pharmacy, categories, products_data, owner):
        created, skipped = 0, 0
        valid_units = {choice[0] for choice in Product.Unit.choices}

        for row in products_data:
            name = row['name']
            if Product.objects.filter(pharmacy=pharmacy, name__iexact=name, is_active=True).exists():
                skipped += 1
                continue

            unit = row['unit'] if row['unit'] in valid_units else 'PIECE'
            expiry_date = datetime.strptime(row['expiry_date'], '%Y-%m-%d').date()

            product = Product.objects.create(
                pharmacy=pharmacy, name=name, category=categories.get(row['category']),
                unit=unit, reorder_level=row['reorder_level'], created_by=owner,
            )
            batch = ProductBatch.objects.create(
                product=product, batch_number=row['batch_number'],
                initial_quantity=row['quantity'], quantity=row['quantity'],
                purchase_price=row['purchase_price'], selling_price=row['selling_price'],
                expiry_date=expiry_date, received_by=owner,
                note="Opening stock — production import",
            )
            StockMovement.objects.create(
                product=product, batch=batch, movement_type=StockMovement.MovementType.NEW_BATCH,
                quantity_change=batch.quantity, note="Opening stock — production import", performed_by=owner,
            )
            created += 1

        return created, skipped
