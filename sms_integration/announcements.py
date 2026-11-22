"""
Platform announcement SMS (system owner -> customers).

Design notes
------------
* Paid by the platform: uses the global SMSSettings credentials and never
  touches a customer's SMS wallet or Premium SMS balance.
* One SMSAnnouncementRecipient row is created per customer BEFORE anything is
  sent. That row is the audit trail and also makes the campaign resumable: if
  a worker restarts mid-send, the remaining PENDING rows can be sent later.
* Sending runs in a background thread so the request returns immediately
  (a few hundred customers would otherwise hit the gunicorn request timeout).
  Identical messages are sent in provider batches of up to 100 numbers;
  personalised messages ({name}) are sent one by one.
"""
import logging
import re
import threading
from datetime import timedelta

from django.db import close_old_connections, connection
from django.db.models import Count, Q, Sum
from django.utils import timezone

from accounts.models import User
from accounts.phone_utils import normalize_phone_number
from .models import SMSAnnouncement, SMSAnnouncementRecipient, SMSLog, SMSSettings
from .utils import sms_character_count, sms_segment_count

logger = logging.getLogger(__name__)

MAX_TEMPLATE_CHARS = 400
BATCH_SIZE = 100
STALE_AFTER = timedelta(minutes=5)
# Africa's Talking: 100 Processed, 101 Sent, 102 Queued.
PROVIDER_OK_CODES = {100, 101, 102}
ALLOWED_PLACEHOLDERS = {'name', 'link'}

# Official platform signature. Keep this strictly GSM-7 compatible so the
# administrator footer does not unexpectedly force the whole SMS into Unicode.
ADMINISTRATOR_FOOTER = 'SmartPOS Team'
# Previous signature(s); retry_unsent() upgrades unsent rows that still carry one.
LEGACY_FOOTERS = ('SmartPOS | Administrator',)

_Status = SMSAnnouncement.Status
_Delivery = SMSAnnouncementRecipient.DeliveryStatus


# ------------------------------------------------------------------ audience
def customers_queryset():
    return User.objects.filter(role=User.Role.ADMIN, is_superuser=False, is_active=True)


def resolve_audience(audience, selected_ids=None):
    """Return the customer (shop owner) queryset for the chosen audience."""
    today = timezone.localdate()
    qs = customers_queryset()
    A = SMSAnnouncement.Audience
    if audience == A.ACTIVE:
        qs = qs.filter(subscription__status='ACTIVE', subscription__end_date__gte=today)
    elif audience == A.EXPIRING:
        qs = qs.filter(subscription__status='ACTIVE',
                       subscription__end_date__gte=today,
                       subscription__end_date__lte=today + timedelta(days=7))
    elif audience == A.EXPIRED:
        qs = qs.filter(Q(subscription__status='EXPIRED') | Q(subscription__end_date__lt=today))
    elif audience == A.SELECTED:
        qs = qs.filter(pk__in=list(selected_ids or []))
    return qs.order_by('first_name', 'last_name', 'username')


def assistants_for_owners(owners):
    """Active assistant managers (User rows) delegated by the given shop owners (queryset or list of users)."""
    owner_ids = [getattr(o, 'pk', o) for o in owners]
    return User.objects.filter(
        role=User.Role.ASSISTANT, is_active=True,
        assistant_profile__active=True, assistant_profile__manager_id__in=owner_ids,
    ).order_by('first_name', 'last_name', 'username')


def resolve_delivery_users(audience, selected_ids=None, include_assistants=True):
    """Return (customers, assistants) for a campaign.

    * Customer audiences: the matching shop owners, plus (optionally) their active assistant managers.
    * ASSISTANTS audience: only the assistant managers of every active customer account.
    """
    A = SMSAnnouncement.Audience
    if audience == A.ASSISTANTS:
        owners = customers_queryset()
        return [], list(assistants_for_owners(owners.values_list('pk', flat=True)))
    owners = list(resolve_audience(audience, selected_ids))
    assistants = list(assistants_for_owners(owners)) if include_assistants else []
    return owners, assistants


def audience_counts():
    """Recipient counts (with a phone number) for the compose screen, split into owners and assistants."""
    has_phone = ~Q(phone_number='') & Q(phone_number__isnull=False)
    A = SMSAnnouncement.Audience
    customers, assistants = {}, {}
    for a in (A.ACTIVE, A.ALL, A.EXPIRING, A.EXPIRED):
        owners = resolve_audience(a)
        customers[a] = owners.filter(has_phone).count()
        assistants[a] = assistants_for_owners(owners.values_list('pk', flat=True)).filter(has_phone).count()
    customers[A.ASSISTANTS] = 0
    assistants[A.ASSISTANTS] = assistants_for_owners(customers_queryset().values_list('pk', flat=True)).filter(has_phone).count()
    return {'customers': customers, 'assistants': assistants}


# ------------------------------------------------------------------- content
def find_unsupported_placeholders(template):
    found = set(re.findall(r'\{([^{}]*)\}', template or ''))
    return sorted(found - ALLOWED_PLACEHOLDERS)


def first_name_of(user):
    return (user.first_name or user.get_full_name() or user.username or '').strip().split(' ')[0]


def build_announcement_link(base_url, token):
    """Legacy tracked redirect {base_url}/sms/o/<token>/ — no longer used for new messages
    (the SMS now carries the administrator's link exactly as typed); kept for old campaigns."""
    from django.urls import reverse
    path = reverse('sms_integration:announcement_open', kwargs={'token': token})
    return f"{(base_url or '').rstrip('/')}{path}"


def render_message(template, user, link_url='', token=None, base_url=''):
    """Render only the personalised announcement body.

    Title and administrator signature are deliberately composed separately so the
    same final-message rules can be used by SMS, previews, retries and bell
    notifications without duplicating formatting logic.
    """
    name = first_name_of(user) or 'Mteja'
    text = (template or '').replace('{name}', name)
    if '{link}' in text:
        # {link} is replaced with the destination exactly as the administrator typed it.
        # (A token/base_url pair still renders the legacy tracked redirect, but build_recipients no longer passes them.)
        if link_url and token and base_url:
            replacement = build_announcement_link(base_url, token)
        else:
            replacement = link_url or ''
        text = text.replace('{link}', replacement)
    return text.strip()


def compose_customer_message(title, body, rendered_body, include_administrator_footer=True):
    """Build the exact customer-facing announcement text.

    Layout is intentionally compact and professional:
        Title
        Message
        SmartPOS Team

    There is exactly one newline between sections; no blank line is inserted
    before the footer. Empty sections are ignored defensively.
    """
    parts = []
    title = (title or '').strip()
    rendered_body = (rendered_body if rendered_body is not None else body or '').strip()
    if title:
        parts.append(title)
    if rendered_body:
        parts.append(rendered_body)
    if include_administrator_footer:
        parts.append(ADMINISTRATOR_FOOTER)
    return '\n'.join(parts).strip()


def render_customer_message(announcement, user, link_url='', token=None, base_url='', include_administrator_footer=True):
    """Render the complete customer-facing announcement."""
    body = render_message(announcement.message, user, link_url, token, base_url)
    return compose_customer_message(
        announcement.title,
        announcement.message,
        body,
        include_administrator_footer=include_administrator_footer,
    )


# ---------------------------------------------------------------- preparation
def build_recipients(announcement, customers, base_url=''):
    """
    Create one recipient row per customer. Bad/duplicate phone numbers are stored
    as SKIPPED (with the reason) so the report accounts for every customer.

    base_url (e.g. request.build_absolute_uri('/')) is only needed when the message
    uses {link} — each row already has its own access_token (set at instantiation),
    so the personal tracked link can be rendered into `message` before it is ever sent.
    """
    rows, seen = [], set()
    for user in customers:
        name = user.get_full_name() or user.username
        if user.role == User.Role.ASSISTANT:
            name = f'{name} (Assistant)'
        raw = (user.phone_number or '').strip()
        if not raw:
            rows.append(SMSAnnouncementRecipient(
                announcement=announcement, owner=user, display_name=name,
                status=_Delivery.SKIPPED, error_message='No phone number on the account.'))
            continue
        try:
            phone = normalize_phone_number(raw)
        except Exception as exc:  # forms.ValidationError
            msg = ' '.join(getattr(exc, 'messages', [str(exc)]))
            rows.append(SMSAnnouncementRecipient(
                announcement=announcement, owner=user, display_name=name, phone_number=raw[:20],
                status=_Delivery.SKIPPED, error_message=f'Invalid phone number: {msg}'[:300]))
            continue
        if phone in seen:
            rows.append(SMSAnnouncementRecipient(
                announcement=announcement, owner=user, display_name=name, phone_number=phone,
                status=_Delivery.SKIPPED, error_message='Duplicate phone number in this campaign.'))
            continue
        seen.add(phone)
        row = SMSAnnouncementRecipient(
            announcement=announcement, owner=user, display_name=name, phone_number=phone,
            status=_Delivery.PENDING)
        # Deliberately NOT passing token/base_url: the SMS carries the link exactly as the administrator typed it.
        text = render_customer_message(announcement, user, announcement.link_url, include_administrator_footer=True)
        row.message = text
        row.segments = sms_segment_count(text)
        rows.append(row)
    SMSAnnouncementRecipient.objects.bulk_create(rows)
    refresh_counters(announcement, save=True, total=len(rows))
    return len(rows)


def refresh_counters(announcement, save=True, total=None):
    counts = dict(
        announcement.recipients.values_list('status').annotate(c=Count('id')).order_by()
    )
    announcement.sent_count = counts.get(_Delivery.SENT, 0)
    announcement.failed_count = counts.get(_Delivery.FAILED, 0)
    announcement.skipped_count = counts.get(_Delivery.SKIPPED, 0)
    if total is not None:
        announcement.total_recipients = total
    announcement.total_segments = announcement.recipients.filter(
        status=_Delivery.SENT).aggregate(s=Sum('segments'))['s'] or 0
    if save:
        announcement.save(update_fields=[
            'sent_count', 'failed_count', 'skipped_count', 'total_recipients', 'total_segments'])


# ------------------------------------------------------------------- sending
def provider_ready():
    """(ok, reason) — checks the platform SMS switch and credentials before we queue anything."""
    cfg = SMSSettings.load()
    if not cfg.is_enabled:
        return False, 'SMS is switched off globally in SMS Settings (Django Admin).'
    if not cfg.at_username or not cfg.at_api_key or cfg.at_api_key == 'CHANGE-ME':
        return False, "Africa's Talking credentials are not configured in SMS Settings (Django Admin)."
    return True, ''


def _digits(value):
    return re.sub(r'\D', '', str(value or ''))


def _send_batch(sms_service, message, phones, sender_id):
    """Send one message to up to BATCH_SIZE numbers; returns {phone: (ok, provider_status, error)}."""
    kwargs = {'sender_id': sender_id} if sender_id else {}
    try:
        response = sms_service.send(message, phones, **kwargs)
    except Exception as exc:
        logger.exception('Announcement SMS batch failed')
        return {p: (False, 'ERROR', str(exc)[:300]) for p in phones}

    data = (response or {}).get('SMSMessageData') or {}
    reported = {}
    for item in data.get('Recipients') or []:
        try:
            ok = int(item.get('statusCode')) in PROVIDER_OK_CODES
        except (TypeError, ValueError):
            ok = False
        status_text = str(item.get('status') or item.get('statusCode') or '')[:40]
        reported[_digits(item.get('number'))] = (ok, status_text, '' if ok else (status_text or 'Rejected by provider'))

    result = {}
    for phone in phones:
        result[phone] = reported.get(_digits(phone)) or (
            False, 'NO_RESPONSE', (data.get('Message') or 'Provider did not confirm this number.')[:300])
    return result


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _run_send(announcement_id):
    close_old_connections()
    try:
        # Atomic claim: only one worker/thread may send a given campaign at a time.
        claimed = SMSAnnouncement.objects.filter(
            pk=announcement_id, status__in=[_Status.QUEUED, _Status.PARTIAL, _Status.FAILED],
        ).update(status=_Status.SENDING, last_activity_at=timezone.now(), completed_at=None)
        if not claimed:
            return
        announcement = SMSAnnouncement.objects.get(pk=announcement_id)

        cfg = SMSSettings.load()
        import africastalking
        africastalking.initialize(cfg.at_username, cfg.at_api_key)
        sms_service = africastalking.SMS

        pending = list(announcement.recipients.filter(status=_Delivery.PENDING).order_by('id'))
        by_message = {}
        for row in pending:
            by_message.setdefault(row.message, []).append(row)

        for message, rows in by_message.items():
            for chunk in _chunks(rows, BATCH_SIZE):
                outcome = _send_batch(sms_service, message, [r.phone_number for r in chunk], cfg.sender_id)
                now = timezone.now()
                logs = []
                for row in chunk:
                    ok, provider_status, error = outcome[row.phone_number]
                    row.status = _Delivery.SENT if ok else _Delivery.FAILED
                    row.provider_status = provider_status
                    row.error_message = error
                    row.sent_at = now if ok else None
                    logs.append(SMSLog(
                        category=SMSLog.Category.ANNOUNCEMENT, recipient=row.phone_number, message=message,
                        was_sent=ok, error_message=error, owner=None,
                        character_count=sms_character_count(message), sms_segments=row.segments))
                SMSAnnouncementRecipient.objects.bulk_update(
                    chunk, ['status', 'provider_status', 'error_message', 'sent_at'])
                if announcement.audience == SMSAnnouncement.Audience.PROSPECTS:
                    from .prospects import mark_prospects_contacted
                    mark_prospects_contacted([r for r in chunk if r.status == _Delivery.SENT], now)
                SMSLog.objects.bulk_create(logs)
                refresh_counters(announcement, save=True)
                SMSAnnouncement.objects.filter(pk=announcement_id).update(last_activity_at=timezone.now())

        _finalize(announcement_id)
    except Exception:
        logger.exception('Announcement %s crashed while sending', announcement_id)
        try:
            _finalize(announcement_id, crashed=True)
        except Exception:
            logger.exception('Could not finalise announcement %s', announcement_id)
    finally:
        connection.close()


def _finalize(announcement_id, crashed=False):
    announcement = SMSAnnouncement.objects.get(pk=announcement_id)
    refresh_counters(announcement, save=False)
    if announcement.sent_count and not announcement.failed_count and not (crashed and announcement.pending_count):
        status = _Status.COMPLETED
    elif announcement.sent_count:
        status = _Status.PARTIAL
    else:
        status = _Status.FAILED
    announcement.status = status
    announcement.completed_at = timezone.now()
    announcement.save(update_fields=[
        'status', 'completed_at', 'sent_count', 'failed_count', 'skipped_count', 'total_segments'])


def start_sending(announcement_id):
    """Fire-and-forget background send (safe to call again to resume/retry)."""
    thread = threading.Thread(target=_run_send, args=(announcement_id,), name=f'sms-announcement-{announcement_id}', daemon=True)
    thread.start()
    return thread


def retry_unsent(announcement):
    """Re-queue failed rows (and anything left pending) and send again. Returns rows re-queued."""
    if announcement.status == _Status.SENDING and not is_stale(announcement):
        return 0
    if announcement.status == _Status.SENDING:  # stale: release the claim first
        SMSAnnouncement.objects.filter(pk=announcement.pk, status=_Status.SENDING).update(status=_Status.PARTIAL)
    # Bring old pending/failed recipient rows up to the current official
    # title/signature standard before they are sent again. New rows already
    # contain the complete final message, so this is idempotent for them.
    legacy_rows = list(announcement.recipients.filter(status__in=[_Delivery.FAILED, _Delivery.PENDING]).order_by('id'))
    changed = []
    for row in legacy_rows:
        original = (row.message or '').strip()
        current = original
        for legacy in LEGACY_FOOTERS:  # swap the old signature rather than appending a second one
            if current.endswith(legacy):
                current = current[:-len(legacy)].rstrip() + '\n' + ADMINISTRATOR_FOOTER
        if not current.startswith((announcement.title or '').strip()) or not current.endswith(ADMINISTRATOR_FOOTER):
            current = compose_customer_message(announcement.title, '', current, include_administrator_footer=True)
        if current != original:
            row.message = current
            row.segments = sms_segment_count(current)
            changed.append(row)
    if changed:
        SMSAnnouncementRecipient.objects.bulk_update(changed, ['message', 'segments'])

    requeued = announcement.recipients.filter(status=_Delivery.FAILED).update(
        status=_Delivery.PENDING, provider_status='', error_message='')
    remaining = announcement.recipients.filter(status=_Delivery.PENDING).count()
    if remaining:
        SMSAnnouncement.objects.filter(pk=announcement.pk).exclude(status=_Status.SENDING).update(status=_Status.PARTIAL)
        start_sending(announcement.pk)
    return max(requeued, remaining)


def is_stale(announcement):
    """A SENDING campaign with no activity for a while lost its worker (restart/deploy)."""
    if announcement.status not in (_Status.QUEUED, _Status.SENDING):
        return False
    marker = announcement.last_activity_at or announcement.created_at
    return timezone.now() - marker > STALE_AFTER


def release_if_stale(announcement):
    """Mark an orphaned campaign as PARTIAL so the owner can resume it with one click."""
    if not is_stale(announcement):
        return announcement
    refresh_counters(announcement, save=False)
    announcement.status = _Status.PARTIAL if announcement.sent_count else _Status.FAILED
    announcement.completed_at = timezone.now()
    announcement.save(update_fields=[
        'status', 'completed_at', 'sent_count', 'failed_count', 'skipped_count', 'total_segments'])
    return announcement
