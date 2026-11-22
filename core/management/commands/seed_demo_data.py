"""
Generates realistic demo data for testing/demonstration:
- One Shop Owner account (fixed credentials, given by the project owner)
- Two independent pharmacy branches, each with its own product catalog
- At least 2000 products per branch, each with an opening batch
  (some batches deliberately expiring soon, most with normal expiry dates)
- One employee per branch
- A handful of debt sales per branch, some settled, some still owing

Run with: python manage.py seed_demo_data
Safe to re-run: skips creating the owner/branches/employees if they already
exist (matched by username/name), but will always top up products if a
branch has fewer than the target count.
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from accounts.phone_utils import normalize_phone_number
from pharmacies.models import Pharmacy
from inventory.models import Category, Product, ProductBatch, StockMovement
from sales.models import Sale, SaleItem, Debt
from hq.models import PaymentSettings, SignupRequest, Subscription
from sms_integration.models import SMSDebtPackage, SMSDebtSubscription, SMSWallet, CustomerSMSCredentials, SMSDebtPurchase
from notifications.models import Notification
from notifications.utils import notify
from chat.models import ChatThread, ChatMessage

PRODUCTS_PER_PHARMACY = 2000

DRUG_BASE_NAMES = [
    "Amoxicillin", "Paracetamol", "Ibuprofen", "Metronidazole", "Ciprofloxacin",
    "Artemether", "Lumefantrine", "AL (Artemether)", "Doxycycline", "Azithromycin",
    "Diclofenac", "Chlorpheniramine", "Cetirizine", "Loratadine", "Omeprazole",
    "Ranitidine", "Metformin", "Amlodipine", "Atenolol", "Furosemide",
    "Hydrochlorothiazide", "Prednisolone", "Dexamethasone", "Salbutamol", "Aminophylline",
    "Erythromycin", "Cefixime", "Ceftriaxone", "Ampicillin", "Cloxacillin",
    "Tetracycline", "Albendazole", "Mebendazole", "Quinine", "Sulfadoxine",
    "Vitamin C", "Vitamin B Complex", "Folic Acid", "Ferrous Sulphate", "Multivitamin",
    "Aspirin", "Codeine", "Tramadol", "Hyoscine", "Loperamide",
    "ORS (Oral Rehydration Salts)", "Zinc Sulphate", "Magnesium Trisilicate", "Calamine Lotion", "Povidone Iodine",
    "Gentian Violet", "Hydrogen Peroxide", "Chlorhexidine", "Insulin", "Glibenclamide",
    "Warfarin", "Digoxin", "Spironolactone", "Losartan", "Simvastatin",
]

FORMS_AND_UNITS = [
    ("Tablet", Product.Unit.TABLET),
    ("Capsule", Product.Unit.TABLET),
    ("Syrup", Product.Unit.BOTTLE),
    ("Injection", Product.Unit.VIAL),
    ("Suspension", Product.Unit.BOTTLE),
]

DOSAGES = ["5mg", "10mg", "20mg", "25mg", "50mg", "100mg", "200mg", "250mg", "500mg", "1000mg"]

CATEGORY_NAMES = [
    "Antibiotic", "Painkiller", "Antimalarial", "Antihistamine", "Antacid",
    "Cardiovascular", "Diabetes Care", "Vitamins & Supplements", "Antiparasitic", "Respiratory",
]

CUSTOMER_NAMES = [
    "Juma Hassan", "Fatuma Ally", "Neema Mushi", "Rajabu Kimaro", "Zainabu Said",
    "Emmanuel Mollel", "Halima Juma", "Baraka Msangi", "Grace Chuma", "Yusuph Mrisho",
]


class Command(BaseCommand):
    help = "Seeds a complete demonstration environment: HQ, owner, branches, products, packaging, stock, sales, debts, subscriptions, SMS, notifications, chat, and signup workflow."

    def add_arguments(self, parser):
        parser.add_argument('--products-per-branch', type=int, default=PRODUCTS_PER_PHARMACY)
        parser.add_argument(
            '--force-demo',
            action='store_true',
            help='Allow demo data seeding when DEBUG=False. Never use this on the real production tenant.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not options['force_demo']:
            raise CommandError(
                'Demo seeding is disabled when DEBUG=False. Use import_real_legacy_data for the real '
                'database.json, or pass --force-demo only for a deliberate isolated demo environment.'
            )

        target_count = options['products_per_branch']

        owner = self._seed_owner()
        categories = self._seed_categories()

        branch_specs = [
            {"name": "Paranga", "location": "Cheku, Dodoma, Chemba"},
            {"name": "Cheku", "location": "Cheku, Dodoma, Chemba"},
        ]

        for spec in branch_specs:
            pharmacy = self._seed_pharmacy(spec["name"], spec["location"], owner)
            employee = self._seed_employee(pharmacy)
            self._seed_products(pharmacy, employee, categories, target_count)
            self._seed_packaging_examples(pharmacy, employee, categories)
            self._seed_debts(pharmacy, employee)
            self._seed_sales(pharmacy, employee)
            self.stdout.write(self.style.SUCCESS(
                f"[{pharmacy.name}] ready — {pharmacy.products.count()} products, employee '{employee.username}'."
            ))

        self._seed_platform_data(owner)
        call_command('seed_daily_report_packages')
        self._seed_customer_demo(owner)
        self._seed_chat(owner)
        self._seed_notifications()

        self.stdout.write(self.style.SUCCESS(
            "\nDemo environment complete.\n"
            "Shop Owner login -> username: mringobey | password: mringobey@123\n"
            "Employee password -> Employee@123\n"
            "Demo System Administrator -> username: demo.superadmin | password: DemoAdmin@123\n"
            "Premium Debt SMS packages, Intelligent Report SMS packages, HQ signup workflow, subscriptions, SMS wallet, notifications and chat are seeded too."
        ))

    # ------------------------------------------------------------------ owner
    def _seed_owner(self):
        phone_number = normalize_phone_number("+255610117824")
        owner = User.objects.filter(username__iexact="mringobey").first()
        if owner:
            self.stdout.write("Owner 'mringobey' already exists — reusing.")
            return owner

        owner = User.objects.create_user(
            username="mringobey",
            password="mringobey@123",
            first_name="Mringo",
            last_name="Bey",
            phone_number=phone_number,
            role=User.Role.ADMIN,
            is_staff=False,
            is_superuser=False,
        )
        self.stdout.write(self.style.SUCCESS(f"Created owner account '{owner.username}'."))
        return owner

    # -------------------------------------------------------------- pharmacy
    def _seed_pharmacy(self, name, location, owner):
        pharmacy, created = Pharmacy.objects.get_or_create(name=name, defaults={'location': location})
        owner.managed_pharmacies.add(pharmacy)
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created pharmacy branch '{pharmacy.name}'."))
        return pharmacy

    # -------------------------------------------------------------- category
    def _seed_categories(self):
        categories = []
        for name in CATEGORY_NAMES:
            category, _ = Category.objects.get_or_create(pharmacy=pharmacy, name=name)
            categories.append(category)
        return categories

    # -------------------------------------------------------------- employee
    def _seed_employee(self, pharmacy):
        existing = pharmacy.employees.filter(role=User.Role.EMPLOYEE).first()
        if existing:
            return existing

        base_phone_number = 700000000 + pharmacy.id * 1000 + random.randint(1, 998)
        phone_number = normalize_phone_number(f"0{base_phone_number}")
        username = f"{pharmacy.name.lower()}staff"

        employee = User.objects.create_user(
            username=username,
            password="Employee@123",
            first_name=f"{pharmacy.name}",
            last_name="Attendant",
            phone_number=phone_number,
            role=User.Role.EMPLOYEE,
            pharmacy=pharmacy,
            must_change_password=True,
            monthly_salary=Decimal('300000'),
            morning_meal_allowance=Decimal('2000'),
            afternoon_meal_allowance=Decimal('2000'),
            evening_meal_allowance=Decimal('2000'),
        )
        self.stdout.write(self.style.SUCCESS(
            f"Created employee '{employee.username}' for '{pharmacy.name}' (temp password: Employee@123)."
        ))
        return employee

    # --------------------------------------------------------------- products
    def _seed_products(self, pharmacy, employee, categories, target_count):
        existing_count = pharmacy.products.count()
        if existing_count >= target_count:
            self.stdout.write(f"[{pharmacy.name}] already has {existing_count} products — skipping product seeding.")
            return

        today = timezone.localdate()
        combos = [
            (base, form_label, unit, dosage)
            for base in DRUG_BASE_NAMES
            for form_label, unit in FORMS_AND_UNITS
            for dosage in DOSAGES
        ]
        random.shuffle(combos)
        combos = combos[:target_count]

        products_to_create = []
        for base, form_label, unit, dosage in combos:
            name = f"{base} {dosage} {form_label}"
            products_to_create.append(Product(
                pharmacy=pharmacy, name=name, category=random.choice(categories), unit=unit,
                reorder_level=random.choice([10, 15, 20, 25]), created_by=employee,
            ))

        CHUNK = 500
        for i in range(0, len(products_to_create), CHUNK):
            chunk = products_to_create[i:i + CHUNK]
            Product.objects.bulk_create(chunk, ignore_conflicts=True)

        # Re-fetch to get real PKs reliably across all DB backends
        # (bulk_create's return value can't be trusted for PKs when ignore_conflicts=True).
        products = list(pharmacy.products.order_by('-id')[:len(products_to_create)])

        batches_to_create = []
        for idx, product in enumerate(products):
            is_near_expiry = (idx % 20 == 0)  # ~5% of products get a soon-to-expire batch
            if is_near_expiry:
                expiry_date = today + timedelta(days=random.randint(3, 25))
            else:
                expiry_date = today + timedelta(days=random.randint(120, 730))

            quantity = random.randint(15, 500)
            purchase_price = Decimal(random.choice([500, 800, 1000, 1500, 2000, 3000, 3500]))
            selling_price = purchase_price + Decimal(random.choice([300, 500, 800, 1000, 1500]))

            batches_to_create.append(ProductBatch(
                product=product, batch_number=f"BN-{today:%Y}-{random.randint(100000, 999999)}",
                initial_quantity=quantity, quantity=quantity,
                purchase_price=purchase_price, selling_price=selling_price,
                expiry_date=expiry_date, received_by=employee, note="Seed data — opening batch",
            ))

        for i in range(0, len(batches_to_create), CHUNK):
            chunk = batches_to_create[i:i + CHUNK]
            ProductBatch.objects.bulk_create(chunk)

        self.stdout.write(f"[{pharmacy.name}] seeded {len(products)} products with opening batches.")

    # ---------------------------------------------------------- packaging demos
    def _seed_packaging_examples(self, pharmacy, employee, categories):
        """Add a few explicit multi-unit examples so the packaging/WAC workflow is visible."""
        examples = [
            ('Demo Paracetamol 500mg', Product.Unit.TABLET, Product.Unit.BOX, 100, 5, Decimal('4200'), Decimal('100')),
            ('Demo Drinking Water 500ml', Product.Unit.BOTTLE, Product.Unit.BOTTLE, 1, 7, Decimal('500'), Decimal('800')),
            ('Demo Body Lotion 250ml', Product.Unit.BOTTLE, Product.Unit.CARTON, 24, 2, Decimal('72000'), Decimal('4500')),
        ]
        category = categories[0] if categories else None
        for name, base_unit, purchase_unit, conversion, purchase_qty, package_price, selling_price in examples:
            product, _ = Product.objects.get_or_create(
                pharmacy=pharmacy, name=name,
                defaults={'category': category, 'unit': base_unit, 'reorder_level': 10, 'created_by': employee},
            )
            if ProductBatch.objects.filter(product=product, note='Seed packaging example').exists():
                continue
            batch = ProductBatch(
                product=product, batch_number=f"PK-{timezone.localdate():%Y}-{pharmacy.id}-{random.randint(1000,9999)}",
                purchase_unit=purchase_unit, purchase_quantity=purchase_qty,
                units_per_purchase_unit=conversion, purchase_unit_price=package_price,
                initial_quantity=purchase_qty * conversion, quantity=purchase_qty * conversion,
                purchase_price=(package_price / Decimal(conversion)).quantize(Decimal('0.01')),
                selling_price=selling_price, expiry_date=timezone.localdate() + timedelta(days=365),
                received_by=employee, note='Seed packaging example',
            )
            batch.save()
            StockMovement.objects.create(product=product, batch=batch,
                movement_type=StockMovement.MovementType.NEW_BATCH,
                quantity_change=batch.initial_quantity,
                note='Seed packaging example', performed_by=employee)
        self.stdout.write(f"[{pharmacy.name}] packaging examples are ready.")

    # -------------------------------------------------------------- demo sales
    def _seed_sales(self, pharmacy, employee):
        """Create ordinary cash/mobile-money sales so reports and POS history are populated."""
        if Sale.objects.filter(pharmacy=pharmacy, customer_name='Demo Customer').exists():
            self.stdout.write(f"[{pharmacy.name}] demo sales already seeded — skipping.")
            return
        batches = list(ProductBatch.objects.filter(product__pharmacy=pharmacy, quantity__gt=10, expiry_date__gte=timezone.localdate()).select_related('product')[:20])
        if not batches:
            return
        for index in range(8):
            batch = batches[index % len(batches)]
            qty = min(random.randint(1, 3), batch.quantity)
            sale = Sale.objects.create(
                pharmacy=pharmacy, served_by=employee,
                payment_method=random.choice([Sale.PaymentMethod.CASH, Sale.PaymentMethod.MOBILE_MONEY]),
                customer_name='Demo Customer', customer_phone=normalize_phone_number(f"0712{pharmacy.id:02d}{index:04d}"),
            )
            cost = batch.product.weighted_average_cost or batch.purchase_price
            SaleItem.objects.create(sale=sale, product=batch.product, batch=batch, quantity=qty,
                                    unit_price=batch.selling_price, unit_cost=cost)
            batch.quantity -= qty
            batch.save(update_fields=['quantity'])
            StockMovement.objects.create(product=batch.product, batch=batch,
                movement_type=StockMovement.MovementType.SALE, quantity_change=-qty,
                note=f"Seed demo sale #{sale.id}", performed_by=employee)
        self.stdout.write(f"[{pharmacy.name}] seeded 8 demo non-debt sales.")

    # ------------------------------------------------------------ platform data
    def _seed_platform_data(self, owner):
        PaymentSettings.objects.update_or_create(pk=1, defaults={
            'lipa_namba': '5678900', 'payee_name': 'Mutech Apex Innovations',
            'support_phone': '+255700000000', 'owner_notification_phone': '+255700000000',
            'instructions_text': 'Demo payment instructions — replace these values before accepting real payments.',
            'monthly_price': Decimal('12500'), 'yearly_price': Decimal('150000'),
        })
        call_command('seed_sms_packages', verbosity=0)
        call_command('seed_hq_demo_data', verbosity=0)
        SMSWallet.objects.get_or_create(owner=owner, defaults={'credits_granted': 500})
        SMSDebtPackage.objects.filter(is_active=True).order_by('sort_order').first()
        self.stdout.write('HQ payment settings, SMS packages and owner SMS wallet are ready.')

        admin, created = User.objects.get_or_create(username='demo.superadmin', defaults={
            'first_name': 'Demo', 'last_name': 'System Administrator', 'phone_number': '+255712000098',
            'role': User.Role.ADMIN, 'is_staff': True, 'is_superuser': True,
        })
        if created:
            admin.set_password('DemoAdmin@123')
            admin.save()

        # Give the main demo owner an active subscription so middleware does not
        # block any customer-facing page during local demonstrations.
        subscription, created = Subscription.objects.get_or_create(
            owner=owner,
            defaults={'plan': Subscription.Plan.YEARLY, 'status': Subscription.Status.ACTIVE,
                      'start_date': timezone.localdate(), 'end_date': timezone.localdate() + timedelta(days=365),
                      'amount_paid': Decimal('150000')},
        )
        if not created and (subscription.status != Subscription.Status.ACTIVE or subscription.end_date < timezone.localdate()):
            subscription.status = Subscription.Status.ACTIVE
            subscription.end_date = timezone.localdate() + timedelta(days=365)
            subscription.save(update_fields=['status', 'end_date'])

    def _seed_customer_demo(self, owner):
        """Populate one customer account, subscription and Premium Debt SMS entitlement."""
        package = SMSDebtPackage.objects.filter(code='DEBT10000', is_active=True).first()
        customer, created = User.objects.get_or_create(username='demo.customer', defaults={
            'first_name': 'Demo', 'last_name': 'Customer', 'phone_number': '+255712000099',
            'role': User.Role.ADMIN, 'is_active': True,
        })
        if created:
            customer.set_password('DemoCustomer@123')
            customer.save()
        branch = Pharmacy.objects.filter(name='Cheku').first() or Pharmacy.objects.order_by('id').first()
        if branch:
            customer.managed_pharmacies.add(branch)
        SMSWallet.objects.get_or_create(owner=customer, defaults={'credits_granted': 500})
        creds, _ = CustomerSMSCredentials.objects.get_or_create(owner=customer, defaults={'access_enabled': True})
        if not creds.access_enabled:
            creds.access_enabled = True
            creds.save(update_fields=['access_enabled', 'updated_at'])
        subscription, _ = Subscription.objects.get_or_create(owner=customer, defaults={
            'plan': Subscription.Plan.MONTHLY, 'status': Subscription.Status.ACTIVE,
            'start_date': timezone.localdate(), 'end_date': timezone.localdate() + timedelta(days=30),
            'amount_paid': Decimal('12500'),
        })
        if subscription.status != Subscription.Status.ACTIVE or subscription.end_date < timezone.localdate():
            subscription.status = Subscription.Status.ACTIVE
            subscription.end_date = timezone.localdate() + timedelta(days=30)
            subscription.save(update_fields=['status', 'end_date'])
        if package:
            SMSDebtSubscription.objects.get_or_create(owner=customer, package=package, status=SMSDebtSubscription.Status.ACTIVE,
                defaults={'sms_credits': package.sms_credits, 'sms_used': 25, 'start_date': timezone.localdate(),
                          'end_date': timezone.localdate() + timedelta(days=package.duration_days),
                          'amount_paid': package.price, 'payment_reference': 'DEMO-PREMIUM-10000'})
            if branch:
                SMSDebtPurchase.objects.get_or_create(owner=customer, package=package, payment_reference='DEMO-PURCHASE-001',
                    defaults={'amount': package.price, 'status': SMSDebtPurchase.Status.APPROVED,
                              'customer_note': 'Demo purchase record', 'admin_note': 'Seeded demo purchase'})
        self.stdout.write('Demo customer account and Premium Debt SMS entitlement are ready.')

    def _seed_chat(self, owner):
        """Create representative branch/all-branch/customer-group conversations."""
        for pharmacy in owner.managed_pharmacies.all():
            employee = pharmacy.employees.filter(role=User.Role.EMPLOYEE).first()
            if not employee:
                continue
            thread, _ = ChatThread.objects.get_or_create(pharmacy=pharmacy, employee=None, manager=None, platform_group=False)
            if not thread.messages.exists():
                ChatMessage.objects.create(thread=thread, sender=owner,
                    text=f"Welcome to the {pharmacy.name} branch demo chat. Stock and sales updates can be discussed here.")
                ChatMessage.objects.create(thread=thread, sender=employee,
                    text="Demo employee online. Ready for the next shift and stock review.")
        all_thread, _ = ChatThread.objects.get_or_create(pharmacy=None, manager=owner, employee=None, platform_group=False)
        if not all_thread.messages.exists():
            ChatMessage.objects.create(thread=all_thread, sender=owner,
                text='All-branches demo discussion: review sales, stock and expiry alerts before closing.')
        customer_thread, _ = ChatThread.objects.get_or_create(pharmacy=None, manager=owner, employee=None, platform_group=True)
        if not customer_thread.messages.exists():
            ChatMessage.objects.create(thread=customer_thread, sender=owner,
                text='Welcome customers. This is the demo System Owner & Customers group.')
        self.stdout.write('Demo chat threads and messages are ready.')

    def _seed_notifications(self):
        users = list(User.objects.filter(is_active=True)[:20])
        for user in users:
            if user.notifications.exists():
                continue
            notify(user, 'Welcome to SmartPOS', 'Your demo workspace is ready for testing.',
                   category=Notification.Category.SYSTEM, level=Notification.Level.SUCCESS)
            notify(user, 'Stock Review', 'Review low-stock and expiry alerts from the dashboard.',
                   category=Notification.Category.STOCK_ALERT, level=Notification.Level.WARNING)
            notify(user, 'Sales Report Ready', 'Use Reports & Analytics to review sales, cost and gross profit.',
                   category=Notification.Category.SALES, level=Notification.Level.INFO)
        self.stdout.write('Demo notifications are ready.')

    # ----------------------------------------------------------------- debts
    def _seed_debts(self, pharmacy, employee):
        if Debt.objects.filter(pharmacy=pharmacy).exists():
            self.stdout.write(f"[{pharmacy.name}] debts already seeded — skipping.")
            return

        candidate_batches = list(
            ProductBatch.objects.filter(product__pharmacy=pharmacy, quantity__gt=5).order_by('?')[:8]
        )
        for batch in candidate_batches:
            qty = random.randint(1, min(5, batch.quantity))
            customer_name = random.choice(CUSTOMER_NAMES)
            customer_phone = normalize_phone_number(f"07{random.randint(10000000, 99999999)}")

            sale = Sale.objects.create(
                pharmacy=pharmacy, served_by=employee, payment_method=Sale.PaymentMethod.DEBT,
                customer_name=customer_name, customer_phone=customer_phone,
            )
            wac = batch.product.weighted_average_cost or batch.purchase_price
            SaleItem.objects.create(
                sale=sale, product=batch.product, batch=batch, quantity=qty,
                unit_price=batch.selling_price, unit_cost=wac,
            )
            batch.quantity -= qty
            batch.save(update_fields=['quantity'])
            StockMovement.objects.create(
                product=batch.product, batch=batch, movement_type=StockMovement.MovementType.SALE,
                quantity_change=-qty, note=f"Seed debt sale #{sale.id}", performed_by=employee,
            )

            total = batch.selling_price * qty
            is_settled = random.random() < 0.3  # ~30% already paid off
            Debt.objects.create(
                pharmacy=pharmacy, sale=sale, customer_name=customer_name, customer_phone=customer_phone,
                amount_owed=total, amount_paid=total if is_settled else Decimal('0'), is_settled=is_settled,
            )

        self.stdout.write(f"[{pharmacy.name}] seeded {len(candidate_batches)} debt sale(s).")
