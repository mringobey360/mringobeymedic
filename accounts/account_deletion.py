"""Permanent self-service deletion of a manager (business owner) account.

Flow
----
1. The manager opens Profile -> "Delete account", reads what will be erased and
   types the confirmation word.
2. A 6-digit code is sent by SMS to the phone number on the account.
3. Only after the correct code is entered, ``purge_owner_account`` erases the
   manager and EVERYTHING that belongs to them, in one database transaction
   (all-or-nothing): branches, products, stock, sales, debts, customers,
   expenses, cash records, payroll, meals, attendance, chat, notifications,
   employees, assistants, subscriptions, SMS wallet/logs, payment records,
   backups (including the stored files) and uploaded images.

Safety rules
------------
* Only a real business-owner (Role.ADMIN) account may delete itself. Platform
  superadmins, assistants, employees and branch-display accounts cannot.
* If a branch is also managed by ANOTHER owner it is never deleted behind that
  owner's back: the request is blocked with a clear message instead.
* The code is never shown on screen (not even if the SMS fails), only a keyed
  hash is stored, codes expire, allow few attempts and are single-use.
"""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.storage import default_storage
from django.db import connection, models, transaction
from django.db.models import F, Q
from django.db.models.deletion import PROTECT, Collector, ProtectedError
from django.utils import timezone

from sms_integration.models import SMSLog
from sms_integration.utils import send_sms

from .models import AccountDeletionRequest, ManagerAssistant

logger = logging.getLogger(__name__)
User = get_user_model()

CONFIRM_WORD = 'DELETE'
CODE_LENGTH = 6
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
MAX_CODES_PER_HOUR = 5
SESSION_CONFIRM_KEY = 'acct_delete_confirmed_at'
SESSION_CONFIRM_MAX_AGE_SECONDS = 15 * 60


class DeletionError(Exception):
    """A problem the person should be told about in plain words."""


# --------------------------------------------------------------------------- #
# Eligibility and scope
# --------------------------------------------------------------------------- #
def _pharmacy_model():
    return apps.get_model('pharmacies', 'Pharmacy')


def owned_pharmacy_ids(user):
    """Branches that belong to this owner: managed branches + paid add-on branches."""
    Pharmacy = _pharmacy_model()
    ids = set(user.managed_pharmacies.values_list('pk', flat=True))
    ids |= set(Pharmacy.objects.filter(addon__owner=user).values_list('pk', flat=True))
    return ids


def shared_branch_names(user, pharmacy_ids=None):
    """Names of owned branches that another (non-platform) manager also manages."""
    Pharmacy = _pharmacy_model()
    ids = owned_pharmacy_ids(user) if pharmacy_ids is None else pharmacy_ids
    if not ids:
        return []
    others = (User.objects.filter(role=User.Role.ADMIN, managed_pharmacies__in=ids, is_superuser=False)
              .exclude(pk=user.pk))
    return list(Pharmacy.objects.filter(pk__in=ids, managers__in=others)
                .distinct().values_list('name', flat=True))


def shared_branch_ids(user, pharmacy_ids=None):
    """Owned branches that ANOTHER owner also manages. They are never erased: this owner is
    simply detached from them, so the other owner keeps their shop and data."""
    Pharmacy = _pharmacy_model()
    ids = owned_pharmacy_ids(user) if pharmacy_ids is None else pharmacy_ids
    if not ids:
        return set()
    others = (User.objects.filter(role=User.Role.ADMIN, managed_pharmacies__in=ids, is_superuser=False)
              .exclude(pk=user.pk))
    return set(Pharmacy.objects.filter(pk__in=ids, managers__in=others).distinct().values_list('pk', flat=True))


def deletion_blocker(user, by_platform=False):
    """Return a human-readable reason why this user may not delete the account, or None.

    ``by_platform=True`` is used when the platform owner (HQ) erases a customer: no SMS
    code is involved, so a missing phone number is not a problem.
    """
    if not getattr(user, 'is_authenticated', False):
        return 'Please sign in first.'
    if user.is_superuser:
        return 'Platform administrator accounts cannot be deleted from here.'
    if user.role != User.Role.ADMIN:
        return 'Only the business owner (manager) can delete the account.'
    if not by_platform and not user.phone_number:
        return ('Your account has no phone number, so we cannot send the confirmation code. '
                'Add a phone number to your profile first.')
    return None


def platform_deletion_blocker(actor, target):
    """Why a platform operator may not start deleting ``target`` (or None)."""
    if not getattr(actor, 'is_authenticated', False) or not actor.is_superuser:
        return 'Only a platform administrator can delete customer accounts here.'
    if target.pk == actor.pk:
        return 'You cannot delete the account currently in use.'
    if target.is_superuser:
        return 'Platform administrator accounts are protected and cannot be deleted.'
    if not actor.phone_number:
        return ('Your own account has no phone number, so we cannot send you the confirmation code. '
                'Add your phone number first.')
    if target.role == User.Role.ADMIN:
        return deletion_blocker(target, by_platform=True)
    return None


def get_scope(user):
    """Everything that will be erased, as id sets."""
    owned = owned_pharmacy_ids(user)
    kept = shared_branch_ids(user, owned)      # shared with another owner: detach, never erase
    pharmacy_ids = owned - kept
    assistant_ids = set(ManagerAssistant.objects.filter(manager=user).values_list('assistant_id', flat=True))
    staff_ids = set(
        User.objects.filter(pharmacy_id__in=pharmacy_ids, is_superuser=False)
        .exclude(role=User.Role.ADMIN).exclude(pk=user.pk).values_list('pk', flat=True)
    )
    return {
        'pharmacy_ids': pharmacy_ids,
        'kept_shared_ids': kept,
        'assistant_ids': assistant_ids,
        'staff_ids': staff_ids,
        'user_ids': {user.pk} | assistant_ids | staff_ids,
    }


def deletion_summary(user):
    """Counts shown on the warning page so the owner knows what will disappear."""
    from customer_registry.models import CustomerContact
    from inventory.models import Product
    from sales.models import Sale

    scope = get_scope(user)
    ph = scope['pharmacy_ids']
    return {
        'branches': len(ph),
        'shared_kept': len(scope['kept_shared_ids']),
        'staff': len(scope['staff_ids']) + len(scope['assistant_ids']),
        'products': Product.objects.filter(pharmacy_id__in=ph).count(),
        'sales': Sale.objects.filter(pharmacy_id__in=ph).count(),
        'customers': CustomerContact.objects.filter(pharmacy_id__in=ph).count(),
    }


def mask_phone(phone):
    phone = phone or ''
    return f"{phone[:4]}\u2022\u2022\u2022\u2022{phone[-3:]}" if len(phone) >= 8 else ''


# --------------------------------------------------------------------------- #
# SMS code
# --------------------------------------------------------------------------- #
def _otp_minutes():
    return int(getattr(settings, 'OTP_VALID_MINUTES', 5))


def _hash_code(user, code, target=None):
    """Keyed hash. For a platform (HQ) deletion the code is also bound to the customer being
    deleted, so a code issued for one account can never authorise deleting another."""
    key = settings.SECRET_KEY.encode()
    label = (f'account-delete:{user.pk}:{code}' if target is None
             else f'hq-delete:{user.pk}:{target.pk}:{code}')
    return hmac.new(key, label.encode(), hashlib.sha256).hexdigest()


def issue_code(user, ip=None, target=None):
    """Create and SMS a fresh code to ``user``. Raises DeletionError (and keeps nothing) on failure.

    ``target`` is set when a platform operator deletes someone else's account: the code goes to
    the operator's own phone and is bound to that one target.
    """
    now = timezone.now()
    recent = AccountDeletionRequest.objects.filter(user=user, created_at__gte=now - timedelta(hours=1))
    if recent.count() >= MAX_CODES_PER_HOUR:
        raise DeletionError('Too many codes were requested. Please try again in an hour.')
    last = recent.order_by('-created_at').first()
    if last is not None:
        wait = RESEND_COOLDOWN_SECONDS - int((now - last.created_at).total_seconds())
        if wait > 0:
            raise DeletionError(f'Please wait {wait} seconds before requesting a new code.')

    code = ''.join(secrets.choice('0123456789') for _ in range(CODE_LENGTH))
    request_row = AccountDeletionRequest.objects.create(
        user=user, code_hash=_hash_code(user, code, target),
        expires_at=now + timedelta(minutes=_otp_minutes()), requested_ip=ip or None,
    )
    if target is None:
        message = (f"SmartPOS - Code to PERMANENTLY DELETE your account: {code}\n"
                   f"Valid for {_otp_minutes()} min.\n"
                   "Not you? Do not share it and change your password.")
    else:
        message = (f"SmartPOS HQ - Code to PERMANENTLY DELETE account @{target.username}: {code}\n"
                   f"Valid for {_otp_minutes()} min.\n"
                   "Not you? Do not share it and change your password.")
    # owner=None -> an OTP never consumes the owner's SMS wallet.
    sent = send_sms(user.phone_number, message, SMSLog.Category.OTP, owner=None)
    if not sent:
        request_row.delete()
        raise DeletionError('We could not send the SMS code right now. Please try again in a moment.')
    # Older unused codes stop working as soon as a new one is on its way.
    AccountDeletionRequest.objects.filter(user=user, consumed=False).exclude(pk=request_row.pk).update(consumed=True)
    return request_row


def verify_code(user, code, target=None):
    """Check the code. Raises DeletionError with a helpful message if it is not acceptable."""
    code = (code or '').strip()
    row = AccountDeletionRequest.objects.filter(user=user, consumed=False).order_by('-created_at').first()
    if row is None or row.expires_at < timezone.now():
        raise DeletionError('The code has expired. Please request a new one.')
    if row.attempts >= MAX_ATTEMPTS:
        raise DeletionError('Too many wrong attempts. Please request a new code.')

    # Count the attempt first so parallel guesses cannot beat the limit.
    AccountDeletionRequest.objects.filter(pk=row.pk).update(attempts=F('attempts') + 1)
    if not (code.isdigit() and len(code) == CODE_LENGTH
            and hmac.compare_digest(row.code_hash, _hash_code(user, code, target))):
        left = max(MAX_ATTEMPTS - (row.attempts + 1), 0)
        raise DeletionError(f'Incorrect code. {left} attempt(s) left.' if left else
                            'Incorrect code. Please request a new one.')
    # Single use: whoever flips consumed first wins.
    if AccountDeletionRequest.objects.filter(pk=row.pk, consumed=False).update(consumed=True) != 1:
        raise DeletionError('This code was already used. Please request a new one.')
    return True


# --------------------------------------------------------------------------- #
# The purge
# --------------------------------------------------------------------------- #
def _delete_known_protected_chains(pharmacy_ids):
    """Delete, children first, the rows that PROTECT other rows inside the owner's own branches.

    Django refuses to delete e.g. a Product while a SaleItem still points at it with
    on_delete=PROTECT, even if that SaleItem is deleted in the same operation. So the
    protecting rows are removed explicitly and in dependency order. Deleting through
    querysets (never instance.delete) is deliberate: it is the only way to erase the
    append-only cash audit tables, and only for the account being erased.
    """
    from expenses.models import (CashAuditEvent, CashClosingSnapshot, CashControlReset, CashCount,
                                 CashDay, CashDayReopenAudit, Expense)
    from inventory.models import BranchStockTransfer
    from sales.models import SaleItem

    ph = pharmacy_ids
    if not ph:
        return
    # Sales lines point at products and batches (PROTECT).
    SaleItem.objects.filter(Q(sale__pharmacy_id__in=ph) | Q(product__pharmacy_id__in=ph)).delete()
    # Stock transfers point at batches and at both branches (PROTECT); their items cascade.
    BranchStockTransfer.objects.filter(Q(source_pharmacy_id__in=ph) | Q(destination_pharmacy_id__in=ph)).delete()
    # Expenses point at their category (PROTECT).
    Expense.objects.filter(pharmacy_id__in=ph).delete()
    # Cash control: break the self-references, then remove what protects each cash day.
    CashCount.objects.filter(cash_day__pharmacy_id__in=ph).update(supersedes=None)
    CashDay.objects.filter(pharmacy_id__in=ph).update(carried_from=None)
    CashDayReopenAudit.objects.filter(cash_day__pharmacy_id__in=ph).delete()
    CashClosingSnapshot.objects.filter(cash_day__pharmacy_id__in=ph).delete()
    CashAuditEvent.objects.filter(Q(pharmacy_id__in=ph) | Q(cash_day__pharmacy_id__in=ph)).delete()
    CashControlReset.objects.filter(pharmacy_id__in=ph).delete()


def _delete_protected_objects(objs, depth=0):
    """Safety net: remove the exact rows Django reports as blocking a deletion."""
    if depth > 10:
        raise DeletionError('Some records are linked in a way that could not be resolved automatically.')
    by_model = {}
    for obj in objs:
        by_model.setdefault(obj.__class__, set()).add(obj.pk)
    for model, pks in by_model.items():
        for _ in range(50):
            try:
                model._default_manager.filter(pk__in=pks).delete()
                break
            except ProtectedError as inner:
                _delete_protected_objects(inner.protected_objects, depth + 1)
        else:
            raise DeletionError('Some records are linked in a way that could not be resolved automatically.')


def protected_model_names(exc):
    return sorted({o.__class__.__name__ for o in getattr(exc, 'protected_objects', [])})


def _protect_referrers(target_model):
    """(model, field_name) for every FK with on_delete=PROTECT that points at target_model."""
    for model in apps.get_models():
        for field in model._meta.get_fields():
            if (field.is_relation and field.concrete and (field.many_to_one or field.one_to_one)
                    and field.related_model is target_model
                    and getattr(field.remote_field, 'on_delete', None) is PROTECT):
                yield model, field.name


def _delete_protecting_rows(user_ids, pharmacy_ids):
    """Remove rows that would block deletion through on_delete=PROTECT.

    Django refuses to delete a user/branch while PROTECT rows still point at it,
    even when those rows are about to be removed anyway. They all belong to the
    owner's own scope, so they are deleted first, found automatically from the
    model definitions so future models are covered too.
    """
    Pharmacy = _pharmacy_model()
    pending = [(m, n, user_ids) for m, n in _protect_referrers(User)]
    pending += [(m, n, pharmacy_ids) for m, n in _protect_referrers(Pharmacy)]
    last_error = None
    while pending:
        remaining = []
        for model, name, ids in pending:
            try:
                model._default_manager.filter(**{f'{name}__in': ids}).delete()
            except ProtectedError as exc:  # blocked by another PROTECT row: retry after the others
                last_error = exc
                remaining.append((model, name, ids))
        if len(remaining) == len(pending):
            raise last_error
        pending = remaining


def _file_refs(collector):
    """(storage, name) for every uploaded file on rows the collector is about to delete."""
    refs = []

    def file_fields(model):
        return [f for f in model._meta.concrete_fields if isinstance(f, models.FileField)]

    try:
        for model, instances in collector.data.items():
            fields = file_fields(model)
            for obj in (instances if fields else ()):
                for f in fields:
                    name = getattr(getattr(obj, f.attname, None), 'name', None)
                    if name:
                        refs.append((f.storage, name))
        for fast in collector.fast_deletes:
            querysets = fast if isinstance(fast, (list, tuple)) else [fast]
            for qs in querysets:
                fields = file_fields(qs.model)
                if not fields:
                    continue
                for row in qs.values_list(*[f.attname for f in fields]):
                    for f, name in zip(fields, row):
                        if name:
                            refs.append((f.storage, name))
    except Exception:  # never let file bookkeeping block the erasure itself
        logger.exception('Could not list every uploaded file for removal.')
    return refs


def _delete_instances(queryset, file_refs):
    for _ in range(25):
        collector = Collector(using=queryset.db)
        try:
            collector.collect(list(queryset))
        except ProtectedError as exc:
            logger.warning('Removing protecting rows before erasing: %s', protected_model_names(exc))
            _delete_protected_objects(exc.protected_objects)
            continue
        file_refs.extend(_file_refs(collector))
        collector.delete()
        return
    raise DeletionError('Some records are linked in a way that could not be resolved automatically.')


def _purge_sessions_later(user_ids):
    """Drop stored browser sessions of the erased accounts (so no login traces remain)."""
    ids = {str(i) for i in user_ids}

    def _run():
        try:
            from django.contrib.sessions.models import Session
            stale = []
            for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator(chunk_size=500):
                try:
                    if str(session.get_decoded().get('_auth_user_id')) in ids:
                        stale.append(session.pk)
                except Exception:
                    continue
            if stale:
                Session.objects.filter(pk__in=stale).delete()
        except Exception:
            logger.warning('Could not clear browser sessions after account deletion.')
    transaction.on_commit(_run)


def _remove_files_later(file_refs, extra_paths):
    def _run():
        seen = set()
        for storage, name in file_refs:
            if (id(storage), name) in seen:
                continue
            seen.add((id(storage), name))
            try:
                storage.delete(name)
            except Exception:
                logger.warning('Could not remove file %s after account deletion.', name)
        for path in extra_paths:
            try:
                default_storage.delete(path)
            except Exception:
                logger.warning('Could not remove backup file %s after account deletion.', path)
    transaction.on_commit(_run)


# --------------------------------------------------------------------------- #
# Database-level references Django does not know about (schema drift)
# --------------------------------------------------------------------------- #
def _modeled_fk_columns():
    """(table, column) of every foreign key that a Django model declares."""
    known = set()
    for model in apps.get_models(include_auto_created=True):
        for field in model._meta.concrete_fields:
            if field.is_relation and (field.many_to_one or field.one_to_one):
                known.add((model._meta.db_table, field.column))
    return known


def _clear_unmodeled_references(target_model, ids):
    """Remove rows that point at the doomed rows through a database foreign key that NO model
    declares any more.

    Production databases can keep a column + constraint from an old migration after the model
    field was removed (for example ``dashboard_dashboardpreference.pharmacy_id``). Django's
    cascade cannot see it, so the final COMMIT failed with a ForeignKeyViolation and nothing was
    deleted. This inspects the real database, and for each such hidden reference deletes the row
    (or, for a nullable link to a user, detaches it) so the erasure can complete.
    """
    ids = sorted(i for i in (ids or ()) if i is not None)
    if not ids or connection.vendor != 'postgresql':
        return 0
    table = target_model._meta.db_table
    pk_col = target_model._meta.pk.column
    known = _modeled_fk_columns()
    quote = connection.ops.quote_name
    cleared = 0
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT cl.relname, att.attname, att.attnotnull, fatt.attname
            FROM pg_constraint con
            JOIN pg_class cl   ON cl.oid  = con.conrelid
            JOIN pg_class ref  ON ref.oid = con.confrelid
            JOIN pg_attribute att  ON att.attrelid  = con.conrelid  AND att.attnum  = con.conkey[1]
            JOIN pg_attribute fatt ON fatt.attrelid = con.confrelid AND fatt.attnum = con.confkey[1]
            WHERE con.contype = 'f' AND array_length(con.conkey, 1) = 1
              AND ref.relname = %s AND pg_table_is_visible(ref.oid)
            """, [table])
        for ref_table, ref_col, not_null, target_col in cursor.fetchall():
            if (ref_table, ref_col) in known:
                continue
            value_sql = f'{quote(target_col)}' if target_col != pk_col else quote(pk_col)
            where = f'{quote(ref_col)} IN (SELECT {value_sql} FROM {quote(table)} WHERE {quote(pk_col)} = ANY(%s))'
            if table == User._meta.db_table and not not_null:
                cursor.execute(f'UPDATE {quote(ref_table)} SET {quote(ref_col)} = NULL WHERE {where}', [ids])
            else:
                cursor.execute(f'DELETE FROM {quote(ref_table)} WHERE {where}', [ids])
            cleared += cursor.rowcount or 0
            logger.warning('Account erasure: cleared %s row(s) of %s.%s that referenced %s outside the models.',
                           cursor.rowcount, ref_table, ref_col, table)
    return cleared


def _delete_marketplace_leftovers(pharmacy_ids, file_refs):
    """Marketplace rows only point at the store with SET_NULL, so a plain cascade would leave
    the shop's products, buyer orders (names, phones, addresses), payment attempts and
    downloadable files behind. Erase them explicitly, children first."""
    from django.apps import apps as _apps
    try:
        SellerStore = _apps.get_model('marketplace', 'SellerStore')
        Order = _apps.get_model('marketplace', 'Order')
        Product = _apps.get_model('marketplace', 'Product')
        PaymentAttempt = _apps.get_model('marketplace', 'PaymentAttempt')
    except LookupError:
        return
    store_ids = list(SellerStore.objects.filter(pharmacy_id__in=pharmacy_ids).values_list('pk', flat=True))
    if not store_ids:
        return
    # Orders (items, events, reviews, download grants cascade) before the products they PROTECT.
    _delete_instances(Order.objects.filter(seller_store_id__in=store_ids), file_refs)
    PaymentAttempt.objects.filter(seller_store_id__in=store_ids).delete()
    _delete_instances(Product.objects.filter(seller_store_id__in=store_ids), file_refs)


def _delete_log_leftovers(user_ids, pharmacy_ids):
    """Audit/log tables that keep a copy of names, usernames or IPs with SET_NULL links."""
    try:
        from customer_registry.models import CustomerDataAccessLog
        CustomerDataAccessLog.objects.filter(Q(user_id__in=user_ids) | Q(pharmacy_id__in=pharmacy_ids)).delete()
    except Exception:
        logger.exception('Could not clear customer data access logs during account erasure.')
    try:
        from accounts.models import PinSecurityAudit, DeviceSessionAudit
        PinSecurityAudit.objects.filter(Q(user_id__in=user_ids) | Q(actor_id__in=user_ids)).delete()
        DeviceSessionAudit.objects.filter(Q(user_id__in=user_ids) | Q(actor_id__in=user_ids)).delete()
    except Exception:
        logger.exception('Could not clear security audit rows during account erasure.')


def purge_staff_account(user):
    """Permanently erase ONE non-owner account (employee / assistant / display) cleanly.

    Used by HQ. Clears the rows that PROTECT the user so the delete never fails with a 500.
    """
    if user.is_superuser:
        raise DeletionError('Platform administrator accounts cannot be deleted.')
    if user.role == User.Role.ADMIN:
        raise DeletionError('This is a business owner account. Use the full account deletion.')
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        file_refs = []
        _delete_protecting_rows({user.pk}, set())
        _delete_log_leftovers({user.pk}, set())
        _clear_unmodeled_references(User, {user.pk})
        _delete_instances(User.objects.filter(pk=user.pk), file_refs)
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        _remove_files_later(file_refs, [])
        _purge_sessions_later({user.pk})


def purge_owner_account(user, by_platform=False):
    """Permanently erase the owner and everything that belongs to them. Returns counts."""
    from core.models import UserDataBackup
    from hq.models import ImpersonationSession, SignupRequest, Subscription
    from daily_reports.models import DailyReportDeliveryLog
    from payments.models import PesapalTransaction
    from pharmacies.models import BranchAddOn
    from sms_integration.models import SMSAnnouncement, SMSAnnouncementRecipient

    Pharmacy = _pharmacy_model()
    with transaction.atomic():
        # Lock the row so a double-click cannot run the purge twice.
        user = User.objects.select_for_update().get(pk=user.pk)
        blocker = deletion_blocker(user, by_platform=by_platform)
        if blocker:
            raise DeletionError(blocker)

        scope = get_scope(user)
        user_ids, pharmacy_ids = scope['user_ids'], scope['pharmacy_ids']
        phones = [p for p in User.objects.filter(pk__in=user_ids).values_list('phone_number', flat=True) if p]
        file_refs, extra_paths = [], []

        # Backups are stored by path (not a FileField): remove those files too.
        extra_paths = [b for b in UserDataBackup.objects.filter(owner=user).values_list('storage_path', flat=True) if b]

        # Records that reference these accounts but are not removed by cascade.
        signup_ids = list(SignupRequest.objects.filter(
            Q(created_owner_id__in=user_ids) | Q(created_pharmacy_id__in=pharmacy_ids)
            | Q(phone_number__in=phones)).values_list('pk', flat=True))
        subscription_ids = list(Subscription.objects.filter(owner_id__in=user_ids).values_list('pk', flat=True))
        addon_ids = list(BranchAddOn.objects.filter(
            Q(owner_id__in=user_ids) | Q(pharmacy_id__in=pharmacy_ids)).values_list('pk', flat=True))

        payment_q = Q(owner_id__in=user_ids)
        for model, ids in ((SignupRequest, signup_ids), (Subscription, subscription_ids), (BranchAddOn, addon_ids)):
            if ids:
                payment_q |= Q(content_type=ContentType.objects.get_for_model(model), object_id__in=ids)
        PesapalTransaction.objects.filter(payment_q).delete()

        SignupRequest.objects.filter(pk__in=signup_ids).delete()
        SMSLog.objects.filter(owner_id__in=user_ids).delete()
        SMSLog.objects.filter(recipient__in=phones,
                              category__in=[SMSLog.Category.OTP, SMSLog.Category.CREDENTIALS]).delete()
        SMSAnnouncementRecipient.objects.filter(owner_id__in=user_ids).delete()
        SMSAnnouncement.objects.filter(created_by_id__in=user_ids).delete()
        DailyReportDeliveryLog.objects.filter(owner_id__in=user_ids).delete()
        ImpersonationSession.objects.filter(target_id__in=user_ids).delete()

        _delete_marketplace_leftovers(pharmacy_ids, file_refs)
        _delete_log_leftovers(user_ids, pharmacy_ids)

        # Rows that PROTECT users/branches, then the branches (cascades to all
        # stock, sales, debts, customers, expenses, payroll, chat...), then the people.
        _delete_known_protected_chains(pharmacy_ids)
        _delete_protecting_rows(user_ids, pharmacy_ids)
        _clear_unmodeled_references(Pharmacy, pharmacy_ids)
        _clear_unmodeled_references(User, user_ids)
        _delete_instances(Pharmacy.objects.filter(pk__in=pharmacy_ids), file_refs)
        _delete_instances(User.objects.filter(pk__in=user_ids), file_refs)

        # Check every deferred foreign key NOW, inside the transaction, so a leftover reference
        # is reported clearly (and rolled back) instead of failing at COMMIT.
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        _remove_files_later(file_refs, extra_paths)
        _purge_sessions_later(user_ids)

    return {
        'branches': len(pharmacy_ids),
        'staff': len(scope['staff_ids']) + len(scope['assistant_ids']),
    }
