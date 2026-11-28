"""Sales reconciliation + integrity checks (V54.91).

Why this exists
---------------
The cash ledger counts a cash sale at its ORIGINAL total (refunds are deducted
separately), while "Total sales" is NET of voids and returns. Both are correct,
but a report that shows one next to the other looks wrong unless the bridge is
printed. `sales_reconciliation` builds that bridge from the same data, and
`run_integrity_checks` proves it balances - and repairs the few things that are
safe to repair automatically.

    Gross sales (original totals, all sales made in the period)
  - Voided sales
  - Partial returns
  = Net sales            (must equal the dashboard "Total sales")
"""
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce, TruncDate

from expenses.ledger import cash_refund_share
from sales.models import Debt, DebtPayment, Sale, SaleItem, SaleReturnRequest

ZERO = Decimal('0')
MONEY = DecimalField(max_digits=16, decimal_places=2)


def _sum(expr):
    return Coalesce(Sum(expr), Value(ZERO, output_field=MONEY), output_field=MONEY)


def sales_reconciliation(pharmacy, start, end):
    """Bridge from gross sales to net sales for sales MADE in [start, end]."""
    items = SaleItem.objects.filter(sale__pharmacy=pharmacy, sale__business_date__range=(start, end))
    original = ExpressionWrapper(F('unit_price') * (F('quantity') + F('returned_quantity')), output_field=MONEY)
    returned = ExpressionWrapper(F('unit_price') * F('returned_quantity'), output_field=MONEY)
    net = ExpressionWrapper(F('unit_price') * F('quantity'), output_field=MONEY)

    gross = items.aggregate(v=_sum(original))['v']
    voided = items.filter(sale__status=Sale.Status.VOIDED).aggregate(v=_sum(original))['v']
    partial = items.exclude(sale__status=Sale.Status.VOIDED).aggregate(v=_sum(returned))['v']
    net_sales = items.exclude(sale__status=Sale.Status.VOIDED).aggregate(v=_sum(net))['v']

    by_method = OrderedDict()
    for row in items.exclude(sale__payment_method=Sale.PaymentMethod.SPLIT).values('sale__payment_method').annotate(g=_sum(original)):
        by_method[row['sale__payment_method']] = row['g']
    # Cash + Mobile Money sales are shared out by what the customer actually handed over.
    for sale in (Sale.objects.filter(pharmacy=pharmacy, business_date__range=(start, end),
                                     payment_method=Sale.PaymentMethod.SPLIT)):
        by_method[Sale.PaymentMethod.CASH] = by_method.get(Sale.PaymentMethod.CASH, ZERO) + sale.cash_paid
        by_method[Sale.PaymentMethod.MOBILE_MONEY] = by_method.get(Sale.PaymentMethod.MOBILE_MONEY, ZERO) + sale.mobile_paid
    return {
        'gross': gross, 'voided': voided, 'partial_returns': partial,
        'net': gross - voided - partial, 'net_check': net_sales,
        'balanced': (gross - voided - partial) == net_sales,
        'gross_cash': by_method.get(Sale.PaymentMethod.CASH, ZERO),
        'gross_mobile': by_method.get(Sale.PaymentMethod.MOBILE_MONEY, ZERO),
        'gross_credit': by_method.get(Sale.PaymentMethod.DEBT, ZERO),
    }


def refund_rows(pharmacy, start, end):
    """Every approved return/void DECIDED in the period, with what it was and where the money went."""
    qs = (SaleReturnRequest.objects
          .filter(pharmacy=pharmacy, status=SaleReturnRequest.Status.APPROVED,
                  decided_at__date__range=(start, end))
          .select_related('sale', 'decided_by', 'requested_by').order_by('decided_at'))
    rows = []
    for rr in qs:
        from django.utils import timezone
        decided = timezone.localtime(rr.decided_at)
        who = rr.decided_by or rr.requested_by
        rows.append({
            'decided_at': decided, 'date': decided.date(),
            'sale_number': rr.sale.sale_number or rr.sale_id,
            'sale_date': rr.sale.business_date,
            'same_day': rr.sale.business_date == decided.date(),
            'kind': 'Full void' if rr.is_full_void else 'Partial return',
            'method': rr.sale.payment_summary,
            'value': rr.refund_amount, 'refund_due': rr.refund_due,
            'by': (who.get_full_name() or who.get_username()) if who else '-',
            'reason': (rr.reason or '').strip()[:120],
            'drawer': rr.sale.payment_method in (Sale.PaymentMethod.CASH, Sale.PaymentMethod.SPLIT),
            'drawer_amount': cash_refund_share(rr) if rr.sale.payment_method in (
                Sale.PaymentMethod.CASH, Sale.PaymentMethod.SPLIT) else ZERO,
        })
    return rows


# --------------------------------------------------------------------------
# Integrity checks (+ safe automatic repair)
# --------------------------------------------------------------------------

@dataclass
class Issue:
    code: str
    severity: str        # info | warning | danger
    message: str
    fixed: bool = False


def _status_mismatches(pharmacy):
    """Sales whose status disagrees with their line quantities (the quantities are the truth)."""
    totals = (SaleItem.objects.filter(sale__pharmacy=pharmacy)
              .values('sale_id').annotate(q=Sum('quantity'), r=Sum('returned_quantity')))
    qty = {t['sale_id']: (t['q'] or 0, t['r'] or 0) for t in totals}
    wrong = []
    for sale in Sale.objects.filter(pharmacy=pharmacy).only('id', 'status', 'sale_number'):
        q, r = qty.get(sale.pk, (0, 0))
        if q == 0 and r > 0:
            expected = Sale.Status.VOIDED
        elif r > 0:
            expected = Sale.Status.PARTIALLY_RETURNED
        else:
            expected = Sale.Status.COMPLETED
        if sale.status != expected:
            wrong.append((sale, expected))
    return wrong


def run_integrity_checks(pharmacy, start, end, fix=False):
    """Return a list of Issue. With fix=True only the provably safe repair is applied
    (sale status derived from its own quantities); money mismatches are NEVER auto-edited."""
    from .views import _profit_summary
    from .range_report import period_cash_control
    issues = []

    recon = sales_reconciliation(pharmacy, start, end)
    fin = _profit_summary(pharmacy, start, end)
    if not recon['balanced']:
        issues.append(Issue('RECON_BRIDGE', 'danger',
                            f"Gross - voids - returns ({recon['net']:,.0f}) differs from net line total "
                            f"({recon['net_check']:,.0f}). Check sale items for corrupted quantities."))
    if recon['net_check'] != fin['sales']:
        issues.append(Issue('RECON_DASHBOARD', 'danger',
                            f"Report net sales ({recon['net_check']:,.0f}) differs from dashboard "
                            f"({fin['sales']:,.0f})."))

    cash = period_cash_control(pharmacy, start, end)
    non_cash_net = fin['mobile_sales'] + fin['credit_sales'] + fin['credit_deposits']
    drawer_gap = (cash['cash_sales'] + non_cash_net) - fin['sales']
    refunds_all = sum((r['refund_due'] for r in refund_rows(pharmacy, start, end)), ZERO)
    if drawer_gap != 0:
        issues.append(Issue('BRIDGE_NOTE', 'info',
                            f"Cash + non-cash receipts exceed net sales by {drawer_gap:,.0f}: this is voided/returned "
                            f"value already shown in the sales bridge (refunds decided in period: {refunds_all:,.0f})."))
    if cash['variance'] not in (None, ZERO):
        issues.append(Issue('CASH_VARIANCE', 'danger', f"Cash difference of {cash['variance']:,.0f} on the last day."))

    stale = _status_mismatches(pharmacy)
    for sale, expected in stale:
        issue = Issue('SALE_STATUS', 'warning',
                      f"Sale #{sale.sale_number or sale.pk} is {sale.status} but its items say {expected}.")
        if fix:
            Sale.objects.filter(pk=sale.pk).update(status=expected)
            issue.fixed = True
        issues.append(issue)

    for sale in Sale.objects.filter(pharmacy=pharmacy, business_date__range=(start, end),
                                    payment_method=Sale.PaymentMethod.SPLIT).prefetch_related('items'):
        if sale.cash_paid <= 0 or sale.mobile_paid <= 0 or sale.cash_paid + sale.mobile_paid != sale.original_total:
            issues.append(Issue('SPLIT_AMOUNTS', 'danger',
                                f"Sale #{sale.sale_number or sale.pk}: cash {sale.cash_paid:,.0f} + mobile "
                                f"{sale.mobile_paid:,.0f} does not equal the sale total {sale.original_total:,.0f}."))
    issues.extend(_debt_checks(pharmacy, fix))
    issues.extend(_debt_aging(pharmacy))
    issues.extend(_meal_and_expense_checks(pharmacy, start, end))

    for rr in SaleReturnRequest.objects.filter(pharmacy=pharmacy, status=SaleReturnRequest.Status.APPROVED,
                                               decided_at__isnull=True):
        issues.append(Issue('RETURN_NO_DATE', 'danger',
                            f'Approved return #{rr.pk} has no decision time, so it is missing from cash totals.'))
    return issues


def _debt_checks(pharmacy, fix):
    """Debt rows must agree with themselves: settled flag vs balance, repayments vs amount paid."""
    out = []
    debts = Debt.objects.filter(pharmacy=pharmacy)
    for debt in debts:
        should_be_settled = debt.amount_paid >= debt.amount_owed
        if debt.is_settled != should_be_settled:
            issue = Issue('DEBT_SETTLED_FLAG', 'warning',
                          f"Debt of {debt.customer_name} (sale #{debt.sale_id}) is marked "
                          f"{'settled' if debt.is_settled else 'open'} but its balance says otherwise.")
            if fix:
                Debt.objects.filter(pk=debt.pk).update(is_settled=should_be_settled)
                issue.fixed = True
            out.append(issue)
        if debt.amount_paid > debt.amount_owed:
            out.append(Issue('DEBT_OVERPAID', 'danger',
                             f"{debt.customer_name} (sale #{debt.sale_id}) has paid more than is owed."))
    out.extend(_debt_untraced(pharmacy))
    return out


def _debt_untraced(pharmacy):
    """Debts whose 'amount paid' is not backed by recorded payments (older versions let the figure be typed in
    by hand). Money recorded that way never reached the cash ledger, so the manager must see it.
    Debts that had a return with a refund are skipped: their paid figure is legitimately reduced."""
    from django.db.models import Sum
    backed = {r['debt_id']: r['t'] for r in DebtPayment.objects.filter(pharmacy=pharmacy)
              .values('debt_id').annotate(t=Sum('amount'))}
    refunded = set(SaleReturnRequest.objects.filter(
        pharmacy=pharmacy, status=SaleReturnRequest.Status.APPROVED, refund_due__gt=0).values_list('sale_id', flat=True))
    rows = []
    for debt in Debt.objects.filter(pharmacy=pharmacy, amount_paid__gt=0):
        if debt.sale_id in refunded:
            continue
        gap = debt.amount_paid - (backed.get(debt.pk) or ZERO)
        if gap > 0:
            rows.append((debt, gap))
    if not rows:
        return []
    total = sum((g for _, g in rows), ZERO)
    worst = max(rows, key=lambda r: r[1])
    return [Issue('DEBT_UNTRACED', 'warning',
                  f"{len(rows)} debt(s) show TZS {total:,.0f} as paid without a matching payment record "
                  f"(largest: {worst[0].customer_name}, sale #{worst[0].sale_id}, TZS {worst[1]:,.0f}). "
                  "Open the debt's Trace page; record the real payment if money was received.")]


def _meal_and_expense_checks(pharmacy, start, end):
    from datetime import timedelta
    from django.db.models import Count
    from django.utils import timezone
    from expenses.ledger import STAFF_MEALS_CATEGORY, cash_expenses_qs, meal_payments_qs
    from expenses.models import Expense
    out = []
    manual = (cash_expenses_qs(pharmacy.pk).filter(cash_day__range=(start, end),
                                                   category__name__iexact=STAFF_MEALS_CATEGORY))
    auto_days = set(meal_payments_qs(pharmacy.pk).filter(paid_at__date__range=(start, end))
                    .values_list('paid_at__date', flat=True))
    for day in sorted({e.cash_day for e in manual} & auto_days):
        out.append(Issue('MEAL_DOUBLE_DEDUCTION', 'warning',
                         f'{day}: staff meals were deducted automatically AND recorded manually as an expense. '
                         'The drawer is charged twice; remove the manual Staff Meals expense.'))
    stale = (Expense.objects.filter(pharmacy=pharmacy, status=Expense.Status.PENDING,
                                    payment_source=Expense.PaymentSource.CASH_SALES,
                                    submitted_at__lt=timezone.now() - timedelta(days=2))
             .aggregate(n=Count('pk')))
    if stale['n']:
        out.append(Issue('EXPENSE_PENDING_OLD', 'warning',
                         f"{stale['n']} cash expense(s) have waited over 2 days for approval; cash that already "
                         "left the drawer is not deducted until they are approved."))
    # V97.38: auto-recorded employee expenses the boss has not reviewed. The cash already left the drawer, so the
    # figures are right, but an old unreviewed expense should not stay unnoticed.
    unreviewed = (Expense.objects.filter(pharmacy=pharmacy, auto_recorded=True, status=Expense.Status.APPROVED,
                                         reviewed_at__isnull=True, submitted_at__lt=timezone.now() - timedelta(days=2))
                  .aggregate(n=Count('pk')))
    if unreviewed['n']:
        out.append(Issue('EXPENSE_UNREVIEWED_OLD', 'warning',
                         f"{unreviewed['n']} employee expense(s) were deducted from the drawer more than 2 days ago "
                         "and are still waiting for boss review."))
    # Internal consistency: the drawer total of each day must equal the sum of its itemised ledger lines, so the
    # summary, the drill-down and every report can never disagree. Checked for the most recent 31 days of the range.
    from expenses.ledger import CashLedgerService
    from expenses.models import CashDay
    for day in CashDay.objects.filter(pharmacy=pharmacy, date__range=(max(start, end - timedelta(days=30)), end)):
        net = CashLedgerService.totals(day).net
        itemised = sum((e.signed_amount for e in CashLedgerService.entries(day)), Decimal('0'))
        if abs(net - itemised) > Decimal('0.5'):
            out.append(Issue('LEDGER_ITEMISED_MISMATCH', 'warning',
                             f'{day.date}: the cash total (TZS {net:,.0f}) differs from the sum of its itemised '
                             f'movements (TZS {itemised:,.0f}). Please report this so it can be investigated.'))
    return out


def _debt_aging(pharmacy):
    """One summary line per branch: money owed for over 30 days (so old credit is never forgotten)."""
    from datetime import timedelta
    from django.utils import timezone
    cutoff = timezone.now() - timedelta(days=30)
    old = [d for d in Debt.objects.filter(pharmacy=pharmacy, is_settled=False, created_at__lt=cutoff)]
    if not old:
        return []
    total = sum((d.amount_owed - d.amount_paid for d in old), ZERO)
    return [Issue('DEBT_OVER_30_DAYS', 'warning',
                  f'{len(old)} customer debt(s) are older than 30 days, owing TZS {total:,.0f} in total. '
                  'Send reminders from the Debts page.')]
