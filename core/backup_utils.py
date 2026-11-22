"""Backup/export helpers.

There are deliberately two different products here:

* build_user_data_backup_zip(owner): a safe, tenant-isolated, human-readable
  export for a Shop Owner. It never contains passwords, API keys, OTPs,
  sessions, Django permissions or another tenant's data.
* build_backup_zip(): the existing full-system Django fixture + media archive
  for platform recovery. This is for Superadmin/CLI use only.
"""
import csv
import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.core import management
from django.core.files.storage import default_storage
from django.db.models import Q
from django.utils import timezone

from accounts.models import User, WeeklyMealSchedule, MealAdjustment
from chat.models import ChatAttachment, ChatMessage, ChatThread
from inventory.models import Category, Product, ProductBatch, StockImport, StockMovement
from pharmacies.models import BranchAddOn, Pharmacy
from sales.models import Debt, Sale, SaleItem
from expenses.models import Expense, CashDay, ExpenseCategory
from sms_integration.models import (
    SMSDebtPurchase, SMSDebtSubscription, SMSLog, SMSReceiptPurchase,
    SMSReceiptSubscription, SMSWallet,
)
from hq.models import Subscription


USER_EXPORT_RETENTION = 10


def _scalar(value):
    if value is None:
        return ''
    if isinstance(value, (datetime, date)):
        return timezone.localtime(value).isoformat() if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    return str(value)


def _write_csv(zf, filename, headers, rows):
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_scalar(v) for v in row])
    zf.writestr(filename, buffer.getvalue().encode('utf-8-sig'))


def _copy_storage_file(zf, storage_name, archive_name):
    if not storage_name:
        return False
    try:
        with default_storage.open(storage_name, 'rb') as src:
            zf.writestr(archive_name, src.read())
        return True
    except (FileNotFoundError, OSError, NotImplementedError):
        return False


def create_user_backup(owner):
    owner = owner.business_owner() if hasattr(owner, 'business_owner') else owner
    """Build the owner's data backup, store it, apply retention and return the UserDataBackup row.

    Shared by the Data Protection page and the permanent-delete safeguard so both
    create backups in exactly the same way.
    """
    from core.models import UserDataBackup

    filename, zip_bytes, counts = build_user_data_backup_zip(owner)
    storage_path = f'backups/user/{owner.pk}/{filename}'
    if default_storage.exists(storage_path):
        default_storage.delete(storage_path)
    saved_path = default_storage.save(storage_path, io.BytesIO(zip_bytes))
    backup = UserDataBackup.objects.create(
        owner=owner,
        filename=filename,
        storage_path=saved_path,
        size_bytes=len(zip_bytes),
        record_counts=counts,
    )
    old = list(UserDataBackup.objects.filter(owner=owner).order_by('-created_at')[USER_EXPORT_RETENTION:])
    for item in old:
        try:
            default_storage.delete(item.storage_path)
        finally:
            item.delete()
    return backup


def build_user_data_backup_zip(owner):
    """Build a readable ZIP containing ONLY data belonging to ``owner``.

    An owner can manage multiple active/inactive branches. All included
    records are selected from those branches or directly from the owner.
    Sensitive authentication/SMS-provider secrets are intentionally omitted.
    """
    if not owner or not owner.is_management_user() or owner.is_superuser:
        raise ValueError('User data backups are only available to Shop Owners/Supervisors.')

    owner = owner.business_owner()
    pharmacies = list(owner.managed_pharmacies.all().order_by('id'))
    pharmacy_ids = [p.id for p in pharmacies]

    products = Product.objects.filter(pharmacy_id__in=pharmacy_ids).select_related('category', 'created_by').order_by('id')
    product_ids = list(products.values_list('id', flat=True))
    product_batches = ProductBatch.objects.filter(product_id__in=product_ids).select_related('product', 'stock_import', 'received_by').order_by('id')
    stock_imports = StockImport.objects.filter(pharmacy_id__in=pharmacy_ids).select_related('created_by').order_by('id')
    movements = StockMovement.objects.filter(product_id__in=product_ids).select_related('product', 'batch', 'performed_by').order_by('id')
    sales = Sale.objects.filter(pharmacy_id__in=pharmacy_ids).select_related('served_by', 'receipt_sms_sent_by').order_by('id')
    sale_ids = list(sales.values_list('id', flat=True))
    sale_items = SaleItem.objects.filter(sale_id__in=sale_ids).select_related('sale', 'product', 'batch').order_by('id')
    debts = Debt.objects.filter(pharmacy_id__in=pharmacy_ids).select_related('sale').order_by('id')

    # Customers are not a standalone table in this application; customer
    # identity is stored on sales/debts. Build a de-duplicated customer file
    # from those tenant-scoped records without inventing a new customer model.
    customer_map = {}
    for row in Sale.objects.filter(pharmacy_id__in=pharmacy_ids).values(
        'customer_name', 'customer_phone'
    ).iterator():
        key = (row['customer_phone'] or '').strip() or (row['customer_name'] or '').strip().lower()
        if key:
            customer_map[key] = (row['customer_name'] or '', row['customer_phone'] or '')
    for row in Debt.objects.filter(pharmacy_id__in=pharmacy_ids).values(
        'customer_name', 'customer_phone'
    ).iterator():
        key = (row['customer_phone'] or '').strip() or (row['customer_name'] or '').strip().lower()
        if key:
            customer_map[key] = (row['customer_name'] or '', row['customer_phone'] or '')
    customers = sorted(customer_map.values(), key=lambda x: (x[0].lower(), x[1]))
    branch_addons = BranchAddOn.objects.filter(pharmacy_id__in=pharmacy_ids).select_related('owner', 'confirmed_by').order_by('id')
    expense_categories = ExpenseCategory.objects.filter(Q(pharmacy_id__in=pharmacy_ids) | Q(pharmacy__isnull=True), is_active=True).order_by('id')
    expenses = Expense.objects.filter(pharmacy_id__in=pharmacy_ids).select_related('category', 'submitted_by', 'decided_by').order_by('id')
    cash_days = CashDay.objects.filter(pharmacy_id__in=pharmacy_ids).select_related('opened_by', 'closed_by').order_by('id')

    employee_ids = list(
        User.objects.filter(role=User.Role.EMPLOYEE, pharmacy_id__in=pharmacy_ids).values_list('id', flat=True)
    )
    employee_ids.append(owner.id)
    employee_ids = sorted(set(employee_ids))
    users = User.objects.filter(id__in=employee_ids).order_by('id')
    meal_schedules = WeeklyMealSchedule.objects.filter(employee_id__in=employee_ids).select_related('employee').order_by('employee_id', 'weekday')
    meal_adjustments = MealAdjustment.objects.filter(employee_id__in=employee_ids).select_related('employee', 'created_by').order_by('employee_id', '-date')

    categories = Category.objects.filter(products__pharmacy_id__in=pharmacy_ids).distinct().order_by('id')

    receipt_subs = SMSReceiptSubscription.objects.filter(owner=owner).select_related('package').order_by('id')
    receipt_purchases = SMSReceiptPurchase.objects.filter(owner=owner).select_related('package', 'reviewed_by').order_by('id')
    debt_subs = SMSDebtSubscription.objects.filter(owner=owner).select_related('package').order_by('id')
    debt_purchases = SMSDebtPurchase.objects.filter(owner=owner).select_related('package', 'reviewed_by').order_by('id')
    sms_wallet = SMSWallet.objects.filter(owner=owner).first()
    sms_logs = SMSLog.objects.filter(owner=owner).exclude(category__in=[SMSLog.Category.CREDENTIALS, SMSLog.Category.OTP]).order_by('id')
    subscription = Subscription.objects.filter(owner=owner).first()

    # Branch chat only. The platform-wide customer group is deliberately
    # excluded because it can contain messages belonging to other customers.
    chat_threads = ChatThread.objects.filter(
        Q(pharmacy_id__in=pharmacy_ids) | Q(manager=owner, pharmacy__isnull=True, platform_group=False)
    ).filter(platform_group=False).distinct().order_by('id')
    chat_thread_ids = list(chat_threads.values_list('id', flat=True))
    chat_messages = ChatMessage.objects.filter(thread_id__in=chat_thread_ids).select_related('sender').order_by('id')
    chat_message_ids = list(chat_messages.values_list('id', flat=True))
    chat_attachments = ChatAttachment.objects.filter(message_id__in=chat_message_ids).order_by('id')

    timestamp = timezone.localtime().strftime('%Y%m%d_%H%M%S')
    safe_name = ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in (owner.username or 'owner'))[:40]
    filename = f'pharmacy_manager_my_data_{safe_name}_{timestamp}.zip'

    counts = {
        'pharmacies': len(pharmacies), 'employees': users.count() - 1 if owner.id in employee_ids else users.count(),
        'products': len(product_ids), 'batches': product_batches.count(), 'stock_imports': stock_imports.count(),
        'stock_movements': movements.count(), 'sales': len(sale_ids), 'sale_items': sale_items.count(), 'customers': len(customers),
        'debts': debts.count(), 'categories': categories.count(), 'expense_categories': expense_categories.count(), 'expenses': expenses.count(), 'cash_days': cash_days.count(), 'chat_threads': len(chat_thread_ids),
        'chat_messages': len(chat_message_ids), 'chat_attachments': chat_attachments.count(),
        'receipt_sms_subscriptions': receipt_subs.count(), 'receipt_sms_purchases': receipt_purchases.count(),
        'debt_sms_subscriptions': debt_subs.count(), 'debt_sms_purchases': debt_purchases.count(),
        'sms_logs': sms_logs.count(),
    }

    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        manifest = {
            'format': 'SmartPOS User Data Backup v1',
            'created_at': timezone.localtime().isoformat(),
            'timezone': settings.TIME_ZONE,
            'owner_username': owner.username,
            'pharmacy_names': [p.name for p in pharmacies],
            'record_counts': counts,
            'contains': 'Human-readable CSV exports, JSON account summary, and branch-owned chat attachments.',
            'does_not_contain': [
                'password hashes', 'OTP codes', 'login sessions', 'Django permissions',
                'Africa\'s Talking API keys', 'SMS provider credentials', 'other customers\' platform chat data',
            ],
            'note': 'This is a tenant-isolated data export. It is not a raw PostgreSQL/Django database image.',
        }
        zf.writestr('README.txt', (
            'BUSINESS MANAGER — MY DATA BACKUP\n\n'
            'This archive contains only the data belonging to the Shop Owner who created it.\n'
            'CSV files can be opened in Excel or another spreadsheet application.\n\n'
            'SECURITY: Passwords, OTPs, login sessions, API keys and platform-wide customer chat\n'
            'data are intentionally excluded. Keep this archive in a secure location.\n\n'
            'For full PostgreSQL/system recovery, the platform Superadmin should use the separate\n'
            'Full System Backup facility or managed PostgreSQL backups.\n'
        ).encode('utf-8'))
        zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))

        _write_csv(zf, 'data/pharmacies.csv',
                   ['id', 'name', 'location', 'phone', 'email', 'registration_number', 'pharmacy_council_fin', 'permit_number', 'permit_expiry_date', 'is_active', 'created_at'],
                   ((p.id, p.name, p.location, p.phone_number, p.email, p.registration_number, p.pharmacy_council_fin, p.permit_number, p.permit_expiry_date, p.is_active, p.created_at) for p in pharmacies))
        _write_csv(zf, 'data/users.csv',
                   ['id', 'username', 'first_name', 'last_name', 'phone_number', 'email', 'role', 'is_active', 'pharmacy_id', 'monthly_salary', 'morning_meal_allowance', 'afternoon_meal_allowance', 'evening_meal_allowance', 'weekend_meal_schedule', 'created_at'],
                   ((u.id, u.username, u.first_name, u.last_name, u.phone_number, u.email, u.get_role_display(), u.is_active, u.pharmacy_id, u.monthly_salary, u.morning_meal_allowance, u.afternoon_meal_allowance, u.evening_meal_allowance, u.get_weekend_meal_schedule_display(), u.created_at) for u in users))
        _write_csv(zf, 'data/weekly_meal_policy.csv',
                   ['employee_id', 'employee', 'weekday', 'day', 'breakfast', 'lunch', 'dinner', 'updated_at'],
                   ((m.employee_id, m.employee.get_full_name() or m.employee.username, m.weekday, m.get_weekday_display(), m.breakfast, m.lunch, m.dinner, m.updated_at) for m in meal_schedules))
        _write_csv(zf, 'data/meal_date_overrides.csv',
                   ['id', 'employee_id', 'employee', 'date', 'breakfast', 'lunch', 'dinner', 'reason', 'created_by_id', 'created_by', 'created_at', 'updated_at'],
                   ((m.id, m.employee_id, m.employee.get_full_name() or m.employee.username, m.date, m.breakfast, m.lunch, m.dinner, m.reason, m.created_by_id, m.created_by.get_full_name() or m.created_by.username, m.created_at, m.updated_at) for m in meal_adjustments))
        _write_csv(zf, 'data/categories.csv', ['id', 'name'], ((c.id, c.name) for c in categories))
        _write_csv(zf, 'data/products.csv',
                   ['id', 'pharmacy_id', 'name', 'category_id', 'category', 'unit', 'reorder_level', 'discount_enabled', 'min_discount_percent', 'max_discount_percent', 'barcode', 'is_active', 'created_by_id', 'created_at', 'updated_at'],
                   ((p.id, p.pharmacy_id, p.name, p.category_id, p.category.name if p.category else '', p.get_unit_display(), p.reorder_level, p.discount_enabled, p.min_discount_percent, p.max_discount_percent, p.barcode, p.is_active, p.created_by_id, p.created_at, p.updated_at) for p in products))
        _write_csv(zf, 'data/stock_imports.csv',
                   ['id', 'pharmacy_id', 'mode', 'reference', 'file_name', 'row_count', 'new_products', 'new_batches', 'topped_up', 'created_by_id', 'created_at'],
                   ((x.id, x.pharmacy_id, x.get_mode_display(), x.reference, x.file_name, x.row_count, x.new_products, x.new_batches, x.topped_up, x.created_by_id, x.created_at) for x in stock_imports))
        _write_csv(zf, 'data/product_batches.csv',
                   ['id', 'product_id', 'product', 'stock_import_id', 'batch_number', 'purchase_unit', 'purchase_quantity', 'units_per_purchase_unit', 'purchase_unit_price', 'initial_quantity', 'quantity_remaining', 'purchase_price_per_base_unit', 'selling_price', 'expiry_date', 'received_by_id', 'received_at', 'note'],
                   ((b.id, b.product_id, b.product.name, b.stock_import_id, b.batch_number, b.get_purchase_unit_display(), b.purchase_quantity, b.units_per_purchase_unit, b.purchase_unit_price, b.initial_quantity, b.quantity, b.purchase_price, b.selling_price, b.expiry_date, b.received_by_id, b.received_at, b.note) for b in product_batches))
        _write_csv(zf, 'data/stock_movements.csv',
                   ['id', 'product_id', 'product', 'batch_id', 'movement_type', 'quantity_change', 'note', 'performed_by_id', 'created_at'],
                   ((m.id, m.product_id, m.product.name, m.batch_id, m.get_movement_type_display(), m.quantity_change, m.note, m.performed_by_id, m.created_at) for m in movements))
        _write_csv(zf, 'data/sales.csv',
                   ['id', 'pharmacy_id', 'sale_number', 'receipt_number', 'served_by_id', 'payment_method', 'customer_name', 'customer_phone', 'created_at', 'synced_offline', 'needs_review', 'review_note', 'receipt_sms_sent_at', 'receipt_sms_sent_by_id'],
                   ((s.id, s.pharmacy_id, s.sale_number, f'PM-{s.pharmacy_id}-{(s.sale_number or s.id):08d}', s.served_by_id, s.get_payment_method_display(), s.customer_name, s.customer_phone, s.created_at, s.synced_offline, s.needs_review, s.review_note, s.receipt_sms_sent_at, s.receipt_sms_sent_by_id) for s in sales))
        _write_csv(zf, 'data/sale_items.csv',
                   ['id', 'sale_id', 'receipt_number', 'product_id', 'product', 'batch_id', 'quantity', 'unit_price', 'unit_cost', 'original_unit_price', 'discount_percent', 'discount_amount', 'subtotal'],
                   ((i.id, i.sale_id, f'PM-{i.sale.pharmacy_id}-{(i.sale.sale_number or i.sale_id):08d}', i.product_id, i.product.name, i.batch_id, i.quantity, i.unit_price, i.unit_cost, i.original_unit_price, i.discount_percent, i.discount_amount, i.subtotal) for i in sale_items))
        _write_csv(zf, 'data/customers.csv',
                   ['customer_name', 'customer_phone'],
                   customers)
        _write_csv(zf, 'data/debts.csv',
                   ['id', 'sale_id', 'pharmacy_id', 'customer_name', 'customer_phone', 'amount_owed', 'amount_paid', 'balance', 'is_settled', 'created_at', 'updated_at'],
                   ((d.id, d.sale_id, d.pharmacy_id, d.customer_name, d.customer_phone, d.amount_owed, d.amount_paid, d.balance, d.is_settled, d.created_at, d.updated_at) for d in debts))
        _write_csv(zf, 'data/expense_categories.csv',
                   ['id', 'pharmacy_id', 'name', 'is_active', 'is_system', 'created_at'],
                   ((c.id, c.pharmacy_id, c.name, c.is_active, c.is_system, c.created_at) for c in expense_categories))
        _write_csv(zf, 'data/expenses.csv',
                   ['id', 'pharmacy_id', 'expense_date', 'category', 'title', 'description', 'amount', 'payment_source', 'reference', 'status', 'submitted_by_id', 'submitted_at', 'decided_by_id', 'decided_at', 'decision_note', 'receipt'],
                   ((e.id, e.pharmacy_id, e.expense_date, e.category.name, e.title, e.description, e.amount, e.get_payment_source_display(), e.reference, e.get_status_display(), e.submitted_by_id, e.submitted_at, e.decided_by_id, e.decided_at, e.decision_note, e.receipt.name if e.receipt else '') for e in expenses))
        _write_csv(zf, 'data/cash_days.csv',
                   ['id', 'pharmacy_id', 'date', 'opening_cash', 'status', 'actual_closing_cash', 'physical_counted_by_id', 'physical_counted_at', 'variance_reason', 'opening_note', 'closing_note', 'opened_by_id', 'opened_at', 'closed_by_id', 'closed_at'],
                   ((d.id, d.pharmacy_id, d.date, d.opening_cash, d.get_status_display(), d.actual_closing_cash, d.physical_counted_by_id, d.physical_counted_at, d.variance_reason, d.opening_note, d.closing_note, d.opened_by_id, d.opened_at, d.closed_by_id, d.closed_at) for d in cash_days))

        _write_csv(zf, 'data/branch_addons.csv',
                   ['id', 'pharmacy_id', 'owner_id', 'monthly_price', 'status', 'payment_reference', 'requested_at', 'activated_at', 'next_billing_date', 'cancelled_at'],
                   ((a.id, a.pharmacy_id, a.owner_id, a.monthly_price, a.get_status_display(), a.payment_reference, a.requested_at, a.activated_at, a.next_billing_date, a.cancelled_at) for a in branch_addons))

        if subscription:
            _write_csv(zf, 'data/account_subscription.csv', ['owner_id', 'plan', 'status', 'start_date', 'end_date', 'amount_paid', 'created_at'],
                       [(subscription.owner_id, subscription.get_plan_display(), subscription.get_status_display(), subscription.start_date, subscription.end_date, subscription.amount_paid, subscription.created_at)])
        else:
            _write_csv(zf, 'data/account_subscription.csv', ['owner_id', 'plan', 'status', 'start_date', 'end_date', 'amount_paid', 'created_at'], [])

        _write_csv(zf, 'data/receipt_sms_subscriptions.csv',
                   ['id', 'package', 'credits_granted', 'credits_used', 'credits_remaining', 'start_date', 'end_date', 'status', 'amount_paid', 'payment_reference', 'created_at', 'updated_at'],
                   ((s.id, s.package.name, s.sms_credits, s.sms_used, s.credits_remaining, s.start_date, s.end_date, s.get_status_display(), s.amount_paid, s.payment_reference, s.created_at, s.updated_at) for s in receipt_subs))
        _write_csv(zf, 'data/receipt_sms_purchases.csv',
                   ['id', 'package', 'amount', 'payment_reference', 'status', 'customer_note', 'admin_note', 'requested_at', 'reviewed_at'],
                   ((p.id, p.package.name, p.amount, p.payment_reference, p.get_status_display(), p.customer_note, p.admin_note, p.requested_at, p.reviewed_at) for p in receipt_purchases))
        _write_csv(zf, 'data/debt_sms_subscriptions.csv',
                   ['id', 'package', 'credits_granted', 'credits_used', 'credits_remaining', 'start_date', 'end_date', 'status', 'amount_paid', 'payment_reference', 'created_at', 'updated_at'],
                   ((s.id, s.package.name, s.sms_credits, s.sms_used, s.credits_remaining, s.start_date, s.end_date, s.get_status_display(), s.amount_paid, s.payment_reference, s.created_at, s.updated_at) for s in debt_subs))
        _write_csv(zf, 'data/debt_sms_purchases.csv',
                   ['id', 'package', 'amount', 'payment_reference', 'status', 'customer_note', 'admin_note', 'requested_at', 'reviewed_at'],
                   ((p.id, p.package.name, p.amount, p.payment_reference, p.get_status_display(), p.customer_note, p.admin_note, p.requested_at, p.reviewed_at) for p in debt_purchases))
        _write_csv(zf, 'data/sms_summary.csv', ['wallet_credits_granted', 'wallet_credits_used', 'wallet_credits_remaining', 'sms_log_count'],
                   [[sms_wallet.credits_granted, sms_wallet.credits_used, sms_wallet.credits_remaining, sms_logs.count()] if sms_wallet else [0, 0, 0, sms_logs.count()]])
        _write_csv(zf, 'data/sms_logs.csv',
                   ['id', 'category', 'recipient', 'message', 'was_sent', 'error_message', 'created_at'],
                   ((l.id, l.get_category_display(), l.recipient, l.message, l.was_sent, l.error_message, l.created_at) for l in sms_logs))

        _write_csv(zf, 'data/chat_threads.csv',
                   ['id', 'pharmacy_id', 'manager_id', 'employee_id', 'created_at', 'label'],
                   ((t.id, t.pharmacy_id, t.manager_id, t.employee_id, t.created_at, t.label) for t in chat_threads))
        _write_csv(zf, 'data/chat_messages.csv',
                   ['id', 'thread_id', 'sender_id', 'sender_username', 'text', 'created_at', 'edited_at'],
                   ((m.id, m.thread_id, m.sender_id, m.sender.username, m.text, m.created_at, m.edited_at) for m in chat_messages))
        _write_csv(zf, 'data/chat_attachments.csv',
                   ['id', 'message_id', 'original_name', 'content_type', 'size', 'created_at', 'archive_path'],
                   ((a.id, a.message_id, a.original_name, a.content_type, a.size, a.created_at, f'attachments/{a.id}_{os.path.basename(a.original_name)}') for a in chat_attachments))

        # Pharmacy logos are the only business media currently attached directly
        # to a pharmacy. Chat attachments are included above and copied below.
        for p in pharmacies:
            if p.logo:
                _copy_storage_file(zf, p.logo.name, f'media/pharmacy_logos/{p.id}_{os.path.basename(p.logo.name)}')
        for a in chat_attachments:
            archive_path = f'attachments/{a.id}_{os.path.basename(a.original_name)}'
            _copy_storage_file(zf, a.file.name, archive_path)

    return filename, output.getvalue(), counts


def build_backup_zip():
    """Returns (filename, zip_bytes) — complete system backup for Superadmin/CLI."""
    timestamp = timezone.localtime().strftime('%Y%m%d_%H%M%S')

    with tempfile.TemporaryDirectory() as content_dir, tempfile.TemporaryDirectory() as output_dir:
        db_dump_path = os.path.join(content_dir, 'database.json')
        with open(db_dump_path, 'w') as f:
            management.call_command(
                'dumpdata',
                exclude=['contenttypes', 'auth.permission', 'admin.logentry', 'sessions.session'],
                indent=2,
                stdout=f,
            )

        media_dir = os.path.join(content_dir, 'media')
        def copy_storage(prefix=''):
            dirs, files = default_storage.listdir(prefix)
            for name in files:
                storage_name = f"{prefix.rstrip('/')}/{name}" if prefix else name
                destination = os.path.join(media_dir, storage_name.replace('/', os.sep))
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                with default_storage.open(storage_name, 'rb') as src, open(destination, 'wb') as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
            for name in dirs:
                storage_name = f"{prefix.rstrip('/')}/{name}" if prefix else name
                copy_storage(storage_name)
        try:
            copy_storage('')
        except (FileNotFoundError, NotImplementedError):
            pass

        archive_base = os.path.join(output_dir, f'backup_{timestamp}')
        archive_path = shutil.make_archive(archive_base, 'zip', content_dir)
        with open(archive_path, 'rb') as f:
            zip_bytes = f.read()

    return f'pharmacy_backup_{timestamp}.zip', zip_bytes
