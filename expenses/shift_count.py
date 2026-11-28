"""Employee blind drawer count (V97.44).

Flow: the employee counts the cash he is responsible for WITHOUT seeing the system figure. On submit the server builds
the statement (the same ledger the boss uses), freezes it with the difference, and only then reveals it.
Zero difference -> accepted automatically. Any difference -> the manager accepts it or asks for a recount.
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .cash_rules import clean_variance
from .ledger import cash_sales_qs, employee_cash_reference
from .models import CashDay, EmployeeShiftCount

ZERO = Decimal('0')
MAX_AMOUNT = Decimal('99999999999')
LIVE = (EmployeeShiftCount.Status.PENDING, EmployeeShiftCount.Status.ACCEPTED)


def parse_amount(raw):
    text = str(raw or '').replace(',', '').replace(' ', '').strip()
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValidationError('Enter the cash you counted as a number.')
    if not value.is_finite() or value < 0 or value > MAX_AMOUNT:
        raise ValidationError('Enter a valid amount (0 or more).')
    return value.quantize(Decimal('0.01'))


def opening_holder_id(pharmacy, day, date_value):
    """Opening cash is one amount for the whole branch, so exactly one employee carries it in his count:
    the employee who opened the day, otherwise the only employee with cash sales that day. Nobody else
    includes it (no double counting). None = the opening is not part of any employee's count."""
    if day is None:
        return None
    opener = day.opened_by
    if opener is not None and opener.is_employee_role():
        return opener.pk
    ids = set(cash_sales_qs(pharmacy.pk).filter(business_date=date_value, served_by__isnull=False)
              .values_list('served_by_id', flat=True))
    return next(iter(ids)) if len(ids) == 1 else None


def statement(pharmacy, employee, date_value):
    """The employee's hand-over statement for a day. Same ledger rows the boss sees, plus the opening if he carries it."""
    rows = employee_cash_reference(pharmacy.pk, date_value, employee)
    row = dict(rows[0]) if rows else {}
    for key in ('cash_sales', 'cash_refunds', 'meal_earned', 'expenses', 'expected', 'total_sales', 'non_cash_sales',
                'sales_count', 'meal_not_deducted'):
        row.setdefault(key, ZERO)
    day = CashDay.objects.filter(pharmacy=pharmacy, date=date_value).select_related('opened_by').first()
    holder = opening_holder_id(pharmacy, day, date_value)
    carries = bool(day and holder == employee.pk)
    row['opening'] = day.opening_cash if carries else None
    row['opening_carried'] = bool(carries and day.is_carried_forward)
    row['opening_elsewhere'] = bool(day and not carries and day.opening_cash)
    row['handover'] = row['expected'] + (day.opening_cash if carries else ZERO)
    row['day_closed'] = bool(day and day.status == CashDay.Status.CLOSED)
    return row


def current_count(employee, date_value):
    return (EmployeeShiftCount.objects.filter(employee=employee, date=date_value, status__in=LIVE)
            .order_by('-counted_at').first())


def submit_count(pharmacy, employee, date_value, raw_amount):
    counted = parse_amount(raw_amount)
    with transaction.atomic():
        if current_count(employee, date_value):
            raise ValidationError('You have already submitted your count for today.')
        st = statement(pharmacy, employee, date_value)
        if st['day_closed']:
            raise ValidationError('Today is already closed by your manager.')
        system = (st['handover']).quantize(Decimal('0.01'))
        diff = clean_variance(counted - system)
        matched = diff == 0
        try:
            with transaction.atomic():
                obj = EmployeeShiftCount.objects.create(
                    pharmacy=pharmacy, employee=employee, date=date_value, counted_cash=counted,
                    opening_cash=st['opening'], cash_sales=st['cash_sales'], cash_refunds=st['cash_refunds'],
                    meal_earned=st['meal_earned'], expenses_paid=st['expenses'], system_amount=system,
                    difference=diff,
                    status=EmployeeShiftCount.Status.ACCEPTED if matched else EmployeeShiftCount.Status.PENDING,
                    auto_accepted=matched, decided_at=timezone.now() if matched else None,
                )
        except IntegrityError:
            raise ValidationError('You have already submitted your count for today.')
    if not matched:
        _notify_managers(obj)
    return obj


def add_reason(count, employee, reason):
    reason = (reason or '').strip()
    if count.employee_id != employee.pk:
        raise ValidationError('This count belongs to another employee.')
    if count.difference == 0 or count.status != EmployeeShiftCount.Status.PENDING:
        raise ValidationError('No explanation is needed for this count.')
    if len(reason) < 5:
        raise ValidationError('Write a short explanation (at least 5 characters).')
    count.reason = reason[:1000]
    count.reason_at = timezone.now()
    count.save(update_fields=['reason', 'reason_at'])
    return count


def decide(count, manager, accept, note=''):
    with transaction.atomic():
        locked = EmployeeShiftCount.objects.select_for_update().get(pk=count.pk)
        if locked.status != EmployeeShiftCount.Status.PENDING:
            raise ValidationError('This count has already been decided.')
        locked.status = EmployeeShiftCount.Status.ACCEPTED if accept else EmployeeShiftCount.Status.REJECTED
        locked.decided_by = manager
        locked.decided_at = timezone.now()
        locked.decision_note = (note or '').strip()[:255]
        locked.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
    return locked


def _notify_managers(count):
    """Never raises: a count must not fail on an alert."""
    try:
        from django.urls import reverse
        from notifications.models import Notification
        from notifications.utils import management_recipients, notify_many
        word = 'short' if count.difference < 0 else 'over'
        notify_many(
            management_recipients(count.pharmacy),
            f'Cash count {word}: {count.employee.get_full_name() or count.employee.get_username()}',
            f'Counted TZS {count.counted_cash:,.0f} vs system TZS {count.system_amount:,.0f} '
            f'({"-" if count.difference < 0 else "+"}TZS {abs(count.difference):,.0f}). Review it in Cash Control.',
            category=Notification.Category.SYSTEM, level=Notification.Level.WARNING,
            link_url=reverse('expenses:cash'), pharmacy=count.pharmacy,
            dedupe_key=f'shift-count-{count.pk}',
        )
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('shift count notification failed')
