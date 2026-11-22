"""Owner SMS alerts for employee attendance.

Design goals ("professional and wise"):

* Opt-in per branch (default OFF) and a global master switch in SMS Settings.
* ``EXCEPTIONS`` mode (recommended) only texts the owner when something needs
  attention: a late check-in, an early check-out, or a manual-approval request.
  ``ALL`` mode texts every check-in and check-out.
* One SMS per employee per event per day. ``check_in_sms_sent_at`` /
  ``check_out_sms_sent_at`` make retries, double scans and re-opened workdays
  harmless.
* Sent in the background (Celery) after the database commit, so a slow SMS
  gateway can never delay or break an employee's check-in.
* Structured, plain-ASCII Swahili text (header, who, what, time, status, branch) that
  always fits in ONE SMS segment (160 characters), so each alert costs exactly one credit.
* v97.18: a workday-reopen request also alerts the owner (it needs a decision), and a
  re-opened workday reports the employee's FINAL check-out as well.

Everything that needs Django is imported lazily, which keeps the message
builders importable (and unit-testable) without a configured project.
"""
import logging
import unicodedata
from datetime import datetime

logger = logging.getLogger(__name__)

MODE_OFF = 'OFF'
MODE_EXCEPTIONS = 'EXCEPTIONS'
MODE_ALL = 'ALL'

KIND_IN = 'IN'
KIND_OUT = 'OUT'
KIND_EXCEPTION = 'EXC'
KIND_REOPEN = 'REO'

SMS_LIMIT = 160
HEADER = 'SMARTPOS - MAHUDHURIO'
PREFIX = HEADER + '\n'  # kept for backwards compatibility with older imports


# --------------------------------------------------------------------------
# Pure helpers (no Django)
# --------------------------------------------------------------------------
def ascii_text(value, limit):
    """Fold accents, drop anything outside ASCII and cap the length.

    ASCII keeps the SMS in the GSM-7 alphabet (160 chars per segment instead of 70).
    """
    folded = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode('ascii')
    folded = ' '.join(folded.split())
    return folded[:limit].rstrip()


def _hhmm(value):
    return value.strftime('%H:%M')


def _worked_text(seconds):
    """'saa 9 na dakika 13', 'saa 8' or 'dakika 45'."""
    minutes = max(int(seconds // 60), 0)
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f'saa {hours} na dakika {mins}'
    if hours:
        return f'saa {hours}'
    return f'dakika {mins}'


def late_minutes_for(local_check_in, work_start):
    """Whole minutes after the official start time (negative/zero = on time)."""
    start = datetime.combine(local_check_in.date(), work_start, tzinfo=local_check_in.tzinfo)
    return int((local_check_in - start).total_seconds() // 60)


def early_minutes_for(local_check_out, work_end):
    """Whole minutes before the official closing time (negative/zero = not early)."""
    end = datetime.combine(local_check_out.date(), work_end, tzinfo=local_check_out.tzinfo)
    return int((end - local_check_out).total_seconds() // 60)


def should_send(mode, kind, is_late=False, is_early=False):
    """Decide whether this event deserves an SMS in the branch's chosen mode."""
    if mode == MODE_ALL:
        return kind in (KIND_IN, KIND_OUT, KIND_EXCEPTION, KIND_REOPEN)
    if mode == MODE_EXCEPTIONS:
        if kind == KIND_IN:
            return bool(is_late)
        if kind == KIND_OUT:
            return bool(is_early)
        return kind in (KIND_EXCEPTION, KIND_REOPEN)
    return False


_REOPEN_REASONS = (
    ('Closed by mistake', 'Alifunga kwa makosa'),
    ('Customer arrived after closing', 'Mteja amefika baada ya kufunga'),
    ('Sales still need to be recorded', 'Ana mauzo bado ya kuandika'),
)


def reopen_reason_text(reason, limit=40):
    """Short Swahili reason for the owner; free text ('Other') is kept, ASCII-folded."""
    raw = str(reason or '').strip()
    for english, swahili in _REOPEN_REASONS:
        if raw.startswith(english):
            return ascii_text(swahili, limit)
    if raw.startswith('Other reason'):
        raw = raw.split(':', 1)[1] if ':' in raw else ''
    return ascii_text(raw, limit)


def build_message(kind, employee_name, branch_name, local_time=None, *, late_minutes=0, is_late=False,
                  early_minutes=0, is_early=False, worked_seconds=None, work_start=None, work_end=None,
                  reason=''):
    """Compose the owner SMS as short labelled lines. Always <= 160 characters of ASCII.

    Check-in::

        SMARTPOS - MAHUDHURIO
        Neema Juma ameingia kazini
        Saa 08:03, 15/11/2026
        Hali: Kwa wakati
        Tawi: MANAL COSMETICS

    When the text would exceed one segment, optional detail is dropped in this order:
    the date, then the start/closing-time hint, then the names are shortened.
    """
    # (name limit, branch limit, include date, include detail)
    tiers = ((24, 22, True, True), (24, 22, False, True), (20, 16, False, True),
             (16, 12, False, False), (12, 8, False, False))
    text = ''
    for name_limit, branch_limit, with_date, with_detail in tiers:
        name = ascii_text(employee_name, name_limit) or 'Mfanyakazi'
        branch = ascii_text(branch_name, branch_limit) or 'Tawi'
        lines = [HEADER]
        if kind == KIND_IN:
            when = f'Saa {_hhmm(local_time)}' + (f', {local_time.strftime("%d/%m/%Y")}' if with_date else '')
            if is_late:
                hint = f' (kuanza {_hhmm(work_start)})' if (work_start and with_detail) else ''
                status = f'Hali: AMECHELEWA dakika {max(late_minutes, 1)}{hint}'
            else:
                status = 'Hali: Kwa wakati'
            lines += [f'{name} ameingia kazini', when, status]
        elif kind == KIND_OUT:
            when = f'Saa {_hhmm(local_time)}' + (f', {local_time.strftime("%d/%m/%Y")}' if with_date else '')
            lines += [f'{name} ameondoka kazini', when]
            if worked_seconds is not None:
                lines.append(f'Muda wa kazi: {_worked_text(worked_seconds)}')
            if is_early:
                hint = f' (kufunga {_hhmm(work_end)})' if (work_end and with_detail) else ''
                lines.append(f'Hali: MAPEMA dakika {max(early_minutes, 1)}{hint}')
            else:
                lines.append('Hali: Kwa wakati')
        elif kind == KIND_REOPEN:
            lines += [f'{name} amefunga siku, anaomba kuifungua tena.']
            why = reopen_reason_text(reason, 40 if with_detail else 24)
            if why:
                lines.append(f'Sababu: {why}')
            lines.append('Fungua SmartPOS kuidhinisha.')
        else:
            lines += [f'{name} anaomba idhini ya mahudhurio.', 'Fungua SmartPOS kuidhinisha.']
        lines.append(f'Tawi: {branch}')
        text = '\n'.join(lines)
        if len(text) <= SMS_LIMIT:
            return text
    return text[:SMS_LIMIT]


# --------------------------------------------------------------------------
# Django-aware parts
# --------------------------------------------------------------------------
def owner_recipients(pharmacy):
    """Branch owners that have a valid mobile number: ``[(owner, phone), ...]`` (deduplicated)."""
    from .models import User
    from .phone_utils import normalize_phone_number, is_sms_deliverable

    owners = User.objects.filter(role=User.Role.ADMIN, is_active=True, managed_pharmacies=pharmacy).distinct()
    seen, result = set(), []
    for owner in owners:
        raw = (owner.phone_number or '').strip()
        if not raw:
            continue
        try:
            phone = normalize_phone_number(raw)
        except Exception:
            continue
        if not is_sms_deliverable(phone) or phone in seen:
            continue
        seen.add(phone)
        result.append((owner, phone))
    return result


def queue_attendance_sms(kind, object_id, pharmacy):
    """Schedule the alert after the surrounding transaction commits. Never raises."""
    try:
        mode = getattr(pharmacy, 'attendance_sms_mode', MODE_OFF)
        if mode in (None, '', MODE_OFF):
            return  # Branch has not opted in: no queue traffic at all.
        from django.db import transaction

        def _enqueue():
            try:
                from .tasks import send_attendance_sms
                send_attendance_sms.delay(kind, object_id)
            except Exception:
                logger.exception('Could not queue attendance SMS kind=%s id=%s', kind, object_id)

        transaction.on_commit(_enqueue)
    except Exception:
        logger.exception('Attendance SMS scheduling failed kind=%s id=%s', kind, object_id)


def _deliver(message, recipients):
    from sms_integration.models import SMSLog
    from sms_integration.utils import send_sms

    sent_any = False
    for owner, phone in recipients:
        try:
            if send_sms(phone, message, SMSLog.Category.ATTENDANCE, owner=owner):
                sent_any = True
        except Exception:
            logger.exception('Attendance SMS delivery error for owner=%s', getattr(owner, 'pk', '?'))
    return sent_any


def _employee_name(employee):
    return (employee.get_full_name() or employee.username or '').strip()


def process_attendance_sms(kind, object_id):
    """Build and send one alert. Returns a short status string (useful for tasks/tests)."""
    from django.core.cache import cache
    from django.utils import timezone
    from .models import AttendanceRecord, AttendanceExceptionRequest

    if kind == KIND_EXCEPTION:
        req = AttendanceExceptionRequest.objects.select_related('employee', 'pharmacy').filter(pk=object_id).first()
        if req is None:
            return 'missing'
        if req.status != AttendanceExceptionRequest.Status.PENDING:
            return 'not_pending'
        pharmacy = req.pharmacy
        if not should_send(pharmacy.attendance_sms_mode, KIND_EXCEPTION):
            return 'off'
        recipients = owner_recipients(pharmacy)
        if not recipients:
            return 'no_recipient'
        key = f'att-sms-exc:{req.pk}:{req.date}'
        if not cache.add(key, 1, 60 * 60 * 24):
            return 'already_sent'
        message = build_message(KIND_EXCEPTION, _employee_name(req.employee), pharmacy.name)
        if _deliver(message, recipients):
            return 'sent'
        cache.delete(key)
        return 'failed'

    if kind == KIND_REOPEN:
        from .models import WorkdayReopenRequest
        req = WorkdayReopenRequest.objects.select_related('employee', 'pharmacy').filter(pk=object_id).first()
        if req is None:
            return 'missing'
        if req.status != WorkdayReopenRequest.Status.PENDING:
            return 'not_pending'
        pharmacy = req.pharmacy
        if not should_send(pharmacy.attendance_sms_mode, KIND_REOPEN):
            return 'off'
        recipients = owner_recipients(pharmacy)
        if not recipients:
            return 'no_recipient'
        key = f'att-sms-reo:{req.pk}'
        if not cache.add(key, 1, 60 * 60 * 24):
            return 'already_sent'
        message = build_message(KIND_REOPEN, _employee_name(req.employee), pharmacy.name, reason=req.reason)
        if _deliver(message, recipients):
            return 'sent'
        cache.delete(key)
        return 'failed'

    if kind not in (KIND_IN, KIND_OUT):
        return 'unknown_kind'
    rec = AttendanceRecord.objects.select_related('employee', 'pharmacy').filter(pk=object_id).first()
    if rec is None:
        return 'missing'
    pharmacy = rec.pharmacy
    mode = pharmacy.attendance_sms_mode
    if mode in (None, '', MODE_OFF):
        return 'off'

    stamp_field = 'check_in_sms_sent_at' if kind == KIND_IN else 'check_out_sms_sent_at'
    event_at = rec.check_in_at if kind == KIND_IN else rec.check_out_at
    if event_at is None:
        return 'no_event'
    if getattr(rec, stamp_field):
        return 'already_sent'

    local = timezone.localtime(event_at)
    grace = pharmacy.attendance_sms_grace_minutes or 0
    work_start, work_end = pharmacy.attendance_sms_work_start, pharmacy.attendance_sms_work_end
    late_min = early_min = 0
    is_late = is_early = False
    worked = None
    if kind == KIND_IN:
        late_min = late_minutes_for(local, work_start)
        is_late = late_min > grace
    else:
        early_min = early_minutes_for(local, work_end)
        is_early = early_min > grace
        if rec.check_in_at:
            worked = (event_at - rec.check_in_at).total_seconds()

    if not should_send(mode, kind, is_late=is_late, is_early=is_early):
        return 'not_needed'
    recipients = owner_recipients(pharmacy)
    if not recipients:
        return 'no_recipient'

    # Claim the event first so two workers can never both text the owner.
    claimed = AttendanceRecord.objects.filter(pk=rec.pk, **{f'{stamp_field}__isnull': True}).update(**{stamp_field: timezone.now()})
    if not claimed:
        return 'already_sent'
    message = build_message(
        kind, _employee_name(rec.employee), pharmacy.name, local,
        late_minutes=late_min, is_late=is_late, early_minutes=early_min, is_early=is_early,
        worked_seconds=worked, work_start=work_start, work_end=work_end,
    )
    if _deliver(message, recipients):
        return 'sent'
    AttendanceRecord.objects.filter(pk=rec.pk).update(**{stamp_field: None})  # allow one retry
    return 'failed'
