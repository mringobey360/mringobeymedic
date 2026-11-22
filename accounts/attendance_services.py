
from decimal import Decimal
from math import atan2, cos, radians, sin, sqrt
from datetime import timedelta
import secrets
import time as _time
from django.core import signing
from django.db import transaction
from django.utils import timezone
from .models import AttendanceRecord, AttendanceVerification, AttendanceExceptionRequest, AttendanceCheckpoint, MealWorkday, MealLedger


def queue_owner_attendance_sms(kind, obj, pharmacy=None):
    """Hand an attendance event to the owner-SMS pipeline. Never raises, never blocks check-in."""
    try:
        from .attendance_sms import queue_attendance_sms
        queue_attendance_sms(kind, obj.pk, pharmacy or obj.pharmacy)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Owner attendance SMS hook failed (kind=%s)', kind)

def _distance_m(lat1, lon1, lat2, lon2):
    r=6371000.0; p1,p2=radians(float(lat1)),radians(float(lat2)); dlat=radians(float(lat2)-float(lat1)); dlon=radians(float(lon2)-float(lon1)); a=sin(dlat/2)**2+cos(p1)*cos(p2)*sin(dlon/2)**2
    return 2*r*atan2(sqrt(a),sqrt(max(0.0,1-a)))

def _ip(request):
    f=request.META.get('HTTP_X_FORWARDED_FOR',''); return (f.split(',')[0].strip() if f else request.META.get('REMOTE_ADDR'))

def verify_location(employee, latitude, longitude, accuracy_m):
    branch=employee.pharmacy
    if branch is None: raise ValueError('Your account is not assigned to a branch.')
    if branch.latitude is None or branch.longitude is None: raise ValueError('Attendance location has not been configured for your branch. Ask the manager to set the branch GPS location.')
    if latitude in (None,'') or longitude in (None,''): raise ValueError('Location is required for automatic attendance verification.')
    try:
        lat, lon = float(latitude), float(longitude)
        accuracy = float(accuracy_m) if accuracy_m not in (None, '') else None
    except (TypeError, ValueError):
        raise ValueError('Your browser did not return a valid GPS reading. Please try again.')
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('Your browser returned invalid GPS coordinates. Please try again.')
    if accuracy is None or accuracy <= 0:
        raise ValueError('GPS accuracy could not be determined. Keep Location enabled and try again.')
    if accuracy > 250:
        return False, None, accuracy, (
            f'GPS accuracy is about {accuracy:.0f}m, which is too low for attendance verification. '
            'Move outdoors or near a window, enable precise location, and try again.'
        )
    distance = _distance_m(branch.latitude, branch.longitude, lat, lon)
    allowed = max(30, min(int(branch.geofence_radius_m or 150), 2000))
    # Accuracy is part of the audit. We still use the measured distance as the
    # decision boundary so an employee is never silently accepted outside the
    # configured branch radius.
    inside = distance <= allowed
    if inside:
        note = f'Location verified. Device accuracy {accuracy:.0f}m; distance from branch {distance:.0f}m.'
    else:
        note = f'You are approximately {distance:.0f}m from the assigned branch; allowed radius is {allowed}m.'
    return inside, distance, accuracy, note

@transaction.atomic
def start_verified_workday(employee, latitude, longitude, accuracy_m, request):
    if 'GPS' not in employee.allowed_attendance_methods: raise ValueError('GPS verification is not enabled for your account. Use the method your manager has provided.')
    now=timezone.now(); local_date=timezone.localtime(now).date()
    existing=AttendanceRecord.objects.select_for_update().filter(employee=employee,date=local_date).first()
    if existing and existing.check_in_at: raise ValueError('Your attendance for today has already been recorded or is awaiting review.')
    inside,distance,accuracy,note=verify_location(employee,latitude,longitude,accuracy_m); device=request.headers.get('User-Agent','')[:128]; ip=_ip(request)
    if not inside:
        attendance=existing or AttendanceRecord.objects.create(employee=employee,pharmacy=employee.pharmacy,date=local_date)
        attendance.status=AttendanceRecord.Status.REVIEW; attendance.method=AttendanceRecord.Method.GEOFENCE; attendance.check_in_latitude=Decimal(str(latitude)); attendance.check_in_longitude=Decimal(str(longitude)); attendance.check_in_accuracy_m=Decimal(str(accuracy)) if accuracy is not None else None; attendance.check_in_distance_m=Decimal(str(round(distance,2))) if distance is not None else None; attendance.ip_address=ip; attendance.device_fingerprint=device; attendance.save()
        AttendanceVerification.objects.create(attendance=attendance,latitude=latitude,longitude=longitude,accuracy_m=accuracy,distance_m=distance,result=AttendanceVerification.Result.OUTSIDE,ip_address=ip,device_fingerprint=device,note=note)
        raise ValueError(note)
    attendance,_=AttendanceRecord.objects.get_or_create(employee=employee,pharmacy=employee.pharmacy,date=local_date)
    attendance.status=AttendanceRecord.Status.VERIFIED; attendance.method=AttendanceRecord.Method.GEOFENCE; attendance.check_in_at=now; attendance.check_in_latitude=Decimal(str(latitude)); attendance.check_in_longitude=Decimal(str(longitude)); attendance.check_in_accuracy_m=Decimal(str(accuracy)) if accuracy is not None else None; attendance.check_in_distance_m=Decimal(str(round(distance,2))) if distance is not None else None; attendance.ip_address=ip; attendance.device_fingerprint=device; attendance.verified_at=now
    attendance.presence_status=AttendanceRecord.PresenceStatus.VERIFIED; attendance.last_presence_at=now; attendance.last_presence_distance_m=Decimal(str(round(distance,2))) if distance is not None else None; attendance.last_presence_accuracy_m=Decimal(str(accuracy)) if accuracy is not None else None; attendance.outside_since=None; attendance.presence_check_count=0; attendance.save()
    AttendanceVerification.objects.create(attendance=attendance,latitude=latitude,longitude=longitude,accuracy_m=accuracy,distance_m=distance,result=AttendanceVerification.Result.VERIFIED,ip_address=ip,device_fingerprint=device,note=note)
    workday,_=MealWorkday.objects.get_or_create(employee=employee,date=local_date,defaults={'opened_at':now,'opened_manually':False,'note':'GPS attendance verified.'})
    if workday.closed_at is not None: raise ValueError("Today's workday has already been closed.")
    queue_owner_attendance_sms('IN', attendance)
    return attendance,workday

def record_presence_check(attendance,latitude,longitude,accuracy_m,request):
    now=timezone.now()
    previous=attendance.presence_status
    inside,distance,accuracy,note=verify_location(attendance.employee,latitude,longitude,accuracy_m)
    result=AttendanceVerification.Result.VERIFIED if inside else AttendanceVerification.Result.OUTSIDE
    event=AttendanceVerification.objects.create(attendance=attendance,latitude=latitude,longitude=longitude,accuracy_m=accuracy,distance_m=distance,result=result,ip_address=_ip(request),device_fingerprint=request.headers.get('User-Agent','')[:128],note=note)
    attendance.last_presence_at=now
    attendance.last_presence_distance_m=Decimal(str(round(distance,2))) if distance is not None else None
    attendance.last_presence_accuracy_m=Decimal(str(accuracy)) if accuracy is not None else None
    attendance.presence_check_count=(attendance.presence_check_count or 0)+1
    attendance.presence_status=AttendanceRecord.PresenceStatus.VERIFIED if inside else AttendanceRecord.PresenceStatus.OUTSIDE
    if inside:
        returned=previous == AttendanceRecord.PresenceStatus.OUTSIDE or attendance.outside_since is not None
        attendance.outside_since=None
        attendance.outside_alert_sent_at=None
        if attendance.status == AttendanceRecord.Status.REVIEW and attendance.check_in_at:
            attendance.status=AttendanceRecord.Status.VERIFIED
        if returned:
            from notifications.utils import notify_many, management_recipients
            managers=management_recipients(attendance.pharmacy)
            notify_many(managers, 'Employee returned to workplace', f'{attendance.employee.get_full_name() or attendance.employee.username} is back within the {attendance.pharmacy.name} workplace boundary ({distance:.0f}m).', category='SYSTEM', level='INFO', link_url='/accounts/attendance/history/', pharmacy=attendance.pharmacy)
    else:
        if attendance.outside_since is None:
            attendance.outside_since=now
        if attendance.status == AttendanceRecord.Status.VERIFIED:
            attendance.status=AttendanceRecord.Status.REVIEW
            attendance.review_note=f'Presence check flagged outside geofence at {event.checked_at:%H:%M}.'
        # Brief GPS drift should not create an immediate alarm. Notify only
        # after the configured outside grace period has elapsed.
        grace = max(0, int(getattr(attendance.pharmacy, 'attendance_outside_grace_minutes', 5) or 0))
        if (
            attendance.outside_since
            and (now - attendance.outside_since) >= timedelta(minutes=grace)
            and attendance.outside_alert_sent_at is None
        ):
            from notifications.utils import notify_many, management_recipients
            managers=management_recipients(attendance.pharmacy)
            notify_many(
                managers,
                'Employee outside workplace',
                f'{attendance.employee.get_full_name() or attendance.employee.username} has remained about {distance:.0f}m from {attendance.pharmacy.name}; allowed radius is {max(30, min(int(attendance.pharmacy.geofence_radius_m or 150), 2000))}m.',
                category='SYSTEM', level='WARNING',
                link_url='/accounts/attendance/history/', pharmacy=attendance.pharmacy
            )
            attendance.outside_alert_sent_at=now
    attendance.save(update_fields=['last_presence_at','last_presence_distance_m','last_presence_accuracy_m','presence_check_count','presence_status','outside_since','outside_alert_sent_at','status','review_note','updated_at'])
    return event

def mark_presence_unavailable(attendance, note, request):
    now=timezone.now()
    event=AttendanceVerification.objects.create(attendance=attendance,result=AttendanceVerification.Result.UNAVAILABLE,ip_address=_ip(request),device_fingerprint=request.headers.get('User-Agent','')[:128],note=note[:255])
    attendance.last_presence_at=now
    attendance.presence_status=AttendanceRecord.PresenceStatus.UNAVAILABLE
    attendance.presence_check_count=(attendance.presence_check_count or 0)+1
    attendance.save(update_fields=['last_presence_at','presence_status','presence_check_count','updated_at'])
    return event

def request_manual_exception(employee,reason,request):
    today=timezone.localtime(timezone.now()).date()
    if 'MANUAL' not in employee.allowed_attendance_methods: raise ValueError('Manager-approval requests are not enabled for your account.')
    if not reason.strip(): raise ValueError('Please explain why automatic location verification could not be completed.')
    obj,created=AttendanceExceptionRequest.objects.get_or_create(employee=employee,pharmacy=employee.pharmacy,date=today,defaults={'reason':reason.strip(),'ip_address':_ip(request)})
    if not created and obj.status==AttendanceExceptionRequest.Status.PENDING: raise ValueError('You already have a pending attendance exception request for today.')
    if not created: obj.reason=reason.strip(); obj.status=AttendanceExceptionRequest.Status.PENDING; obj.decided_by=None; obj.decided_at=None; obj.decision_note=''; obj.save()
    return obj

def approve_exception(obj,manager,note=''):
    if obj.status!=AttendanceExceptionRequest.Status.PENDING: raise ValueError('This request has already been decided.')
    now=timezone.now(); attendance,_=AttendanceRecord.objects.get_or_create(employee=obj.employee,pharmacy=obj.pharmacy,date=obj.date)
    attendance.status=AttendanceRecord.Status.VERIFIED; attendance.method=AttendanceRecord.Method.MANUAL; attendance.check_in_at=attendance.check_in_at or obj.requested_at; attendance.reviewed_by=manager; attendance.review_note=note or 'Manual attendance approved by manager.'; attendance.verified_at=now; attendance.save()
    obj.status=AttendanceExceptionRequest.Status.APPROVED; obj.decided_by=manager; obj.decided_at=now; obj.decision_note=note; obj.save()
    MealWorkday.objects.get_or_create(employee=obj.employee,date=obj.date,defaults={'opened_at':attendance.check_in_at,'opened_manually':True,'note':'Manager-approved attendance exception.'})
    return attendance

def reject_exception(obj,manager,note=''):
    if obj.status!=AttendanceExceptionRequest.Status.PENDING: raise ValueError('This request has already been decided.')
    obj.status=AttendanceExceptionRequest.Status.REJECTED; obj.decided_by=manager; obj.decided_at=timezone.now(); obj.decision_note=note; obj.save()


# ---------------------------------------------------------------------------
# Dynamic branch QR (fallback when GPS is unavailable/inaccurate)
# ---------------------------------------------------------------------------
QR_SALT = 'attendance-branch-qr-v1'
QR_SCAN_NOTE = 'QR scan'  # prefix for audit rows written by the scan-location gate
QR_ROTATE_SECONDS = 30   # the manager screen fetches a fresh QR this often
QR_MAX_AGE_SECONDS = 60  # a scanned token older than this is rejected
QR_LOCATION_MAX_AGE_SECONDS = 150  # longer when the scan must also prove GPS presence (GPS can take a while)


def make_branch_qr_token(pharmacy):
    """Server-signed, short-lived token that identifies one branch."""
    return signing.dumps({'p': pharmacy.pk, 'n': secrets.token_hex(4)}, salt=QR_SALT)


def read_branch_qr_token(token, max_age=None):
    """Return the pharmacy id inside a valid token or raise ValueError."""
    try:
        data = signing.loads(token or '', salt=QR_SALT, max_age=max_age or QR_MAX_AGE_SECONDS)
        return int(data['p'])
    except signing.SignatureExpired:
        raise ValueError('This QR code has expired. Scan the code currently shown at the branch.')
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValueError('This QR code is not valid. Scan the code shown on the branch screen.')


def qr_token_seconds_left(token, max_age=None):
    """Seconds before a scanned branch token expires (None if it cannot be read)."""
    try:
        issued = signing.b62_decode(str(token).split(':')[-2])
        return max(0, (max_age or QR_MAX_AGE_SECONDS) - int(_time.time() - issued))
    except Exception:
        return None


def _qr_action_for_employee(employee, local_now, attendance):
    """Determine the next professional QR action from the employee policy and meal window."""
    from .meal_services import _entitlements, meal_timing_for
    if not attendance or not attendance.check_in_at:
        return 'START', 'Check in'
    if attendance.check_out_at:
        return 'ALREADY', 'Already checked out'
    entitlements, _, _ = _entitlements(employee, local_now.date())
    timing = meal_timing_for(employee)
    reopened = (
        MealLedger.objects.filter(
            employee=employee, date=local_now.date(), claimed_at__isnull=True,
            scan_reopened_at__isnull=False, status=MealLedger.Status.PENDING,
        ).order_by('scan_reopened_at').first()
    )
    if reopened and reopened.meal_type in ('BREAKFAST', 'LUNCH', 'DINNER') and entitlements.get(reopened.meal_type):
        return reopened.meal_type, dict((m[0], m[3]) for m in [
            ('BREAKFAST', '', '', 'Breakfast'), ('LUNCH', '', '', 'Lunch'), ('DINNER', '', '', 'Dinner')
        ])[reopened.meal_type]
    current = local_now.time()
    windows = [
        ('BREAKFAST', timing.breakfast_start, timing.breakfast_cutoff, 'Breakfast'),
        ('LUNCH', timing.lunch_start, timing.lunch_cutoff, 'Lunch'),
        ('DINNER', timing.dinner_start, timing.dinner_cutoff, 'Dinner'),
    ]
    for meal_type, start, end, label in windows:
        if not entitlements.get(meal_type):
            continue
        if start <= current <= end and not attendance.checkpoints.filter(checkpoint_type=meal_type).exists():
            return meal_type, label
    # Check-out is deliberately only offered after the last configured meal window.
    latest_cutoff = max(timing.breakfast_cutoff, timing.lunch_cutoff, timing.dinner_cutoff)
    if current >= latest_cutoff:
        return 'CHECKOUT', 'Check out'
    return 'WAIT', 'Next checkpoint'


def _restore_verified_after_scan(attendance, now):
    """A QR scan that passed the workplace GPS check is fresh proof of presence.

    A single drifting presence reading can flip a checked-in employee to REVIEW. Because a
    scan that recorded something already required the device to be inside the branch radius,
    it re-verifies attendance exactly like a successful presence check, so the employee is
    not locked out of meals until a manager steps in.
    """
    attendance.status = AttendanceRecord.Status.VERIFIED
    attendance.presence_status = AttendanceRecord.PresenceStatus.VERIFIED
    attendance.last_presence_at = now
    attendance.outside_since = None
    attendance.outside_alert_sent_at = None
    attendance.presence_check_count = 0
    stamp = f'Re-verified by QR scan at the workplace at {timezone.localtime(now):%H:%M}.'
    attendance.review_note = f'{attendance.review_note} {stamp}'.strip()[:255] if getattr(attendance, 'review_note', '') else stamp
    attendance.save(update_fields=[
        'status', 'presence_status', 'last_presence_at', 'outside_since', 'outside_alert_sent_at',
        'presence_check_count', 'review_note', 'updated_at',
    ])


@transaction.atomic
def _start_workday_with_qr_atomic(employee, token, request, max_age=None, location_verified=False):
    """Dynamic branch QR handles check-in, meal checkpoints and end-of-shift check-out."""
    if employee.pharmacy_id is None:
        raise ValueError('Your account is not assigned to a branch.')
    if read_branch_qr_token(token, max_age) != employee.pharmacy_id:
        raise ValueError('This QR code belongs to a different branch.')

    now = timezone.now()
    local_now = timezone.localtime(now)
    local_date = local_now.date()
    existing = AttendanceRecord.objects.select_for_update().filter(employee=employee, date=local_date).first()

    qr_enabled = 'QR' in employee.allowed_attendance_methods
    if not existing or not existing.check_in_at:
        if not qr_enabled:
            raise ValueError('QR verification is not enabled for your account. Use the method your manager has provided.')
        if existing and existing.verifications.filter(result=AttendanceVerification.Result.OUTSIDE).exclude(note__startswith=QR_SCAN_NOTE).exists():
            raise ValueError('GPS placed you outside the branch boundary today, so QR cannot be used. Please request manager approval.')
        ip = _ip(request)
        device = request.headers.get('User-Agent', '')[:128]
        attendance = existing or AttendanceRecord.objects.create(employee=employee, pharmacy=employee.pharmacy, date=local_date)
        attendance.status = AttendanceRecord.Status.VERIFIED
        attendance.method = AttendanceRecord.Method.QR
        attendance.check_in_at = now
        attendance.verified_at = now
        attendance.ip_address = ip
        attendance.device_fingerprint = device
        attendance.presence_status = AttendanceRecord.PresenceStatus.VERIFIED
        attendance.last_presence_at = now
        attendance.outside_since = None
        attendance.presence_check_count = 0
        attendance.save()
        AttendanceVerification.objects.create(attendance=attendance, result=AttendanceVerification.Result.VERIFIED, ip_address=ip, device_fingerprint=device, note='Verified with dynamic branch QR.')
        workday, _ = MealWorkday.objects.get_or_create(employee=employee, date=local_date, defaults={'opened_at': now, 'opened_manually': False, 'note': 'Branch QR attendance verified.'})
        if workday.closed_at is not None:
            raise ValueError("Today's workday has already been closed.")
        queue_owner_attendance_sms('IN', attendance)
        return 'START'

    action, label = _qr_action_for_employee(employee, local_now, existing)
    if action == 'ALREADY':
        raise ValueError("Today's workday is already closed.")
    if action == 'WAIT':
        from .meal_services import meal_wait_message
        raise ValueError(meal_wait_message(employee, now, existing))
    if action == 'CHECKOUT':
        if not qr_enabled:
            raise ValueError('Check-out by QR is not enabled for your account. Use Close Workday on your dashboard.')
        from .meal_services import close_workday
        AttendanceCheckpoint.objects.create(attendance=existing, checkpoint_type=AttendanceCheckpoint.Type.CHECKOUT, method=AttendanceCheckpoint.Method.QR, ip_address=_ip(request), device_fingerprint=request.headers.get('User-Agent', '')[:128], note='End-of-shift check-out completed with dynamic branch QR.')
        close_workday(employee, now)
        existing.check_out_at = now
        existing.status = AttendanceRecord.Status.CLOSED
        existing.save(update_fields=['check_out_at', 'status', 'updated_at'])
        queue_owner_attendance_sms('OUT', existing)
        return 'CHECKOUT'

    if (location_verified and existing.status == AttendanceRecord.Status.REVIEW
            and existing.check_in_at and not existing.check_out_at):
        _restore_verified_after_scan(existing, now)
    if existing.effective_presence_status == AttendanceRecord.PresenceStatus.OUTSIDE:
        raise ValueError('Your latest presence check shows you outside the branch. Return to the workplace and tap Verify Presence on your dashboard, or request manager review.')
    if not existing.is_verified:
        raise ValueError('Your attendance needs re-verification before a meal can be claimed. Tap Verify Presence on your dashboard while you are at the workplace, or ask your manager to review it.')
    if existing.checkpoints.filter(checkpoint_type=action).exists():
        raise ValueError(f'{label} is already recorded for today.')
    AttendanceCheckpoint.objects.create(attendance=existing, checkpoint_type=action, method=AttendanceCheckpoint.Method.QR, ip_address=_ip(request), device_fingerprint=request.headers.get('User-Agent', '')[:128], note=f'{label} claimed with dynamic branch QR.')
    # Scan is the proof that issues the meal: record the claim on the ledger.
    from .meal_services import claim_meal
    claim_meal(employee, action, now)
    return action


def qr_requires_location(pharmacy):
    return bool(pharmacy is not None and getattr(pharmacy, 'attendance_qr_requires_location', True))


def _parse_location(location):
    """Return (lat, lon, accuracy) from a dict of raw form values or (None, None, None)."""
    if not location:
        return None, None, None
    return location.get('latitude'), location.get('longitude'), location.get('accuracy')


def _scan_needs_location(employee, local_now, existing):
    """A scan that would RECORD something (check-in, meal, check-out) needs proof of presence."""
    if not existing or not existing.check_in_at:
        return True
    action, _label = _qr_action_for_employee(employee, local_now, existing)
    return action in ('BREAKFAST', 'LUNCH', 'DINNER', 'CHECKOUT')


def _log_scan_location(employee, attendance, result, lat, lon, accuracy, distance, note, request):
    """Persist an audit row. Done OUTSIDE the atomic scan so a refused scan is still on record."""
    return AttendanceVerification.objects.create(
        attendance=attendance, latitude=lat, longitude=lon, accuracy_m=accuracy, distance_m=distance,
        result=result, ip_address=_ip(request) if request is not None else None,
        device_fingerprint=(request.headers.get('User-Agent', '')[:128] if request is not None else ''),
        note=(QR_SCAN_NOTE + ': ' + note)[:255],
    )


def check_scan_location(employee, location, request, action_label='scan'):
    """Gate a QR scan on the employee's device GPS being inside the branch radius.

    Returns (lat, lon, accuracy, distance) when the employee is at the workplace.
    Raises ValueError otherwise; a refused attempt is stored for the manager
    (the employee gets no attendance and therefore no meal).
    """
    branch = employee.pharmacy
    lat, lon, acc = _parse_location(location)
    inside, distance, accuracy, note = verify_location(employee, lat, lon, acc)
    if not inside and distance is None:
        raise ValueError(note)  # accuracy too weak to judge: ask to retry, do not accuse
    local_date = timezone.localdate()
    attendance = AttendanceRecord.objects.filter(employee=employee, date=local_date).first()
    if not inside:
        if attendance is None:
            attendance = AttendanceRecord.objects.create(
                employee=employee, pharmacy=branch, date=local_date,
                status=AttendanceRecord.Status.REVIEW, method=AttendanceRecord.Method.QR)
        _log_scan_location(employee, attendance, AttendanceVerification.Result.OUTSIDE,
                           lat, lon, accuracy, distance, f'{action_label} refused. {note}', request)
        raise ValueError(
            f'You are about {distance:.0f} m from the workplace (allowed {max(30, min(int(branch.geofence_radius_m or 150), 2000))} m). '
            'The scan was refused and recorded. Scanning is only possible at the workplace, so no attendance or meal is issued.')
    return float(lat), float(lon), accuracy, distance


def start_workday_with_qr(employee, token, request, location=None):
    """Public entry: validates the QR, enforces workplace GPS when the branch requires it, then records."""
    if employee.pharmacy_id is None:
        raise ValueError('Your account is not assigned to a branch.')
    branch = employee.pharmacy
    needs_gps = qr_requires_location(branch)
    max_age = QR_LOCATION_MAX_AGE_SECONDS if needs_gps else None
    if read_branch_qr_token(token, max_age) != employee.pharmacy_id:
        raise ValueError('This QR code belongs to a different branch.')
    fix = None
    if needs_gps:
        local_now = timezone.localtime(timezone.now())
        existing = AttendanceRecord.objects.filter(employee=employee, date=local_now.date()).first()
        if _scan_needs_location(employee, local_now, existing):
            fix = check_scan_location(employee, location, request, 'QR scan')
    action = _start_workday_with_qr_atomic(employee, token, request, max_age, location_verified=fix is not None)
    if fix is not None:
        lat, lon, accuracy, distance = fix
        attendance = AttendanceRecord.objects.filter(employee=employee, date=timezone.localdate()).first()
        if attendance is not None:
            label = {'START': 'Check-in', 'CHECKOUT': 'Check-out'}.get(action, action.title() + ' claim')
            note = f'{label} location verified at the workplace. Accuracy {accuracy:.0f} m; distance {distance:.0f} m.'
            _log_scan_location(employee, attendance, AttendanceVerification.Result.VERIFIED, lat, lon, accuracy, distance, note, request)
            if action == 'START':
                attendance.check_in_latitude = Decimal(str(round(lat, 7)))
                attendance.check_in_longitude = Decimal(str(round(lon, 7)))
                attendance.check_in_accuracy_m = Decimal(str(round(accuracy, 2)))
                attendance.check_in_distance_m = Decimal(str(round(distance, 2)))
                attendance.save(update_fields=['check_in_latitude', 'check_in_longitude', 'check_in_accuracy_m', 'check_in_distance_m'])
    return action


def record_lunch_qr_checkpoint(employee, token, request, location=None):
    return start_workday_with_qr(employee, token, request, location)
