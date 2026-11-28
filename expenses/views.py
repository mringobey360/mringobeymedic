import io
from datetime import datetime, timedelta
from decimal import Decimal
from xml.sax.saxutils import escape as _esc

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.decorators import admin_required, employee_or_admin_required
from .forms import ExpenseForm, CashOpenForm, CashCloseForm, CashCountForm, CashOpeningAdjustmentForm, ExpenseDecisionForm, CashCountCorrectionForm, MobileMoneyCheckForm, CashTransferForm
from .cash_rules import clean_variance, reconstruct_closing_cash
from .ledger import CashLedgerService, employee_cash_reference
from .models import Expense, CashDay, ExpenseCategory, CashDayReopenAudit, CashAuditEvent, CashTransfer
from .services import refresh_provisional_opening, record_audit, create_closing_snapshot, approve_expense, reject_expense, confirm_expense, record_auto_expense, seed_categories, notify_managers, ensure_cash_day_for_date, rollover_business_day, get_cash_activity_history, record_cash_count, reconcile_provisional_openings, repair_provisional_opening_chain, heal_stale_provisional_closed_days


def _pharmacy(request):
    if request.pharmacy is None:
        messages.warning(request, 'You do not have an active branch.')
        return None
    seed_categories(request.pharmacy)
    return request.pharmacy


@employee_or_admin_required
def expense_list(request):
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    qs = Expense.objects.filter(pharmacy=pharmacy).select_related('category', 'submitted_by', 'decided_by')
    if not request.user.is_management_user():
        qs = qs.filter(submitted_by=request.user)
    status = request.GET.get('status', '').strip()
    category = request.GET.get('category', '').strip()
    q = request.GET.get('q', '').strip()
    date_from = _parse_date(request.GET.get('from', ''), None)
    date_to = _parse_date(request.GET.get('to', ''), None)
    if status in dict(Expense.Status.choices):
        qs = qs.filter(status=status)
    else:
        status = ''
    if category.isdigit():
        qs = qs.filter(category_id=category)
    else:
        category = ''
    if q:
        qs = qs.filter(
            Q(title__icontains=q) | Q(description__icontains=q) | Q(reference__icontains=q) |
            Q(category__name__icontains=q) | Q(submitted_by__first_name__icontains=q) |
            Q(submitted_by__last_name__icontains=q) | Q(submitted_by__username__icontains=q)
        )
    if date_from:
        qs = qs.filter(expense_date__gte=date_from)
    if date_to:
        qs = qs.filter(expense_date__lte=date_to)
    qs = qs.order_by('-expense_date', '-submitted_at')
    total = qs.filter(status=Expense.Status.APPROVED).aggregate(v=Sum('amount'))['v'] or Decimal('0')
    # Pending counter: managers see every pending expense of the branch,
    # an employee sees their own pending expenses (previously always 0).
    pending_qs = Expense.objects.filter(pharmacy=pharmacy, status=Expense.Status.PENDING)
    if not request.user.is_management_user():
        pending_qs = pending_qs.filter(submitted_by=request.user)
    pending = pending_qs.count()
    review_qs = Expense.objects.filter(pharmacy=pharmacy, auto_recorded=True, status=Expense.Status.APPROVED, reviewed_at__isnull=True)
    if not request.user.is_management_user():
        review_qs = review_qs.filter(submitted_by=request.user)
    review_count = review_qs.count()
    try:
        per_page = int(request.GET.get('per_page', 20))
    except (TypeError, ValueError):
        per_page = 20
    if per_page not in (10, 20, 50, 100):
        per_page = 20
    page = Paginator(qs, per_page).get_page(request.GET.get('page'))
    return render(request, 'expenses/list.html', {
        'expenses': page,
        'categories': ExpenseCategory.objects.filter(Q(pharmacy=pharmacy) | Q(pharmacy__isnull=True), is_active=True),
        'total_approved': total,
        'pending_count': pending,
        'review_count': review_count,
        'status_choices': Expense.Status.choices,
        'f_status': status, 'f_category': category, 'f_q': q, 'f_per_page': per_page,
        'f_from': date_from, 'f_to': date_to,
        'has_filters': bool(status or category or q or date_from or date_to),
    })


@employee_or_admin_required
@require_http_methods(['GET', 'POST'])
def expense_create(request):
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    form = ExpenseForm(request.POST or None, request.FILES or None, pharmacy=pharmacy, user=request.user)
    ctx = {'form': form, 'title': 'Record Business Expense'}
    if request.method == 'POST' and form.is_valid():
        expense = form.save(commit=False)
        expense.pharmacy = pharmacy
        expense.submitted_by = request.user
        expense.status = Expense.Status.PENDING
        try:
            expense.full_clean()
        except ValidationError as exc:
            for message in exc.messages:
                form.add_error(None, message)
            return render(request, 'expenses/form.html', ctx)
        auto = (not request.user.is_management_user()) and expense.payment_source == Expense.PaymentSource.CASH_SALES
        if request.user.is_management_user() and expense.payment_source == Expense.PaymentSource.CASH_SALES:
            day = CashDay.objects.filter(pharmacy=pharmacy, date=expense.expense_date).first()
            if day is not None and day.status in [CashDay.Status.DAY_ENDED, CashDay.Status.CLOSED]:
                form.add_error('payment_source', 'The cash day for this date has ended. Record the expense on the active business date or use the authorized correction workflow.')
                return render(request, 'expenses/form.html', ctx)
            if day is None and expense.expense_date == timezone.localdate():
                try:
                    ensure_cash_day_for_date(pharmacy, request.user, expense.expense_date)
                except ValueError as exc:
                    form.add_error('payment_source', str(exc))
                    return render(request, 'expenses/form.html', ctx)
        if request.user.is_management_user():
            expense.status = Expense.Status.APPROVED
            expense.decided_by = request.user
            expense.decided_at = timezone.now()
            expense.decision_note = 'Owner/supervisor recorded and authorized this expense.'
            expense.cash_date = expense.expense_date
            expense.save()
            messages.success(request, 'Expense recorded and authorized. It is included in cash control where applicable.')
        elif auto:
            # Employee, sales cash: deducted from the drawer right now; the boss is notified and can reject at any time.
            try:
                record_auto_expense(expense, request.user)
            except ValueError as exc:
                form.add_error('payment_source', str(exc))
                return render(request, 'expenses/form.html', ctx)
            messages.success(request, 'Expense recorded and deducted from the drawer. The manager has been notified and may review it.')
        else:
            expense.save()
            notify_managers(
                pharmacy, 'New expense awaiting approval',
                f'{expense.title} — TZS {expense.amount:,.0f} submitted by {request.user.get_full_name() or request.user.username}.',
                level='WARNING', link=reverse('expenses:detail', args=[expense.pk]),
            )
            messages.success(request, 'Expense submitted successfully for manager approval.')
        return redirect('expenses:detail', expense.pk)
    return render(request, 'expenses/form.html', ctx)


@employee_or_admin_required
def expense_detail(request, pk):
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    expense = get_object_or_404(Expense.objects.select_related('category', 'submitted_by', 'decided_by'), pk=pk, pharmacy=pharmacy)
    if not request.user.is_management_user() and expense.submitted_by_id != request.user.id:
        return redirect('expenses:list')
    return render(request, 'expenses/detail.html', {'expense': expense, 'decision_form': ExpenseDecisionForm()})


@admin_required
@require_http_methods(['POST'])
def expense_approve(request, pk):
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    expense = get_object_or_404(Expense, pk=pk, pharmacy=pharmacy)
    try:
        approve_expense(expense, request.user, request.POST.get('note', ''))
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Expense approved and, where applicable, deducted from the expected cash of that day.')
    return redirect('expenses:detail', pk)


@admin_required
@require_http_methods(['POST'])
def expense_reject(request, pk):
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    expense = get_object_or_404(Expense, pk=pk, pharmacy=pharmacy)
    try:
        reject_expense(expense, request.user, request.POST.get('note', ''))
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Expense rejected. If it had already been deducted, the amount has been returned to the cash books today and the original day was left untouched.')
    return redirect('expenses:detail', pk)


@admin_required
@require_http_methods(['POST'])
def expense_confirm(request, pk):
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    expense = get_object_or_404(Expense, pk=pk, pharmacy=pharmacy)
    try:
        confirm_expense(expense, request.user, request.POST.get('note', ''))
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Expense confirmed. You can still reject it later if something is wrong.')
    return redirect('expenses:detail', pk)


# --------------------------------------------------------------------------
# Expense report (screen + PDF + Excel share one data builder so the numbers
# in all three are always identical).
# --------------------------------------------------------------------------

def _parse_date(value, default):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date() if value else default
    except (TypeError, ValueError):
        return default


def _report_data(request, pharmacy):
    today = timezone.localdate()
    start = _parse_date(request.GET.get('start', ''), today.replace(day=1))
    end = _parse_date(request.GET.get('end', ''), today)
    if start > end:
        start, end = end, start
    qs = Expense.objects.filter(pharmacy=pharmacy, expense_date__range=(start, end)).select_related(
        'category', 'submitted_by', 'decided_by').order_by('expense_date', 'submitted_at')
    approved = qs.filter(status=Expense.Status.APPROVED)
    category_totals = {row['category__name']: row['total']
                       for row in approved.values('category__name').annotate(total=Sum('amount')).order_by('-total')}
    total = approved.aggregate(v=Sum('amount'))['v'] or Decimal('0')
    cash = approved.filter(payment_source=Expense.PaymentSource.CASH_SALES).aggregate(v=Sum('amount'))['v'] or Decimal('0')
    pending = qs.filter(status=Expense.Status.PENDING).aggregate(v=Sum('amount'))['v'] or Decimal('0')
    return {
        'expenses': qs, 'start': start, 'end': end, 'total': total, 'cash_total': cash,
        'pending_total': pending, 'category_totals': category_totals,
    }


def _person(user):
    return (user.get_full_name() or user.username) if user else '-'


@admin_required
def expense_report(request):
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    ctx = _report_data(request, pharmacy)
    # Screen table is paginated; PDF/Excel keep using the complete queryset.
    try:
        per_page = int(request.GET.get('per_page', 25))
    except (TypeError, ValueError):
        per_page = 25
    if per_page not in (10, 25, 50, 100):
        per_page = 25
    ctx['page_obj'] = Paginator(ctx['expenses'].order_by('-expense_date', '-submitted_at'), per_page).get_page(request.GET.get('page'))
    ctx['f_per_page'] = per_page
    return render(request, 'expenses/report.html', ctx)


@admin_required
def expense_report_pdf(request):
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    from core.pdf_utils import generate_pdf_report
    data = _report_data(request, pharmacy)
    # ReportLab parses cell text as mini-markup, so user-entered text is escaped
    # (a title such as "Tea < Sugar" would otherwise break the whole PDF).
    rows = [[
        f"{e.expense_date:%d %b %Y}", _esc(e.title), _esc(e.category.name), e.get_payment_source_display(),
        e.get_status_display(), f"{e.amount:,.0f}",
    ] for e in data['expenses']]
    summary = [
        ('Branch', _esc(pharmacy.name)),
        ('Period', f"{data['start']:%d %B %Y} — {data['end']:%d %B %Y}"),
        ('Approved expenses', f"TZS {data['total']:,.0f}"),
        ('Paid from sales cash', f"TZS {data['cash_total']:,.0f}"),
        ('Pending approval', f"TZS {data['pending_total']:,.0f}"),
    ]
    for name, value in data['category_totals'].items():
        summary.append((f'  {_esc(name)}', f"TZS {value:,.0f}"))
    return generate_pdf_report(
        title='Expense Report',
        subtitle=f"{_esc(pharmacy.name)} — {data['start']:%d %B %Y} to {data['end']:%d %B %Y}",
        headers=['Date', 'Expense', 'Category', 'Source', 'Status', 'Amount (TZS)'],
        rows=rows, summary=summary,
        filename=f"expense_report_{data['start']}_{data['end']}.pdf",
        column_widths=[22, 38, 42, 28, 24, 26],
    )


def _set_text(cell, value):
    """Write user text as plain text (never as an Excel formula)."""
    cell.value = str(value)
    cell.data_type = 's'


@admin_required
def expense_report_excel(request):
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    data = _report_data(request, pharmacy)
    wb = Workbook()
    ws = wb.active
    ws.title = 'Expenses'
    head_fill = PatternFill('solid', fgColor='0D9488')
    head_font = Font(bold=True, color='FFFFFF')
    bold = Font(bold=True)

    _set_text(ws['A1'], 'Expense Report')
    ws['A1'].font = Font(bold=True, size=14)
    _set_text(ws['A2'], f"{pharmacy.name} — {data['start']:%d %b %Y} to {data['end']:%d %b %Y}")
    summary = [('Approved expenses (TZS)', data['total']), ('Paid from sales cash (TZS)', data['cash_total']),
               ('Pending approval (TZS)', data['pending_total'])]
    row = 4
    for label, value in summary:
        _set_text(ws.cell(row=row, column=1), label)
        ws.cell(row=row, column=1).font = bold
        c = ws.cell(row=row, column=2, value=float(value))
        c.number_format = '#,##0'
        row += 1

    row += 1
    headers = ['Date', 'Expense', 'Category', 'Payment source', 'Status', 'Submitted by', 'Decided by', 'Reference', 'Amount (TZS)']
    for col, h in enumerate(headers, start=1):
        c = ws.cell(row=row, column=col, value=h)
        c.fill, c.font = head_fill, head_font
        c.alignment = Alignment(horizontal='center')
    first_data_row = row + 1
    for e in data['expenses']:
        row += 1
        c = ws.cell(row=row, column=1, value=e.expense_date)
        c.number_format = 'DD MMM YYYY'
        for col, value in ((2, e.title), (3, e.category.name), (4, e.get_payment_source_display()),
                           (5, e.get_status_display()), (6, _person(e.submitted_by)),
                           (7, _person(e.decided_by) if e.decided_by_id else ''), (8, e.reference)):
            _set_text(ws.cell(row=row, column=col), value)
        c = ws.cell(row=row, column=9, value=float(e.amount))
        c.number_format = '#,##0'
    if row >= first_data_row:
        ws.freeze_panes = ws.cell(row=first_data_row, column=1)
    for idx, width in enumerate([14, 34, 24, 20, 18, 22, 22, 20, 16], start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width

    cat = wb.create_sheet('By category')
    for col, h in enumerate(['Category', 'Approved total (TZS)'], start=1):
        c = cat.cell(row=1, column=col, value=h)
        c.fill, c.font = head_fill, head_font
    r = 1
    for name, value in data['category_totals'].items():
        r += 1
        _set_text(cat.cell(row=r, column=1), name)
        c = cat.cell(row=r, column=2, value=float(value))
        c.number_format = '#,##0'
    cat.column_dimensions['A'].width = 34
    cat.column_dimensions['B'].width = 22

    buffer = io.BytesIO()
    wb.save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="expense_report_{data["start"]}_{data["end"]}.xlsx"'
    return response


def _cash_day_needs_attention(day):
    """Return the manager-action state for one cash day.

    CLOSED is only fully settled when its close is not followed by cash
    activity and its opening has been reconciled.  A provisional CLOSED day is
    a special downstream state: it can only be cleared automatically when the
    previous verified close is safe to inherit.
    """
    if day is None:
        return None
    if day.status in (CashDay.Status.OPEN, CashDay.Status.DAY_ENDED):
        return 'UNVERIFIED'
    if day.post_close_cash_activity:
        return 'POST_CLOSE'
    if day.opening_is_provisional:
        return 'PROVISIONAL_CLOSED' if day.status == CashDay.Status.CLOSED else 'PROVISIONAL'
    return None


def _chronological_block(pharmacy, day):
    """Return the *first unresolved* cash day before ``day``.

    The previous implementation inspected only the immediate predecessor.
    That made a downstream CLOSED+provisional day (for example Oct 3) appear
    to be the blocker even though the real unresolved day was older (for
    example Oct 2). Managers should always be sent to the earliest actionable
    cash day first.
    """
    candidates = (CashDay.objects
                  .filter(pharmacy=pharmacy, date__lt=day.date)
                  .order_by('date'))
    for candidate in candidates:
        reason = _cash_day_needs_attention(candidate)
        if not reason:
            continue
        activity = CashLedgerService.post_close_totals(candidate).net if reason == 'POST_CLOSE' else None
        return {'day': candidate, 'reason': reason, 'activity': activity}
    return None


def _opening_handover_candidate(current):
    """Return whether today's untouched physical count can verify yesterday.

    A provisional opening is a handover balance. If the manager counts the
    drawer before any current-day cash activity, that physical count is also a
    valid physical observation of the previous day's closing drawer. We use it
    only when all ledger activity for today is still zero and the observed cash
    exactly matches today's provisional opening. This keeps the shortcut
    auditable and prevents current-day sales/expenses from being attributed to
    yesterday.
    """
    if not current or current.status != CashDay.Status.OPEN or not current.opening_is_provisional:
        return None
    previous = current.carried_from
    if not previous or previous.status not in (CashDay.Status.DAY_ENDED, CashDay.Status.OPEN):
        return None
    # Cash days must be settled in order. Never let today's count close
    # yesterday while an even earlier day is still unresolved (this is how
    # Oct 3 was closed while Oct 2 was still awaiting its recount).
    if _chronological_block(current.pharmacy, previous):
        return None
    count = current.latest_cash_count
    if not count or current.count_is_outdated:
        return None

    # The current day must have had no ledger movement before the count.
    totals = CashLedgerService.totals(current, upto=count.counted_at)
    if totals.net != Decimal('0'):
        return None
    if count.physical_cash != current.opening_cash:
        return None

    return {'current': current, 'previous': previous, 'count': count}


def _heal_meal_payments(pharmacy):
    """V54.43: make sure every EARNED meal of an open day is deducted from the drawer. Never breaks the page."""
    try:
        from .meal_cash import heal_open_day_meal_payments
        heal_open_day_meal_payments(pharmacy.pk)
    except Exception:  # pragma: no cover - self-heal must not take Cash Control down
        import logging
        logging.getLogger(__name__).exception('meal payment self-heal failed')


def _close_page_context(day, form, block=None, pharmacy=None):
    latest = day.latest_cash_count
    current = None
    handover = None
    if pharmacy is not None:
        current = CashDay.objects.filter(pharmacy=pharmacy, date=timezone.localdate()).first()
        handover = _opening_handover_candidate(current) if current else None
        if handover and block and handover['previous'].pk != block['day'].pk:
            handover = None
    reconstruction = None
    if not block and day.status == CashDay.Status.DAY_ENDED and day.date < timezone.localdate():
        try:
            reconstruction = reconstruct_closing_cash(day)
        except Exception:  # pragma: no cover - a helper must never break the close page
            import logging
            logging.getLogger(__name__).exception('cash reconstruction failed')
    return {
        'form': form, 'day': day, 'latest_count': latest,
        'reconstruction': reconstruction,
        'frozen_expected_at': day.day_ended_at if day.status == CashDay.Status.DAY_ENDED else None,
        'can_use_latest': bool(latest and not block and not day.count_is_outdated),
        'pending_previous_day': block['day'] if block else None, 'blocker': block,
        'today': timezone.localdate(),
        'handover': handover,
    }


# --------------------------------------------------------------------------
# Cash control
# --------------------------------------------------------------------------

@admin_required
def cash_control(request):
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    today = timezone.localdate()
    current = CashDay.objects.filter(pharmacy=pharmacy, date=today).first()
    auto_message = None
    blocked_day = None
    block_reason = None
    try:
        # Cash Control is also a reliable midnight rollover boundary. This makes
        # the UI self-healing even when the background scheduler was temporarily
        # unavailable; POS has the same resilient rollover rule.
        current, ended_days = rollover_business_day(pharmacy, request.user, now=timezone.now())
        _heal_meal_payments(pharmacy)
        # Heal stale downstream OPEN provisional openings after an earlier
        # verified close. This is safe and fully audited; CLOSED history is not
        # silently rewritten.
        repair_provisional_opening_chain(pharmacy, request.user, now=timezone.now())
        heal_stale_provisional_closed_days(pharmacy, request.user, now=timezone.now())
        current = CashDay.objects.filter(pharmacy=pharmacy, date=today).first()
        refresh_provisional_opening(pharmacy, current, request.user, request=request)
        if current is not None and current.is_carried_forward and current.opening_is_provisional:
            auto_message = f"Today's opening is provisional at TZS {current.opening_cash:,.0f}. The previous business day ended automatically at midnight and is awaiting physical verification; trading remains active."
    except ValueError as exc:
        # Do not show a technical exception as a scary system warning.
        # Convert it into a guided cash-control action for the manager.
        blocked_day = CashDay.objects.filter(
            pharmacy=pharmacy, date__lt=today, status__in=[CashDay.Status.OPEN, CashDay.Status.DAY_ENDED]
        ).order_by('-date').first()
        block_reason = str(exc)
    # A CLOSED row with post-close activity is an exception, not a reconciled
    # cash day. Paginate only genuinely reconciled days so the UI never labels
    # an unresolved historical close as safe/carry-forward verified.
    closed_days = CashDay.objects.filter(pharmacy=pharmacy, status=CashDay.Status.CLOSED).order_by('-date')
    reconciled_days = [d for d in closed_days if not d.post_close_cash_activity and not d.opening_is_provisional]
    days_page = Paginator(reconciled_days, 10).get_page(request.GET.get('cash_page'))
    activity_history = get_cash_activity_history(pharmacy, limit_days=90)
    activity_page = Paginator(activity_history, 10).get_page(request.GET.get('activity_page'))
    yesterday_activity = next((row for row in activity_history if row['date'] == today - timedelta(days=1)), None)
    previous = (
        CashDay.objects.filter(pharmacy=pharmacy, date__lt=today)
        .order_by('-date')
        .first()
    )
    # The physical cash counted at yesterday's close is the only safe amount
    # to carry into today's opening balance.  Expected yesterday cash is shown
    # separately so managers can see any variance without silently carrying a
    # theoretical amount into the next business day.
    yesterday_actual = previous.actual_closing_cash if previous else None
    yesterday_expected = previous.expected_closing_cash if previous else None
    previous_post_close_activity = previous.post_close_cash_activity if previous else False
    attention = _chronological_block(pharmacy, current) if current else None
    handover = _opening_handover_candidate(current) if current else None
    if attention and attention['reason'] == 'POST_CLOSE':
        pc_day = attention['day']
        if pc_day.closed_at and not CashAuditEvent.objects.filter(
                cash_day=pc_day, action=CashAuditEvent.Action.POST_CLOSE_DETECTED, created_at__gte=pc_day.closed_at).exists():
            record_audit(pc_day, CashAuditEvent.Action.POST_CLOSE_DETECTED, None,
                         old=pc_day.actual_closing_cash, new=f'Net after close TZS {attention["activity"]:,.0f}',
                         reason='Cash transactions were recorded after the final close; reconciliation required.')
    audit_page = Paginator(CashAuditEvent.objects.filter(pharmacy=pharmacy).select_related('actor'), 10).get_page(request.GET.get('audit_page'))
    latest_count = current.latest_cash_count if current else None
    count_is_outdated = current.count_is_outdated if current else False
    count_history = list(current.physical_counts.select_related('counted_by', 'supersedes').order_by('-counted_at')[:6]) if current else []
    employee_cash_rows = employee_cash_reference(pharmacy.pk, today)
    from .models import EmployeeShiftCount
    shift_counts_today = list(EmployeeShiftCount.objects.filter(pharmacy=pharmacy, date=today)
                              .select_related('employee', 'decided_by').order_by('-counted_at'))
    meals_not_deducted = sum((r['meal_not_deducted'] for r in employee_cash_rows), Decimal('0'))
    return render(request, 'expenses/cash.html', {
        'employee_cash_rows': employee_cash_rows, 'meals_not_deducted': meals_not_deducted,
        'shift_counts_today': shift_counts_today,
        'current': current, 'days': days_page, 'days_page': days_page, 'today': today, 'auto_message': auto_message,
        'previous': previous, 'yesterday_actual': yesterday_actual,
        'yesterday_expected': yesterday_expected, 'previous_post_close_activity': previous_post_close_activity, 'blocked_day': blocked_day, 'last_reset': pharmacy.cash_control_resets.select_related('reset_by').first(), 'latest_count': latest_count, 'count_is_outdated': count_is_outdated,
        'block_reason': block_reason, 'attention': attention, 'handover': handover,
        'activity_history': activity_history, 'activity_page': activity_page,
        'yesterday_activity': yesterday_activity, 'count_history': count_history, 'audit_page': audit_page,
    })


@admin_required
@require_http_methods(['GET', 'POST'])
def cash_open(request):
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    form = CashOpenForm(request.POST or None, initial={'date': timezone.localdate()})
    if request.method == 'POST' and form.is_valid():
        target_date = form.cleaned_data['date']
        if CashDay.objects.filter(pharmacy=pharmacy, date=target_date).exists():
            messages.error(request, 'That cash day already exists.')
        else:
            try:
                previous = CashDay.objects.filter(pharmacy=pharmacy, date__lt=target_date).order_by('-date').first()
                # Manual opening is reserved for the first cash-day anchor. Once a
                # branch has history, the system creates the day automatically and
                # may mark its opening provisional until the previous close is verified.
                if previous and previous.status != CashDay.Status.CLOSED:
                    raise ValueError(f'Cash day {target_date} is created automatically while {previous.date} awaits physical verification. Use Daily Cash Control to reconcile the pending day.')
                obj = form.save(commit=False)
                obj.pharmacy = pharmacy
                obj.opened_by = request.user
                if previous and previous.actual_closing_cash is not None:
                    obj.opening_cash = previous.actual_closing_cash
                    obj.opening_source = CashDay.OpeningSource.CARRY_FORWARD
                    obj.carried_from = previous
                    obj.opening_note = form.cleaned_data.get('opening_note') or f'Carried forward from {previous.date} closing cash.'
                else:
                    if not form.cleaned_data.get('physical_count_confirmed'):
                        form.add_error('physical_count_confirmed', 'Confirm that you physically counted the opening cash before starting cash control.')
                        return render(request, 'expenses/cash_open.html', {'form': form})
                    obj.opening_source = CashDay.OpeningSource.MANUAL
                    obj.opening_note = form.cleaned_data.get('opening_note') or 'Opening cash physically counted and verified by the manager.'
                obj.save()
                record_audit(obj, CashAuditEvent.Action.DAY_CREATED, request.user, new=obj.opening_cash, reason=obj.opening_note, request=request)
                source = 'automatically carried forward' if obj.is_carried_forward else 'manually opened'
                messages.success(request, f'Cash day {obj.date} {source}. Opening cash: TZS {obj.opening_cash:,.0f}.')
                return redirect('expenses:cash')
            except ValueError as exc:
                messages.error(request, str(exc))
    return render(request, 'expenses/cash_open.html', {'form': form})


@admin_required
@require_http_methods(['GET', 'POST'])
def cash_opening_adjust(request, pk):
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    day = get_object_or_404(CashDay, pk=pk, pharmacy=pharmacy)
    if day.status == CashDay.Status.CLOSED:
        messages.error(request, 'A closed cash day cannot have its opening balance changed.')
        return redirect('expenses:cash')
    form = CashOpeningAdjustmentForm(request.POST or None, initial={'opening_cash': day.opening_cash})
    if request.method == 'POST' and form.is_valid():
        from .models import CashOpeningAdjustment
        old_amount = day.opening_cash
        new_amount = form.cleaned_data['opening_cash']
        reason = form.cleaned_data['reason'].strip()
        if old_amount == new_amount:
            form.add_error('opening_cash', 'Enter a different opening amount.')
        else:
            CashOpeningAdjustment.objects.create(
                cash_day=day, previous_amount=old_amount, new_amount=new_amount,
                reason=reason, adjusted_by=request.user,
            )
            day.opening_cash = new_amount
            day.opening_source = CashDay.OpeningSource.OVERRIDE
            day.opening_note = f'Manager override: {reason[:220]}'
            day.save(update_fields=['opening_cash', 'opening_source', 'opening_note'])
            record_audit(day, CashAuditEvent.Action.OPENING_ADJUSTED, request.user, old=old_amount, new=new_amount, reason=reason, request=request)
            messages.success(request, 'Opening cash updated and the adjustment was recorded in the audit trail.')
            return redirect('expenses:cash')
    return render(request, 'expenses/cash_adjust.html', {'day': day, 'form': form})


@admin_required
@require_http_methods(['GET', 'POST'])
def cash_count(request, pk):
    """Record a new intraday physical count. This NEVER closes the day."""
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    _heal_meal_payments(pharmacy)
    day = get_object_or_404(CashDay, pk=pk, pharmacy=pharmacy)
    if day.status in [CashDay.Status.DAY_ENDED, CashDay.Status.CLOSED]:
        messages.info(request, 'This business day has ended. Use Count & Close Day to complete the final physical cash verification.')
        return redirect('expenses:cash')
    form = CashCountForm(request.POST or None)
    latest_count = day.latest_cash_count
    if request.method == 'POST' and form.is_valid():
        try:
            count = record_cash_count(day, request.user, form.cleaned_data['physical_cash'], form.cleaned_data.get('note', ''), request=request)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            if count.variance == 0:
                messages.success(request, f'Spot check recorded at TZS {count.physical_cash:,.0f}. It matches expected cash at {count.counted_at:%H:%M}. The day remains open.')
            else:
                direction = 'shortage' if count.variance < 0 else 'overage'
                messages.warning(request, f'Spot check recorded with a TZS {abs(count.variance):,.0f} {direction}. This is preliminary and does not close the day.')
            return redirect('expenses:cash')
    return render(request, 'expenses/cash_count.html', {'day': day, 'form': form, 'latest_count': latest_count, 'correction_mode': False})


@admin_required
@require_http_methods(['POST'])
def mobile_money_check(request, pk):
    """Save the mobile money statement balance (and/or a manual opening balance) for a business day.
    It never changes drawer cash, the cash variance or the day's status."""
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    day = get_object_or_404(CashDay, pk=pk, pharmacy=pharmacy)
    form = MobileMoneyCheckForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Enter a valid mobile money amount (0 or more): the statement balance, the opening balance, or both.')
        return redirect('expenses:cash')
    fields = []
    opening = form.cleaned_data.get('opening_mobile')
    actual = form.cleaned_data.get('actual_mobile_balance')
    if opening is not None:
        day.opening_mobile = opening
        fields.append('opening_mobile')
    if actual is not None:
        day.actual_mobile_balance = actual
        fields += ['actual_mobile_balance']
    day.mobile_check_note = (form.cleaned_data.get('note') or '').strip()
    day.mobile_checked_by = request.user
    day.mobile_checked_at = timezone.now()
    day.save(update_fields=fields + ['mobile_check_note', 'mobile_checked_by', 'mobile_checked_at'])
    if actual is None:
        messages.success(request, f'Opening mobile money balance set to TZS {day.opening_mobile:,.0f}.')
        return redirect('expenses:cash')
    diff = day.mobile_money_difference
    if diff == 0:
        messages.success(request, f'Mobile money balance matches: TZS {day.actual_mobile_balance:,.0f}, as expected.')
    else:
        word = 'shortage' if diff < 0 else 'overage'
        messages.warning(request, f'Mobile money balance saved with a TZS {abs(diff):,.0f} {word} (expected TZS {day.expected_mobile_balance:,.0f}). Drawer cash is not affected.')
    return redirect('expenses:cash')


@admin_required
@require_http_methods(['POST'])
def cash_transfer_create(request):
    """Record money moved between the drawer and the mobile money account (today's business date).
    Total money is unchanged: one side goes up by exactly what the other goes down."""
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    form = CashTransferForm(request.POST)
    if not form.is_valid():
        if 'reference' in form.errors:
            messages.error(request, form.errors['reference'][0])
        else:
            messages.error(request, 'Choose the direction and enter an amount greater than zero.')
        return redirect('expenses:cash')
    today = timezone.localdate()
    day = CashDay.objects.filter(pharmacy=pharmacy, date=today).first()
    if day is None or day.status != CashDay.Status.OPEN:
        messages.error(request, "Open today's cash day first. Transfers are recorded on an open day.")
        return redirect('expenses:cash')
    amount = form.cleaned_data['amount']
    direction = form.cleaned_data['direction']
    with transaction.atomic():
        CashDay.objects.select_for_update().get(pk=day.pk)
        if direction == CashTransfer.Direction.CASH_TO_MOBILE and amount > day.expected_closing_cash:
            messages.error(request, f'The drawer is expected to hold TZS {day.expected_closing_cash:,.0f}. You cannot put TZS {amount:,.0f} into mobile money.')
            return redirect('expenses:cash')
        if direction == CashTransfer.Direction.MOBILE_TO_CASH and amount > day.expected_mobile_balance:
            messages.error(request, f'The mobile money account is expected to hold TZS {day.expected_mobile_balance:,.0f}. You cannot cash out TZS {amount:,.0f}.')
            return redirect('expenses:cash')
        CashTransfer.objects.create(
            pharmacy=pharmacy, transfer_date=today, direction=direction, amount=amount,
            reference=form.cleaned_data.get('reference', '').strip(), note=form.cleaned_data.get('note', '').strip(),
            created_by=request.user)
    messages.success(request, f'Transfer recorded: {CashTransfer.Direction(direction).label} TZS {amount:,.0f}. Total money is unchanged.')
    return redirect('expenses:cash')


@admin_required
@require_http_methods(['GET', 'POST'])
def cash_count_correct(request, pk):
    """Create an auditable replacement for the latest spot check without editing history."""
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    day = get_object_or_404(CashDay, pk=pk, pharmacy=pharmacy)
    latest_count = day.latest_cash_count
    if day.status != CashDay.Status.OPEN:
        messages.info(request, 'This business day is no longer open. Use Count & Close Day or the authorized reopen workflow instead.')
        return redirect('expenses:cash')
    if latest_count is None:
        messages.info(request, 'There is no spot check to correct. Record a new Spot Check instead.')
        return redirect('expenses:cash_count', pk=day.pk)
    form = CashCountCorrectionForm(request.POST or None, initial={'physical_cash': latest_count.physical_cash})
    if request.method == 'POST' and form.is_valid():
        from django.db import transaction
        try:
            with transaction.atomic():
                locked = CashDay.objects.select_for_update().get(pk=day.pk, pharmacy=pharmacy)
                current_latest = locked.latest_cash_count
                if locked.status != CashDay.Status.OPEN:
                    raise ValueError('This cash day changed state before the correction was saved. Refresh Cash Control and follow the current action.')
                if current_latest is None or current_latest.pk != latest_count.pk:
                    raise ValueError('A newer spot check was recorded in another session. Refresh Cash Control before correcting a count.')
                count = record_cash_count(
                    locked, request.user, form.cleaned_data['physical_cash'],
                    form.cleaned_data.get('note', ''), supersedes=current_latest,
                    correction_reason=form.cleaned_data['correction_reason'], request=request,
                )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            if count.variance == 0:
                messages.success(request, f'Spot check corrected. New verified spot check: TZS {count.physical_cash:,.0f}. The original TZS {latest_count.physical_cash:,.0f} remains in the audit trail.')
            else:
                direction = 'shortage' if count.variance < 0 else 'overage'
                messages.warning(request, f'Spot check corrected. Current preliminary difference is TZS {abs(count.variance):,.0f} {direction}; the day remains open.')
            return redirect('expenses:cash')
    return render(request, 'expenses/cash_count.html', {
        'day': day, 'form': form, 'latest_count': latest_count, 'correction_mode': True,
    })


@admin_required
@require_http_methods(['GET', 'POST'])
def cash_reopen(request, pk):
    """Open the audited CLOSED -> OPEN correction workflow.

    GET is deliberately read-only and always renders the review page for a
    matching closed cash day. POST is the only operation that mutates state.
    This makes the action reliable from a normal anchor/link and avoids the
    previous behaviour where a state check could redirect before the manager
    ever saw the reason form.
    """
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')

    day = get_object_or_404(CashDay, pk=pk, pharmacy=pharmacy)

    if day.status != CashDay.Status.CLOSED:
        messages.info(request, f'Cash day {day.date} is not currently closed, so it cannot be reopened.')
        return redirect('expenses:cash')

    reason = request.POST.get('reason', '').strip() if request.method == 'POST' else ''
    post_close = day.post_close_cash_activity
    provisional_opening = bool(day.opening_is_provisional)
    reopenable_exception = post_close or provisional_opening

    if request.method == 'POST':
        if not reopenable_exception:
            messages.info(request, 'This cash day has no unresolved correction exception. Refresh Cash Control and continue from the current state.')
            return redirect('expenses:cash')

        if len(reason) < 12:
            messages.error(request, 'Enter a clear reason (at least 12 characters) for the audit trail.')
        else:
            try:
                with transaction.atomic():
                    locked = CashDay.objects.select_for_update().get(pk=day.pk, pharmacy=pharmacy)
                    if locked.status != CashDay.Status.CLOSED:
                        raise ValueError('This cash day has already been reopened or resolved. Refresh Cash Control.')
                    if not locked.post_close_cash_activity and not locked.opening_is_provisional:
                        raise ValueError('No unresolved cash-control exception remains. Refresh Cash Control.')

                    later_days = CashDay.objects.filter(
                        pharmacy=pharmacy, date__gt=locked.date,
                    ).order_by('date')
                    blocking_later = next(
                        (candidate for candidate in later_days
                         if candidate.status == CashDay.Status.CLOSED
                         and not candidate.opening_is_provisional
                         and not candidate.post_close_cash_activity),
                        None,
                    )
                    if blocking_later:
                        raise ValueError(
                            f'Cannot reopen {locked.date} while a later verified cash day ({blocking_later.date}) exists. '
                            'Resolve the later day through its authorized correction workflow first.'
                        )

                    if locked.actual_closing_cash is None or locked.closed_by is None or locked.closed_at is None:
                        raise ValueError(
                            'This record has no complete verified closing snapshot. Contact support before changing it.'
                        )

                    # If this day was closed while its opening was provisional,
                    # reopening creates a safe correction point. Once the previous
                    # day is verified, the opening is rebased from that physical
                    # closing amount and the manager must recount this day before
                    # closing it again. The old close remains immutable.
                    previous = (CashDay.objects.filter(
                        pharmacy=pharmacy, date__lt=locked.date
                    ).order_by('-date').first())
                    if locked.opening_is_provisional:
                        if not previous or previous.status != CashDay.Status.CLOSED or previous.actual_closing_cash is None:
                            raise ValueError(
                                'This day still depends on an earlier unverified cash day. Resolve that earlier day first.'
                            )
                        from .models import CashOpeningAdjustment
                        if locked.opening_cash != previous.actual_closing_cash:
                            old_opening = locked.opening_cash
                            locked.opening_cash = previous.actual_closing_cash
                            CashOpeningAdjustment.objects.create(
                                cash_day=locked, previous_amount=old_opening,
                                new_amount=locked.opening_cash,
                                reason=(f'Opening corrected during audited reopen after {previous.date} was physically verified. '
                                        f'Provisional opening TZS {old_opening:,.2f} replaced with verified closing TZS {locked.opening_cash:,.2f}.'),
                                adjusted_by=request.user,
                            )
                            record_audit(locked, CashAuditEvent.Action.OPENING_RECONCILED, request.user,
                                         old=old_opening, new=locked.opening_cash,
                                         reason=f'Rebased during audited reopen from {previous.date} verified closing.', request=request)
                        locked.opening_is_provisional = False
                        locked.opening_provisional_note = ''
                        locked.opening_reconciled_at = timezone.now()
                        locked.opening_source = CashDay.OpeningSource.CARRY_FORWARD
                        locked.carried_from = previous

                    audit_row = CashDayReopenAudit.objects.create(
                        cash_day=locked,
                        previous_opening_cash=locked.opening_cash,
                        previous_closing_cash=locked.actual_closing_cash,
                        previous_expected_cash=locked.expected_closing_cash,
                        previous_variance=locked.cash_difference or Decimal('0'),
                        previous_closed_at=locked.closed_at,
                        previous_closed_by=locked.closed_by,
                        reason=reason,
                        reopened_by=request.user,
                    )

                    locked.status = CashDay.Status.OPEN
                    locked.actual_closing_cash = None
                    locked.physical_counted_by = None
                    locked.physical_counted_at = None
                    locked.variance_reason = ''
                    locked.closing_note = ''
                    locked.closed_by = None
                    locked.closed_at = None
                    locked.closed_automatically = False
                    locked.auto_close_reason = ''
                    locked.day_ended_at = None
                    locked.day_ended_automatically = False
                    locked.save(update_fields=[
                        'status', 'opening_cash', 'opening_is_provisional', 'opening_provisional_note', 'opening_reconciled_at', 'opening_source', 'carried_from', 'actual_closing_cash', 'physical_counted_by',
                        'physical_counted_at', 'variance_reason', 'closing_note',
                        'closed_by', 'closed_at', 'closed_automatically',
                        'auto_close_reason', 'day_ended_at', 'day_ended_automatically',
                    ])
                    record_audit(locked, CashAuditEvent.Action.REOPENED, request.user,
                                 old=audit_row.previous_closing_cash, new='Reopened for recount', reason=reason, request=request)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    f'Cash day {day.date} reopened with an immutable audit record. '
                    'Review the source activity, recount the physical drawer, and close the day again.'
                )
                return redirect('expenses:cash_close', pk=day.pk)

    # The GET review page remains available even if the exception has been
    # resolved in another session; it simply explains the current state instead
    # of producing a confusing blank/redirect loop.
    return render(request, 'expenses/cash_reopen.html', {
        'day': day,
        'reason': reason,
        'post_close_activity': post_close,
        'provisional_opening': provisional_opening,
        'reopenable_exception': reopenable_exception,
        'summary': CashLedgerService.post_close_summary(day) if post_close else None,
    })


@admin_required
@require_http_methods(['POST'])
def cash_verify_previous_from_opening(request, pk):
    """Use an untouched next-day opening count to verify the previous close.

    This is the professional handover path for the common case where midnight
    has already opened the next day provisionally and the manager counts the
    inherited drawer before making any new cash movement.
    """
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    current = get_object_or_404(CashDay, pk=pk, pharmacy=pharmacy)

    candidate = _opening_handover_candidate(current)
    if not candidate:
        messages.error(
            request,
            'The current opening count cannot verify the previous cash day. Confirm that the opening is provisional, no current-day cash activity has occurred, and the latest physical count still matches the provisional opening.'
        )
        return redirect('expenses:cash')

    previous = candidate['previous']
    count = candidate['count']
    try:
        with transaction.atomic():
            locked_current = CashDay.objects.select_for_update().get(pk=current.pk)
            locked_previous = CashDay.objects.select_for_update().get(pk=previous.pk)
    
            # Re-check every safety condition inside the transaction.
            if locked_current.status != CashDay.Status.OPEN or not locked_current.opening_is_provisional:
                raise ValueError('The current provisional opening changed before verification. Refresh Cash Control and review the latest state.')
            if locked_previous.status == CashDay.Status.CLOSED:
                messages.info(request, f'{locked_previous.date} is already verified.')
                return redirect('expenses:cash')
    
            latest = locked_current.latest_cash_count
            if latest is None or latest.pk != count.pk or locked_current.count_is_outdated:
                raise ValueError('A newer count or cash movement was recorded. The handover count is no longer valid; perform a new count.')
    
            totals = CashLedgerService.totals(locked_current, upto=latest.counted_at)
            if totals.net != Decimal('0') or latest.physical_cash != locked_current.opening_cash:
                raise ValueError('The current day has cash movement or the drawer no longer matches the provisional opening. The previous day must be closed with its own final count.')
    
            closing_at = latest.counted_at
            expected = locked_previous.expected_cash_as_of(closing_at)
            physical = latest.physical_cash
            difference = clean_variance(physical - expected)
            note = 'Verified from the next business day handover count before any current-day cash activity.'
            if difference != 0:
                note += f' Variance: TZS {abs(difference):,.0f}.'
    
            locked_previous.actual_closing_cash = physical
            locked_previous.physical_counted_by = latest.counted_by
            locked_previous.physical_counted_at = closing_at
            locked_previous.variance_reason = note
            locked_previous.closing_note = note
            locked_previous.status = CashDay.Status.CLOSED
            locked_previous.closed_by = request.user
            locked_previous.closed_at = closing_at
            locked_previous.closed_automatically = False
            locked_previous.auto_close_reason = 'Verified from an untouched next-day handover count.'
            locked_previous.save(update_fields=[
                'actual_closing_cash','physical_counted_by','physical_counted_at',
                'variance_reason','closing_note','status','closed_by','closed_at',
                'closed_automatically','auto_close_reason'
            ])
    
            from .models import CashCount
            CashCount.objects.create(
                cash_day=locked_previous, counted_by=latest.counted_by, counted_at=closing_at,
                physical_cash=physical, expected_cash=expected, variance=difference,
                note=note, confirmed=True,
            )
            create_closing_snapshot(locked_previous, request.user, expected=expected,
                                    physical=physical, variance=difference, note=note,
                                    closed_at=closing_at)
            record_audit(
                locked_previous, CashAuditEvent.Action.CASH_CLOSED, request.user,
                old=expected, new=physical,
                reason=f'Handover verification from {locked_current.date} opening count. {note}',
                request=request, at=closing_at,
            )
            reconcile_provisional_openings(pharmacy, locked_previous, request.user, now=timezone.now())
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('expenses:cash')

    if difference == 0:
        messages.success(
            request,
            f'{locked_previous.date} was verified successfully. The handover count of TZS {physical:,.0f} matched expected cash, and {locked_current.date} is now using a verified opening balance.'
        )
    else:
        messages.warning(
            request,
            f'{locked_previous.date} was verified with a TZS {abs(difference):,.0f} variance. The variance was preserved in the audit trail.'
        )
    return redirect('expenses:cash')


@admin_required
@require_http_methods(['GET', 'POST'])
def cash_close(request, pk):
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    _heal_meal_payments(pharmacy)
    day = get_object_or_404(CashDay, pk=pk, pharmacy=pharmacy)

    # Final close is only allowed after the business date has ended. During the
    # current business date managers may use Spot Check, but must not finalize
    # the day while POS trading is still possible. This is the primary safeguard
    # against post-close cash sales creating historical reconciliation exceptions.
    if day.date >= timezone.localdate() and day.status != CashDay.Status.CLOSED:
        messages.warning(
            request,
            f'{day.date} is still the current business date. Use Spot Check during trading; '
            f'Final Close becomes available after the business date ends.'
        )
        return redirect('expenses:cash')

    if day.status == CashDay.Status.CLOSED:
        messages.info(request, 'This cash day is already closed.')
        return redirect('expenses:cash')

    # A prior version could leave an OPEN day marked provisional even after its
    # predecessor was physically verified. Repair that stale handover before
    # deciding whether this close page is blocked. The repair is append-only and
    # does not alter any historical closing record.
    repair_provisional_opening_chain(pharmacy, request.user, now=timezone.now())
    heal_stale_provisional_closed_days(pharmacy, request.user, now=timezone.now())
    day.refresh_from_db()
    block = _chronological_block(pharmacy, day)
    if request.method == 'GET' and block:
        return render(request, 'expenses/cash_close.html', _close_page_context(day, CashCloseForm(instance=day), block, pharmacy))

    if request.method == 'GET' and not block and request.GET.get('use_reconstruction') == '1':
        suggestion = reconstruct_closing_cash(day)
        if suggestion:
            prefill = CashCloseForm(instance=day, initial={
                'actual_closing_cash': suggestion['amount'], 'closing_note': suggestion['note']})
            return render(request, 'expenses/cash_close.html', _close_page_context(day, prefill, block, pharmacy))

    data = request.POST
    if request.method == 'POST' and request.POST.get('use_latest_count') == '1':
        latest = day.latest_cash_count
        if latest and not day.count_is_outdated:
            data = request.POST.copy()
            data['actual_closing_cash'] = str(latest.physical_cash)
            data['physical_count_confirmed'] = 'on'
        else:
            messages.error(request, 'The earlier count is outdated because cash moved after it. Please count the drawer again.')
            return render(request, 'expenses/cash_close.html', _close_page_context(day, CashCloseForm(instance=day), block, pharmacy))
    form = CashCloseForm(data or None, instance=day)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            day = CashDay.objects.select_for_update().get(pk=day.pk)
            if day.status == CashDay.Status.CLOSED:
                messages.info(request, 'This cash day was already closed by another session.')
                return redirect('expenses:cash')

            repair_provisional_opening_chain(pharmacy, request.user, now=timezone.now())
            heal_stale_provisional_closed_days(pharmacy, request.user, now=timezone.now())
            day.refresh_from_db()
            block = _chronological_block(pharmacy, day)
            if block:
                messages.error(request, f"Cash day {block['day'].date} must be settled before {day.date} can be finally closed.")
                return render(request, 'expenses/cash_close.html', _close_page_context(day, CashCloseForm(instance=day), block, pharmacy))

            form = CashCloseForm(data, instance=day)
            if not form.is_valid():
                return render(request, 'expenses/cash_close.html', _close_page_context(day, form, pharmacy=pharmacy))

            pending = Expense.objects.filter(
                pharmacy=pharmacy, expense_date=day.date, status=Expense.Status.PENDING,
                payment_source=Expense.PaymentSource.CASH_SALES,
            ).count()
            if pending:
                messages.error(request, f'{pending} sales-cash expense(s) for {day.date} are still pending. Approve or reject them before closing the cash day.')
                return render(request, 'expenses/cash_close.html', _close_page_context(day, form, pharmacy=pharmacy))

            from sales.models import SaleReturnRequest, Sale
            pending_returns = SaleReturnRequest.objects.filter(
                pharmacy=pharmacy,
                sale__payment_method__in=[Sale.PaymentMethod.CASH, Sale.PaymentMethod.SPLIT],
                status=SaleReturnRequest.Status.PENDING,
            ).filter(
                Q(sale__business_date=day.date)
            ).count()
            if pending_returns:
                messages.error(request, f'{pending_returns} cash return request(s) are still pending. Approve or reject them before closing the cash day.')
                return render(request, 'expenses/cash_close.html', _close_page_context(day, form, pharmacy=pharmacy))

            obj = form.save(commit=False)
            closing_at = timezone.now()
            expected = obj.expected_cash_as_of(closing_at)
            difference = clean_variance(obj.actual_closing_cash - expected)
            obj.physical_counted_by = request.user
            obj.physical_counted_at = closing_at
            obj.variance_reason = (obj.closing_note or '').strip()
            obj.status = CashDay.Status.CLOSED
            obj.closed_by = request.user
            obj.closed_at = closing_at
            obj.closed_automatically = False
            obj.auto_close_reason = 'Manager physically counted the cash; system calculated expected cash and variance.'
            if not obj.closing_note:
                if difference == 0:
                    obj.closing_note = 'Physical cash matched system expected cash.'
                else:
                    direction = 'short' if difference < 0 else 'over'
                    obj.closing_note = f'Physical cash was TZS {abs(difference):,.0f} {direction} versus expected cash.'
            obj.save(update_fields=['actual_closing_cash','physical_counted_by','physical_counted_at','variance_reason','closing_note','status','closed_by','closed_at','closed_automatically','auto_close_reason'])

            from .models import CashCount
            CashCount.objects.create(
                cash_day=obj, counted_by=request.user, counted_at=closing_at,
                physical_cash=obj.actual_closing_cash, expected_cash=expected,
                variance=difference, note=obj.closing_note or 'Final closing count.', confirmed=True,
            )

            create_closing_snapshot(obj, request.user, expected=expected, physical=obj.actual_closing_cash,
                                    variance=difference, note=obj.closing_note, closed_at=closing_at)
            record_audit(obj, CashAuditEvent.Action.CASH_CLOSED, request.user, old=expected, new=obj.actual_closing_cash,
                         reason=f'Variance {"-" if difference < 0 else ""}TZS {abs(difference):,.0f}. {obj.closing_note}', request=request, at=closing_at)

            rebased = reconcile_provisional_openings(pharmacy, obj, request.user, now=closing_at)

            next_day = day.date + timedelta(days=1)
            if next_day <= timezone.localdate():
                try:
                    ensure_cash_day_for_date(pharmacy, request.user, next_day)
                except ValueError:
                    pass

        if difference == 0:
            messages.success(request, f'Cash for {obj.date} was verified and reconciled. TZS {obj.actual_closing_cash:,.0f} is now the verified closing balance.' + (f' {rebased} downstream provisional opening balance(s) were automatically reconciled.' if rebased else ''))
        else:
            messages.warning(request, f'Cash for {obj.date} was closed with a TZS {abs(difference):,.0f} {"shortage" if difference < 0 else "overage"}. The physical count, variance and manager explanation were saved in the audit trail.')
        return redirect('expenses:cash')
    return render(request, 'expenses/cash_close.html', _close_page_context(day, form, pharmacy=pharmacy))



@admin_required
def cash_activity_detail(request, pk):
    """Transaction-level drill-down. scope=post_close (default) or day."""
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    day = get_object_or_404(CashDay, pk=pk, pharmacy=pharmacy)
    scope = request.GET.get('scope', 'post_close')
    if scope == 'day':
        entries = CashLedgerService.entries(day)
        totals = CashLedgerService.totals(day)
        title = f'All cash activity — {day.date}'
    else:
        scope = 'post_close'
        entries = CashLedgerService.entries(day, after=day.closed_at) if day.closed_at else []
        totals = CashLedgerService.post_close_totals(day)
        title = f'Post-close cash activity — {day.date}'
    return render(request, 'expenses/cash_activity_detail.html', {
        'day': day, 'entries': entries, 'totals': totals, 'scope': scope, 'title': title,
        'summary': CashLedgerService.post_close_summary(day),
    })


@admin_required
@require_http_methods(['GET', 'POST'])
def cash_reset_cycle(request):
    """Professional, two-step, fully audited cash-control cycle reset.

    Step 1 (form): choose date + method (known opening cash, or physical count now
    from which the opening is derived) + reason.
    Step 2 (review): exact before/after figures, signed token, explicit confirmation.
    On confirm, the plan is recomputed against live data; if cash movements changed
    during review the manager must review again. Nothing is ever deleted.
    """
    from datetime import date
    from decimal import Decimal, InvalidOperation
    from .models import CashControlReset
    from .services import (build_cash_reset_plan, make_cash_reset_token, apply_cash_cycle_reset,
                           cash_reset_snapshot, CashResetStale)
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    today = timezone.localdate()
    values = {'start_date': today.isoformat(), 'method': 'COUNT_NOW', 'amount': '', 'reason': ''}
    plan = token = None

    def _context():
        snap = cash_reset_snapshot(pharmacy, today)
        return {
            'today': today, 'values': values, 'plan': plan, 'token': token,
            'snap': snap, 'day': snap['day'], 'totals': snap['totals'],
            'history': CashControlReset.objects.filter(pharmacy=pharmacy).select_related('reset_by')[:8],
            'can_reset_today': (snap['day'] is None) or snap['day'].status == CashDay.Status.OPEN,
        }

    if request.method == 'POST':
        step = request.POST.get('step', 'review')
        if step == 'confirm':
            token = request.POST.get('token', '')
            try:
                if request.POST.get('confirm_reset') != 'yes':
                    raise ValueError('Tick the confirmation box to continue.')
                reset, applied = apply_cash_cycle_reset(pharmacy, request.user, token, request=request)
            except CashResetStale as exc:
                messages.warning(request, str(exc))
                return redirect('expenses:cash_reset_cycle')
            except (ValueError, InvalidOperation) as exc:
                messages.error(request, str(exc))
                return redirect('expenses:cash_reset_cycle')
            messages.success(request, f'New cash-control cycle #{reset.pk} started for {applied["start_date"]}. '
                                      f'Opening cash TZS {applied["opening"]:,.0f}; expected cash now TZS {applied["expected_after"]:,.0f}. '
                                      'Earlier records were retained and the change is in the audit trail.')
            return redirect('expenses:cash')
        # step == review
        values.update({k: request.POST.get(k, '').strip() for k in ('start_date', 'method', 'amount', 'reason')})
        try:
            start_date = date.fromisoformat(values['start_date'])
            try:
                amount = Decimal(values['amount'].replace(',', ''))
            except InvalidOperation:
                raise ValueError('Enter a valid amount in TZS.')
            if not amount.is_finite():
                raise ValueError('Enter a valid amount in TZS.')
            if len(values['reason']) < 12:
                raise ValueError('Enter a clear reason of at least 12 characters for the audit record.')
            plan = build_cash_reset_plan(pharmacy, start_date, values['method'], amount)
            token = make_cash_reset_token(pharmacy, request.user, plan, values['reason'])
        except (ValueError, InvalidOperation) as exc:
            messages.error(request, str(exc))
            plan = token = None
    return render(request, 'expenses/cash_reset.html', _context())



# ---------------------------------------------------------------------------
# Drawer <-> mobile money transfer history (preview + Excel download)
# ---------------------------------------------------------------------------
def _transfer_filters(request):
    def d(raw):
        try:
            return datetime.strptime((raw or '').strip(), '%Y-%m-%d').date()
        except ValueError:
            return None
    direction = request.GET.get('direction', '')
    if direction not in {c for c, _ in CashTransfer.Direction.choices}:
        direction = ''
    return {'q': (request.GET.get('q') or '').strip()[:100], 'date_from': d(request.GET.get('from')),
            'date_to': d(request.GET.get('to')), 'direction': direction}


def _transfer_qs(pharmacy, f):
    qs = CashTransfer.objects.filter(pharmacy=pharmacy).select_related('created_by')
    if f['direction']:
        qs = qs.filter(direction=f['direction'])
    if f['date_from']:
        qs = qs.filter(transfer_date__gte=f['date_from'])
    if f['date_to']:
        qs = qs.filter(transfer_date__lte=f['date_to'])
    if f['q']:
        qs = qs.filter(Q(reference__icontains=f['q']) | Q(note__icontains=f['q']))
    return qs.order_by('-created_at', '-pk')


def _transfer_totals(qs):
    to_cash = qs.filter(direction=CashTransfer.Direction.MOBILE_TO_CASH).aggregate(v=Sum('amount'))['v'] or Decimal('0')
    to_mobile = qs.filter(direction=CashTransfer.Direction.CASH_TO_MOBILE).aggregate(v=Sum('amount'))['v'] or Decimal('0')
    return {'to_cash': to_cash, 'to_mobile': to_mobile, 'count': qs.count()}


@admin_required
def cash_transfer_history(request):
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    f = _transfer_filters(request)
    qs = _transfer_qs(pharmacy, f)
    page_obj = Paginator(qs, 40).get_page(request.GET.get('page'))
    qd = request.GET.copy(); qd.pop('page', None)
    return render(request, 'expenses/cash_transfers.html', {
        'f': f, 'page_obj': page_obj, 'totals': _transfer_totals(qs), 'querystring': qd.urlencode(),
        'quick': _quick_ranges(f['date_from'], f['date_to']),
        'filtered': any(f.values()),
    })


@admin_required
def cash_transfer_export(request):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    pharmacy = _pharmacy(request)
    if not pharmacy:
        return redirect('dashboard:home')
    f = _transfer_filters(request)
    qs = _transfer_qs(pharmacy, f)
    totals = _transfer_totals(qs)
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Transfers'
    headers = ['Date', 'Time', 'Direction', 'Amount (TZS)', 'Transaction reference', 'Reason / note', 'Recorded by']
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF'); c.fill = PatternFill('solid', fgColor='0F172A')
        c.alignment = Alignment(vertical='center')
    for t in qs[:20000]:
        local = timezone.localtime(t.created_at)
        ws.append([local.date(), local.time().replace(microsecond=0), t.get_direction_display(), t.amount,
                   t.reference, t.note, (t.created_by.get_full_name() or t.created_by.username) if t.created_by else ''])
    last = ws.max_row
    for row in ws.iter_rows(min_row=2, max_row=last):
        row[0].number_format = 'dd mmm yyyy'; row[1].number_format = 'hh:mm:ss'; row[3].number_format = '#,##0.00'
    ws.freeze_panes = 'A2'; ws.auto_filter.ref = f'A1:G{last}'
    for i, w in enumerate([14, 10, 30, 16, 26, 36, 22], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    info = wb.create_sheet('Summary')
    for row in [
        ('Drawer / mobile money transfers', ''), ('Branch', pharmacy.name),
        ('Generated', timezone.localtime().strftime('%d %b %Y, %H:%M:%S') + ' (East Africa Time)'),
        ('Generated by', request.user.get_full_name() or request.user.username), ('', ''),
        ('Date from', f['date_from'].strftime('%d %b %Y') if f['date_from'] else 'Beginning'),
        ('Date to', f['date_to'].strftime('%d %b %Y') if f['date_to'] else 'Today'), ('', ''),
        ('Transfers', totals['count']), ('Mobile money -> Drawer (TZS)', float(totals['to_cash'])),
        ('Drawer -> Mobile money (TZS)', float(totals['to_mobile'])),
    ]:
        info.append(list(row))
    info['A1'].font = Font(bold=True, size=14)
    info['B10'].number_format = '#,##0.00'; info['B11'].number_format = '#,##0.00'
    info.column_dimensions['A'].width = 32; info.column_dimensions['B'].width = 44
    buf = io.BytesIO(); wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="cash_transfers_{timezone.localtime():%Y%m%d_%H%M}.xlsx"'
    return resp



# ── Employee blind drawer count (V97.44) ─────────────────────────────────────────────────────────────────────────
def _employee_only(request):
    from django.core.exceptions import PermissionDenied
    if not request.user.is_employee_role() or request.pharmacy is None:
        raise PermissionDenied('Only employees submit a drawer count.')


@employee_or_admin_required
@require_http_methods(['POST'])
def shift_count_submit(request):
    from . import shift_count
    _employee_only(request)
    try:
        count = shift_count.submit_count(request.pharmacy, request.user, timezone.localdate(), request.POST.get('counted_cash'))
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    else:
        if count.difference == 0:
            messages.success(request, 'Your count matches the system. Thank you.')
        else:
            messages.warning(request, 'Your count differs from the system. Please write a short explanation below.')
    return redirect('dashboard:home')


@employee_or_admin_required
@require_http_methods(['POST'])
def shift_count_reason(request, pk):
    from . import shift_count
    from .models import EmployeeShiftCount
    _employee_only(request)
    count = get_object_or_404(EmployeeShiftCount, pk=pk, employee=request.user, pharmacy=request.pharmacy)
    try:
        shift_count.add_reason(count, request.user, request.POST.get('reason'))
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    else:
        messages.success(request, 'Explanation sent to your manager.')
    return redirect('dashboard:home')


@admin_required
@require_http_methods(['POST'])
def shift_count_decide(request, pk):
    from . import shift_count
    from .models import EmployeeShiftCount
    count = get_object_or_404(EmployeeShiftCount, pk=pk, pharmacy=request.pharmacy)
    accept = request.POST.get('decision') == 'accept'
    if not accept and request.POST.get('decision') != 'recount':
        messages.error(request, 'Unknown decision.')
        return redirect('expenses:cash')
    try:
        shift_count.decide(count, request.user, accept, request.POST.get('note'))
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    else:
        messages.success(request, 'Count accepted.' if accept else 'Recount requested from the employee.')
    return redirect('expenses:cash')
