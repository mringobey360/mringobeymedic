"""Server-side guard: an employee whose workday is closed cannot sell.

Why this exists
---------------
Closing the workday (or checking out with the branch QR) is a hard stop.  The
dashboard already told the employee "sales continue after approval", but until
v97.18 nothing on the SERVER enforced it, so an employee who closed the day by
accident could keep using the POS while the reopen request was still waiting
for the Boss.  Every sale-recording endpoint now asks this module first.

Only the Boss/manager can lift the block, by approving the reopen request (or
using "Reopen workday" on Meals & Attendance), which clears ``closed_at`` and the
attendance ``check_out_at``.

The guard only ever applies to the EMPLOYEE role, only to TODAY, and only when
a workday record exists and is closed.  Managers, assistants and employees who
never started a workday are not affected (the branch may not use attendance).
"""
from django.utils import timezone

CLOSED_MESSAGE = (
    'Your workday is closed, so sales and payments are locked. '
    'Ask your Boss/manager to approve your reopening request, then you can continue.'
)


def closed_workday_for(user):
    """Return today's closed ``MealWorkday`` (or a matching closed attendance row) for an employee, else None."""
    if not getattr(user, 'is_authenticated', False):
        return None
    is_employee = getattr(user, 'is_employee_role', None)
    if not callable(is_employee) or not is_employee() or not getattr(user, 'pharmacy_id', None):
        return None
    from .models import AttendanceRecord, MealWorkday

    today = timezone.localdate()
    workday = MealWorkday.objects.filter(employee=user, date=today).first()
    if workday is not None and workday.closed_at is not None:
        return workday
    attendance = AttendanceRecord.objects.filter(employee=user, date=today, check_out_at__isnull=False).first()
    if attendance is not None:
        return attendance
    return None


def workday_sales_block_message(user):
    """Human-readable reason when the employee must not record sales right now, else an empty string."""
    return CLOSED_MESSAGE if closed_workday_for(user) is not None else ''


def closed_moment(record):
    """The aware datetime at which a closed workday/attendance row was closed (None if unknown)."""
    if record is None:
        return None
    return getattr(record, 'closed_at', None) or getattr(record, 'check_out_at', None)
