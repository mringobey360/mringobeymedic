"""CashLedgerService — the ONE place that decides what counts as drawer cash.

Every cash figure in the system (expected cash, post-close activity, the
timeline, the reopen page, the drill-down) must come from here so two screens
can never disagree.

Accounting rules (kept deliberately simple and explicit):

* Cash sale  : payment_method == CASH, status COMPLETED / PARTIALLY_RETURNED /
               VOIDED, belongs to Sale.business_date, counted at ORIGINAL total
               (refunds are deducted separately, never twice).
* Cash refund: approved SaleReturnRequest on a CASH sale. Cash leaves the drawer
               on the day the refund is DECIDED (decided_at), not on the sale's
               date. This single rule is used for expected cash AND post-close
               detection.
* Cash expense: approved Expense paid from CASH_SALES, on the day it was APPROVED (cash_date; legacy rows: expense_date), timed by
               decided_at (legacy rows: submitted_at).
* Meal payment: an EARNED staff meal is paid from the drawer automatically
               (MealCashPayment, V50.42). Cash leaves on the day the payment is
               recorded; a reversal (meal no longer EARNED) returns it on the day it
               is reversed. Not an Expense, so it is never deducted twice.
* Debt repayment: a DebtPayment recorded with method CASH puts cash into the drawer on the day
               it is recorded (paid_at). Mobile-money repayments and pre-V54.91 (LEGACY) rows do not.
* Refund of a debt sale the customer had already repaid in cash leaves the drawer like any cash refund.
* Transfer     : money moved between the drawer and the mobile money account (CashTransfer, V54.96).
               MOBILE_TO_CASH puts cash INTO the drawer, CASH_TO_MOBILE takes cash OUT of it, on transfer_date.
* Layaway     : cash received on a layaway plan (LayawayPayment, method CASH) puts cash INTO the drawer on the day it is
               received, shown as "Layaway" so the source is always clear. The Sale created at handover is NOT counted
               again (cash_sales_qs skips sales linked to a layaway plan). A cash refund recorded when a plan is cancelled
               (LayawayRefund, method CASH) takes cash OUT on the day it is paid. For every total below, layaway cash is
               added into the debt-collected / refund figures so all screens stay in agreement; the separate
               cash_layaway_* fields only say how much of those figures came from layaway.
* Mobile money / card / bank / unpaid debt never touch the physical drawer.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from django.db import models
from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

ZERO = Decimal('0')
_MONEY = DecimalField(max_digits=14, decimal_places=2)
STAFF_MEALS_CATEGORY = 'Staff Meals'
SALE_STATUSES = ('COMPLETED', 'PARTIALLY_RETURNED', 'VOIDED')


# --------------------------------------------------------------------------
# Shared querysets (the only definition of each cash filter)
# --------------------------------------------------------------------------

def cash_sales_qs(pharmacy_id):
    from sales.models import Sale
    return Sale.objects.filter(
        pharmacy_id=pharmacy_id, payment_method__in=[Sale.PaymentMethod.CASH, Sale.PaymentMethod.SPLIT],
        status__in=[Sale.Status.COMPLETED, Sale.Status.PARTIALLY_RETURNED, Sale.Status.VOIDED],
        # A layaway handover Sale carries cash that was already counted when each instalment was received.
        layaway_plan__isnull=True,
    )


def cash_refunds_qs(pharmacy_id):
    """Approved refunds that physically leave the drawer: refunds on CASH sales, plus the paid part of a
    DEBT sale refunded back to a customer who had repaid it in cash (only when no repayment of that debt
    came by mobile money, so the drawer is never charged for money it never received)."""
    from django.db.models import Q
    from sales.models import DebtPayment, Sale, SaleReturnRequest
    cash_sale = Q(sale__payment_method__in=[Sale.PaymentMethod.CASH, Sale.PaymentMethod.SPLIT])
    paid_debt = (Q(sale__payment_method=Sale.PaymentMethod.DEBT, refund_due__gt=0)
                 & ~Q(sale__debt__payments__payment_method=DebtPayment.Method.MOBILE_MONEY))
    return SaleReturnRequest.objects.filter(
        cash_sale | paid_debt, sale__pharmacy_id=pharmacy_id,
        status=SaleReturnRequest.Status.APPROVED, decided_at__isnull=False,
    )


def cash_refund_amount():
    """SQL expression: the part of a refund that leaves the DRAWER. Whole refund for a cash sale; for a
    Cash + Mobile sale only the cash share (cash_paid / total paid) - the mobile share goes back by mobile money."""
    from django.db.models import Case, When
    from sales.models import Sale
    paid = F('sale__cash_paid') + F('sale__mobile_paid')
    return Case(
        When(sale__payment_method=Sale.PaymentMethod.SPLIT, sale__cash_paid__gt=0, sale__mobile_paid__gt=0,
             then=ExpressionWrapper(F('refund_due') * F('sale__cash_paid') / paid, output_field=_MONEY)),
        default=F('refund_due'), output_field=_MONEY,
    )


def cash_refund_share(rr):
    """Python twin of cash_refund_amount() for one SaleReturnRequest."""
    sale = rr.sale
    if sale.payment_method == 'SPLIT' and sale.cash_paid > 0 and sale.mobile_paid > 0:
        return (rr.refund_due * sale.cash_paid / (sale.cash_paid + sale.mobile_paid)).quantize(Decimal('0.01'))
    return rr.refund_due


def cash_debt_payments_qs(pharmacy_id):
    """Debt repayments received IN CASH (new in V54.91). LEGACY and mobile-money repayments never enter the drawer."""
    from sales.models import DebtPayment
    return DebtPayment.objects.filter(pharmacy_id=pharmacy_id, payment_method=DebtPayment.Method.CASH)


def cash_layaway_payments_qs(pharmacy_id):
    """Layaway instalments/deposits received IN CASH. Mobile / bank / other never enter the drawer."""
    from layaway.models import LayawayPayment
    return LayawayPayment.objects.filter(
        plan__pharmacy_id=pharmacy_id, method=LayawayPayment.Method.CASH, status=LayawayPayment.Status.RECEIVED)


def cash_layaway_refunds_qs(pharmacy_id):
    """Cash handed back to a customer when a layaway plan was cancelled."""
    from layaway.models import LayawayRefund
    return LayawayRefund.objects.filter(plan__pharmacy_id=pharmacy_id, method=LayawayRefund.Method.CASH)


def cash_expenses_qs(pharmacy_id):
    from .models import Expense
    # `cash_day` = the business day the money left the drawer: the approval day (cash_date) or,
    # for legacy rows without one, the expense_date.
    return Expense.objects.filter(
        pharmacy_id=pharmacy_id, status=Expense.Status.APPROVED,
        payment_source=Expense.PaymentSource.CASH_SALES,
    ).annotate(cash_day=Coalesce('cash_date', 'expense_date'))


def _expense_time_q(op, cutoff):
    """Expense timestamp is decided_at, falling back to submitted_at for legacy rows."""
    return (models.Q(**{f'decided_at__{op}': cutoff}) |
            models.Q(decided_at__isnull=True, **{f'submitted_at__{op}': cutoff}))


def meal_payments_qs(pharmacy_id):
    from .models import MealCashPayment
    return MealCashPayment.objects.filter(pharmacy_id=pharmacy_id, is_legacy=False)


def sales_amount(sales_qs):
    """(drawer cash taken at the sale, count) of a cash-sales queryset. The ONLY sales aggregation.
    CASH sale: unit_price x (net quantity + returned quantity), i.e. the original total.
    SPLIT sale: only the cash part the customer handed over (cash_paid); the mobile part never enters the drawer."""
    from sales.models import Sale, SaleItem
    line = ExpressionWrapper(F('unit_price') * (F('quantity') + F('returned_quantity')), output_field=_MONEY)
    cash_only = sales_qs.filter(payment_method=Sale.PaymentMethod.CASH)
    row = SaleItem.objects.filter(sale__in=cash_only.values('pk')).aggregate(
        amount=Coalesce(Sum(line), Value(ZERO, output_field=_MONEY)),
    )
    split = sales_qs.filter(payment_method=Sale.PaymentMethod.SPLIT).aggregate(
        amount=Coalesce(Sum('cash_paid'), Value(ZERO, output_field=_MONEY)))
    return row['amount'] + split['amount'], sales_qs.count()


# --------------------------------------------------------------------------
# Result objects
# --------------------------------------------------------------------------

@dataclass
class LedgerEntry:
    kind: str            # SALE | DEBT_PAYMENT | LAYAWAY_PAYMENT | LAYAWAY_REFUND | REFUND | EXPENSE | MEAL
    reference: str
    occurred_at: datetime
    amount: Decimal      # always positive
    sign: int            # +1 into drawer, -1 out of drawer
    payment_method: str = 'CASH'
    business_date: object = None
    actor: str = ''
    offline: bool = False
    note: str = ''

    @property
    def date_mismatch(self):
        """True when the accounting date differs from the date the record was created/synced."""
        if self.business_date is None or self.occurred_at is None:
            return False
        return timezone.localtime(self.occurred_at).date() != self.business_date

    @property
    def signed_amount(self):
        return self.amount * self.sign


@dataclass
class LedgerTotals:
    cash_sales: Decimal = ZERO
    cash_refunds: Decimal = ZERO
    cash_expenses: Decimal = ZERO
    cash_debt_collected: Decimal = ZERO   # debt repayments received in cash (drawer cash IN)
    cash_layaway_collected: Decimal = ZERO   # part of cash_debt_collected that came from layaway plans
    cash_layaway_refunded: Decimal = ZERO    # part of cash_refunds that was paid back on cancelled layaway plans
    layaway_payment_count: int = 0           # already included in debt_payment_count
    transfer_in: Decimal = ZERO     # mobile money cashed out into the drawer (drawer cash IN)
    transfer_out: Decimal = ZERO    # drawer cash put into mobile money (drawer cash OUT)
    transfer_count: int = 0
    cash_meals: Decimal = ZERO      # meal payments minus reversals (net, drawer cash out)
    meal_paid: Decimal = ZERO
    meal_reversed: Decimal = ZERO
    sales_count: int = 0
    refund_count: int = 0
    expense_count: int = 0
    meal_count: int = 0
    debt_payment_count: int = 0

    @property
    def net(self):
        return (self.cash_sales + self.cash_debt_collected + self.transfer_in
                - self.cash_refunds - self.cash_expenses - self.cash_meals - self.transfer_out)

    @property
    def count(self):
        return (self.sales_count + self.refund_count + self.expense_count + self.meal_count
                + self.debt_payment_count + self.transfer_count)


# --------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------

class CashLedgerService:
    """Stateless helpers. Window semantics: (after, upto] — `after` exclusive,
    `upto` inclusive — so expected(upto) - expected(after) == net(after, upto)."""

    # ---- per-cash-day, time-windowed ------------------------------------
    @staticmethod
    def _day_sales(day, after=None, upto=None):
        qs = cash_sales_qs(day.pharmacy_id).filter(business_date=day.date)
        if after is not None:
            qs = qs.filter(created_at__gt=after)
        if upto is not None:
            qs = qs.filter(created_at__lte=upto)
        return qs

    @staticmethod
    def _day_refunds(day, after=None, upto=None):
        qs = cash_refunds_qs(day.pharmacy_id).filter(decided_at__date=day.date)
        if after is not None:
            qs = qs.filter(decided_at__gt=after)
        if upto is not None:
            qs = qs.filter(decided_at__lte=upto)
        return qs

    @staticmethod
    def _day_debt_payments(day, after=None, upto=None):
        qs = cash_debt_payments_qs(day.pharmacy_id).filter(paid_at__date=day.date)
        if after is not None:
            qs = qs.filter(paid_at__gt=after)
        if upto is not None:
            qs = qs.filter(paid_at__lte=upto)
        return qs

    @staticmethod
    def _day_layaway_payments(day, after=None, upto=None):
        qs = cash_layaway_payments_qs(day.pharmacy_id).filter(received_at__date=day.date)
        if after is not None:
            qs = qs.filter(received_at__gt=after)
        if upto is not None:
            qs = qs.filter(received_at__lte=upto)
        return qs

    @staticmethod
    def _day_layaway_refunds(day, after=None, upto=None):
        qs = cash_layaway_refunds_qs(day.pharmacy_id).filter(refunded_at__date=day.date)
        if after is not None:
            qs = qs.filter(refunded_at__gt=after)
        if upto is not None:
            qs = qs.filter(refunded_at__lte=upto)
        return qs

    @staticmethod
    def _day_expenses(day, after=None, upto=None):
        qs = cash_expenses_qs(day.pharmacy_id).filter(cash_day=day.date)
        if after is not None:
            qs = qs.filter(_expense_time_q('gt', after))
        if upto is not None:
            qs = qs.filter(_expense_time_q('lte', upto))
        return qs

    @staticmethod
    def _day_meal_payments(day, after=None, upto=None):
        qs = meal_payments_qs(day.pharmacy_id).filter(paid_at__date=day.date)
        if after is not None:
            qs = qs.filter(paid_at__gt=after)
        if upto is not None:
            qs = qs.filter(paid_at__lte=upto)
        return qs

    @staticmethod
    def _day_meal_reversals(day, after=None, upto=None):
        qs = meal_payments_qs(day.pharmacy_id).filter(reversed_at__date=day.date)
        if after is not None:
            qs = qs.filter(reversed_at__gt=after)
        if upto is not None:
            qs = qs.filter(reversed_at__lte=upto)
        return qs

    @staticmethod
    def _day_transfers(day, direction, after=None, upto=None):
        from .models import CashTransfer
        qs = CashTransfer.objects.filter(pharmacy_id=day.pharmacy_id, transfer_date=day.date, direction=direction)
        if after is not None:
            qs = qs.filter(created_at__gt=after)
        if upto is not None:
            qs = qs.filter(created_at__lte=upto)
        return qs

    @classmethod
    def totals(cls, day, *, after=None, upto=None):
        t = LedgerTotals()
        t.cash_sales, t.sales_count = sales_amount(cls._day_sales(day, after, upto))
        r = cls._day_refunds(day, after, upto).aggregate(v=Sum(cash_refund_amount()), n=models.Count('pk'))
        t.cash_refunds = r['v'] or ZERO
        t.refund_count = r['n'] or 0
        dp = cls._day_debt_payments(day, after, upto).aggregate(v=Sum('amount'), n=models.Count('pk'))
        t.cash_debt_collected = dp['v'] or ZERO
        t.debt_payment_count = dp['n'] or 0
        lp = cls._day_layaway_payments(day, after, upto).aggregate(v=Sum('amount'), n=models.Count('pk'))
        t.cash_layaway_collected = lp['v'] or ZERO
        t.layaway_payment_count = lp['n'] or 0
        t.cash_debt_collected += t.cash_layaway_collected
        t.debt_payment_count += t.layaway_payment_count
        lr = cls._day_layaway_refunds(day, after, upto).aggregate(v=Sum('amount'), n=models.Count('pk'))
        t.cash_layaway_refunded = lr['v'] or ZERO
        t.cash_refunds += t.cash_layaway_refunded
        t.refund_count += lr['n'] or 0
        e = cls._day_expenses(day, after, upto).aggregate(v=Sum('amount'), n=models.Count('pk'))
        t.cash_expenses = e['v'] or ZERO
        t.expense_count = e['n'] or 0
        p = cls._day_meal_payments(day, after, upto).aggregate(v=Sum('amount'), n=models.Count('pk'))
        r2 = cls._day_meal_reversals(day, after, upto).aggregate(v=Sum('amount'), n=models.Count('pk'))
        t.meal_paid = p['v'] or ZERO
        t.meal_reversed = r2['v'] or ZERO
        t.cash_meals = t.meal_paid - t.meal_reversed
        t.meal_count = (p['n'] or 0) + (r2['n'] or 0)
        from .models import CashTransfer
        tin = cls._day_transfers(day, CashTransfer.Direction.MOBILE_TO_CASH, after, upto).aggregate(v=Sum('amount'), n=models.Count('pk'))
        tout = cls._day_transfers(day, CashTransfer.Direction.CASH_TO_MOBILE, after, upto).aggregate(v=Sum('amount'), n=models.Count('pk'))
        t.transfer_in, t.transfer_out = tin['v'] or ZERO, tout['v'] or ZERO
        t.transfer_count = (tin['n'] or 0) + (tout['n'] or 0)
        return t

    @classmethod
    def expected_cash(cls, day, as_of=None):
        as_of = as_of or timezone.now()
        return day.opening_cash + cls.totals(day, upto=as_of).net

    @classmethod
    def breakdown(cls, day, as_of=None):
        """Itemised expected cash. Uses exactly the same queries as expected_cash(),
        so opening + sales - refunds - meals - other == expected (no double deduction).
        Staff meals are a SUBSET of cash expenses, shown separately for readability."""
        from .models import Expense
        as_of = as_of or timezone.now()
        t = cls.totals(day, upto=as_of)
        meals_qs = cls._day_expenses(day, upto=as_of).filter(category__name__iexact=STAFF_MEALS_CATEGORY)
        m = meals_qs.aggregate(v=Sum('amount'), n=models.Count('pk'))
        legacy_meals = m['v'] or ZERO
        meals = legacy_meals + t.cash_meals   # manual Staff Meals expenses + automatic meal payments
        pend = Expense.objects.filter(
            pharmacy_id=day.pharmacy_id, expense_date=day.date, status=Expense.Status.PENDING,
            payment_source=Expense.PaymentSource.CASH_SALES,
        ).aggregate(v=Sum('amount'), n=models.Count('pk'))
        return {
            'opening': day.opening_cash,
            'cash_sales': t.cash_sales,
            'cash_refunds': t.cash_refunds,
            'debt_collected': t.cash_debt_collected,
            'layaway_collected': t.cash_layaway_collected,
            'layaway_refunded': t.cash_layaway_refunded,
            'transfer_in': t.transfer_in,
            'transfer_out': t.transfer_out,
            'staff_meals': meals,
            'staff_meals_count': (m['n'] or 0) + t.meal_count,
            'staff_meals_auto': t.cash_meals,
            'staff_meals_expense': legacy_meals,
            'other_expenses': t.cash_expenses - legacy_meals,
            'cash_expenses_total': t.cash_expenses + t.cash_meals,
            'pending_expenses': pend['v'] or ZERO,
            'pending_count': pend['n'] or 0,
            'expected': day.opening_cash + t.net,
        }

    @classmethod
    def has_activity_after(cls, day, cutoff):
        return (cls._day_sales(day, after=cutoff).exists()
                or cls._day_debt_payments(day, after=cutoff).exists()
                or cls._day_layaway_payments(day, after=cutoff).exists()
                or cls._day_layaway_refunds(day, after=cutoff).exists()
                or cls._day_refunds(day, after=cutoff).exists()
                or cls._day_expenses(day, after=cutoff).exists()
                or cls._day_meal_payments(day, after=cutoff).exists()
                or cls._day_meal_reversals(day, after=cutoff).exists()
                or cls._day_transfers(day, 'MOBILE_TO_CASH', after=cutoff).exists()
                or cls._day_transfers(day, 'CASH_TO_MOBILE', after=cutoff).exists())

    @classmethod
    def post_close_totals(cls, day):
        """Net cash movement strictly after the day's closing timestamp."""
        if not day.closed_at:
            return LedgerTotals()
        return cls.totals(day, after=day.closed_at)

    @classmethod
    def post_close_summary(cls, day):
        """Everything the banner / reopen page / drill-down need, in one dict."""
        t = cls.post_close_totals(day)
        original_expected = cls.expected_cash(day, day.closed_at) if day.closed_at else None
        return {
            'totals': t,
            'net': t.net,
            'original_closing': day.actual_closing_cash,
            'original_expected': original_expected,
            'current_expected': cls.expected_cash(day),
        }

    @classmethod
    def entries(cls, day, *, after=None, upto=None):
        """Transaction-level rows (for drill-down), oldest first."""
        out = []
        sales = (cls._day_sales(day, after, upto)
                 .select_related('served_by').prefetch_related('items'))
        for s in sales:
            out.append(LedgerEntry(
                kind='SALE', reference=f'#{s.sale_number or s.pk}', occurred_at=s.created_at,
                amount=(s.cash_paid if s.payment_method == 'SPLIT' else s.original_total), sign=1, business_date=s.business_date,
                actor=(s.served_by.get_full_name() or s.served_by.get_username()) if s.served_by else '',
                offline=bool(s.client_uuid),
            ))
        for dp in cls._day_debt_payments(day, after, upto).select_related('debt', 'recorded_by'):
            out.append(LedgerEntry(
                kind='DEBT_PAYMENT', reference=f'{dp.debt.customer_name} (debt repayment)',
                occurred_at=dp.paid_at, amount=dp.amount, sign=1,
                business_date=dp.paid_at.astimezone(timezone.get_current_timezone()).date(),
                actor=(dp.recorded_by.get_full_name() or dp.recorded_by.get_username()) if dp.recorded_by else '',
                note=f'Debt of sale #{dp.debt.sale_id}'))
        for lp in cls._day_layaway_payments(day, after, upto).select_related('plan', 'received_by'):
            out.append(LedgerEntry(
                kind='LAYAWAY_PAYMENT', reference=f'Layaway {lp.plan.reference} - {lp.plan.customer_name}',
                occurred_at=lp.received_at, amount=lp.amount, sign=1,
                business_date=lp.received_at.astimezone(timezone.get_current_timezone()).date(),
                actor=(lp.received_by.get_full_name() or lp.received_by.get_username()) if lp.received_by else '',
                note='Cash received on a layaway plan. Not a sale yet; the handover sale does not count this cash again.'))
        for lr in cls._day_layaway_refunds(day, after, upto).select_related('plan', 'refunded_by'):
            out.append(LedgerEntry(
                kind='LAYAWAY_REFUND', reference=f'Layaway {lr.plan.reference} - refund to {lr.plan.customer_name}',
                occurred_at=lr.refunded_at, amount=lr.amount, sign=-1,
                business_date=lr.refunded_at.astimezone(timezone.get_current_timezone()).date(),
                actor=(lr.refunded_by.get_full_name() or lr.refunded_by.get_username()) if lr.refunded_by else '',
                note='Cash returned to the customer because the layaway plan was cancelled.'))
        refunds = (cls._day_refunds(day, after, upto)
                   .select_related('sale', 'decided_by'))
        for r in refunds:
            out.append(LedgerEntry(
                kind='REFUND', reference=f'#{r.sale.sale_number or r.sale_id} (refund)',
                occurred_at=r.decided_at, amount=cash_refund_share(r), sign=-1,
                business_date=r.decided_at.astimezone(timezone.get_current_timezone()).date(),
                actor=(r.decided_by.get_full_name() or r.decided_by.get_username()) if r.decided_by else '',
                note=f'Sale dated {r.sale.business_date}',
            ))
        expenses = (cls._day_expenses(day, after, upto)
                    .select_related('submitted_by'))
        for x in expenses:
            out.append(LedgerEntry(
                kind='EXPENSE', reference=x.title[:60], occurred_at=x.decided_at or x.submitted_at,
                amount=x.amount, sign=-1, business_date=x.cash_day,
                note=(f'Expense dated {x.expense_date}, approved {x.cash_day}' if x.cash_day != x.expense_date else ''),
                actor=(x.submitted_by.get_full_name() or x.submitted_by.get_username()) if x.submitted_by else '',
            ))
        for mp in (cls._day_meal_payments(day, after, upto).select_related('meal__employee')):
            emp = mp.meal.employee
            out.append(LedgerEntry(
                kind='MEAL', reference=f'{mp.meal.get_meal_type_display()} – {emp.get_full_name() or emp.get_username()}',
                occurred_at=mp.paid_at, amount=mp.amount, sign=-1, business_date=mp.paid_at.astimezone(
                    timezone.get_current_timezone()).date(),
                note=f'Staff meal for {mp.meal_date}'))
        for mp in (cls._day_meal_reversals(day, after, upto).select_related('meal__employee')):
            emp = mp.meal.employee
            out.append(LedgerEntry(
                kind='MEAL', reference=f'Reversal – {mp.meal.get_meal_type_display()} – {emp.get_full_name() or emp.get_username()}',
                occurred_at=mp.reversed_at, amount=mp.amount, sign=1, business_date=mp.reversed_at.astimezone(
                    timezone.get_current_timezone()).date(),
                note=f'Meal for {mp.meal_date} no longer funded'))
        from .models import CashTransfer
        for direction, sign, label in ((CashTransfer.Direction.MOBILE_TO_CASH, 1, 'Mobile money cashed out into the drawer'),
                                       (CashTransfer.Direction.CASH_TO_MOBILE, -1, 'Drawer cash put into mobile money')):
            for tr in cls._day_transfers(day, direction, after, upto).select_related('created_by'):
                out.append(LedgerEntry(
                    kind='TRANSFER', reference=label, occurred_at=tr.created_at, amount=tr.amount, sign=sign,
                    business_date=tr.transfer_date,
                    actor=(tr.created_by.get_full_name() or tr.created_by.get_username()) if tr.created_by else '',
                    note=tr.note or tr.reference))
        out.sort(key=lambda e: e.occurred_at)
        return out

    # ---- multi-day (timeline) -------------------------------------------
    @staticmethod
    def daily_totals(pharmacy_id, start_date, end_date):
        """{date: LedgerTotals} using the SAME filters as the per-day methods."""
        from sales.models import SaleItem
        zero = Value(ZERO, output_field=_MONEY)
        line = ExpressionWrapper(F('unit_price') * (F('quantity') + F('returned_quantity')), output_field=_MONEY)
        out = {}

        def slot(d):
            return out.setdefault(d, LedgerTotals())

        from sales.models import Sale
        in_range = cash_sales_qs(pharmacy_id).filter(business_date__range=(start_date, end_date))
        cash_ids = in_range.filter(payment_method=Sale.PaymentMethod.CASH).values('pk')
        for row in (SaleItem.objects.filter(sale__in=cash_ids).values('sale__business_date')
                    .annotate(amount=Coalesce(Sum(line), zero), n=models.Count('sale_id', distinct=True))):
            t = slot(row['sale__business_date'])
            t.cash_sales, t.sales_count = row['amount'], row['n']
        for row in (in_range.filter(payment_method=Sale.PaymentMethod.SPLIT).values('business_date')
                    .annotate(amount=Coalesce(Sum('cash_paid'), zero), n=models.Count('pk'))):
            t = slot(row['business_date'])
            t.cash_sales += row['amount']
            t.sales_count += row['n']
        for row in (cash_refunds_qs(pharmacy_id).filter(decided_at__date__range=(start_date, end_date))
                    .values('decided_at__date').annotate(amount=Coalesce(Sum(cash_refund_amount()), zero), n=models.Count('pk'))):
            t = slot(row['decided_at__date'])
            t.cash_refunds, t.refund_count = row['amount'], row['n']
        for row in (cash_debt_payments_qs(pharmacy_id).filter(paid_at__date__range=(start_date, end_date))
                    .values('paid_at__date').annotate(amount=Coalesce(Sum('amount'), zero), n=models.Count('pk'))):
            t = slot(row['paid_at__date'])
            t.cash_debt_collected, t.debt_payment_count = row['amount'], row['n']
        for row in (cash_layaway_payments_qs(pharmacy_id).filter(received_at__date__range=(start_date, end_date))
                    .values('received_at__date').annotate(amount=Coalesce(Sum('amount'), zero), n=models.Count('pk'))):
            t = slot(row['received_at__date'])
            t.cash_layaway_collected += row['amount']
            t.layaway_payment_count += row['n']
            t.cash_debt_collected += row['amount']
            t.debt_payment_count += row['n']
        for row in (cash_layaway_refunds_qs(pharmacy_id).filter(refunded_at__date__range=(start_date, end_date))
                    .values('refunded_at__date').annotate(amount=Coalesce(Sum('amount'), zero), n=models.Count('pk'))):
            t = slot(row['refunded_at__date'])
            t.cash_layaway_refunded += row['amount']
            t.cash_refunds += row['amount']
            t.refund_count += row['n']
        for row in (cash_expenses_qs(pharmacy_id).filter(cash_day__range=(start_date, end_date))
                    .values('cash_day').annotate(amount=Coalesce(Sum('amount'), zero), n=models.Count('pk'))):
            t = slot(row['cash_day'])
            t.cash_expenses, t.expense_count = row['amount'], row['n']
        for row in (meal_payments_qs(pharmacy_id).filter(paid_at__date__range=(start_date, end_date))
                    .values('paid_at__date').annotate(amount=Coalesce(Sum('amount'), zero), n=models.Count('pk'))):
            t = slot(row['paid_at__date'])
            t.meal_paid = row['amount']; t.cash_meals += row['amount']; t.meal_count += row['n']
        for row in (meal_payments_qs(pharmacy_id).filter(reversed_at__date__range=(start_date, end_date))
                    .values('reversed_at__date').annotate(amount=Coalesce(Sum('amount'), zero), n=models.Count('pk'))):
            t = slot(row['reversed_at__date'])
            t.meal_reversed = row['amount']; t.cash_meals -= row['amount']; t.meal_count += row['n']
        from .models import CashTransfer
        for row in (CashTransfer.objects.filter(pharmacy_id=pharmacy_id, transfer_date__range=(start_date, end_date))
                    .values('transfer_date', 'direction').annotate(amount=Coalesce(Sum('amount'), zero), n=models.Count('pk'))):
            t = slot(row['transfer_date'])
            if row['direction'] == CashTransfer.Direction.MOBILE_TO_CASH:
                t.transfer_in += row['amount']
            else:
                t.transfer_out += row['amount']
            t.transfer_count += row['n']
        return out


# --------------------------------------------------------------------------
# V54.43 — per-employee expected cash (reference for boss AND employee)
# --------------------------------------------------------------------------

def employee_cash_refunds(pharmacy_id, start_date, end_date, employee=None):
    """{(employee_id, date): cash handed back to customers}. Same source and same day rule as the drawer
    (CashLedgerService): an approved cash refund counts on the day it was DECIDED, for the employee who
    requested it (the person who hands the money back). Refunds that management raised and approved
    themselves never reduce an employee's figure."""
    from accounts.models import User
    qs = (cash_refunds_qs(pharmacy_id)
          .filter(decided_at__date__gte=start_date, decided_at__date__lte=end_date)
          .select_related('sale', 'requested_by'))
    if employee is not None:
        qs = qs.filter(requested_by=employee)
    out = {}
    for rr in qs.distinct():
        who = rr.requested_by
        if who is None or who.role != User.Role.EMPLOYEE:
            continue
        key = (who.pk, timezone.localtime(rr.decided_at).date())
        out[key] = out.get(key, ZERO) + cash_refund_share(rr)
    return out


def employee_cash_reference(pharmacy_id, date_value, employee=None):
    """Per employee, for one business date: cash sales they made, the meal money that leaves the drawer
    for them (EARNED meals), and the cash they should hand over = cash sales - earned meals.
    Meals claimed but still DEBT / awaiting settlement are shown separately (not deducted yet).
    Reference only: the drawer figure itself always comes from CashLedgerService."""
    from django.contrib.auth import get_user_model
    from accounts.models import MealLedger
    sales = cash_sales_qs(pharmacy_id).filter(business_date=date_value, served_by__isnull=False)
    meals = MealLedger.objects.filter(pharmacy_id=pharmacy_id, date=date_value, claimed_at__isnull=False)
    if employee is not None:
        sales = sales.filter(served_by=employee)
        meals = meals.filter(employee=employee)
    refunds = employee_cash_refunds(pharmacy_id, date_value, date_value, employee)
    ids = (set(sales.values_list('served_by_id', flat=True)) | set(meals.values_list('employee_id', flat=True))
           | {uid for (uid, _d) in refunds})
    if employee is not None:
        ids.add(employee.pk)
    users = get_user_model().objects.in_bulk(ids)
    rows = []
    for uid in ids:
        amount, count = sales_amount(sales.filter(served_by_id=uid))
        mine = meals.filter(employee_id=uid)
        earned = mine.filter(status='EARNED').aggregate(v=Sum('amount'))['v'] or ZERO
        debt = mine.filter(status='DEBT').aggregate(v=Sum('amount'))['v'] or ZERO
        waiting = mine.filter(status='PENDING').aggregate(v=Sum('amount'))['v'] or ZERO
        refunded = refunds.get((uid, date_value), ZERO)
        rows.append({
            'employee': users.get(uid), 'cash_sales': amount, 'sales_count': count,
            'cash_refunds': refunded,
            'meal_earned': earned, 'meal_debt': debt, 'meal_awaiting': waiting,
            'meal_not_deducted': debt + waiting, 'expected': amount - refunded - earned,
        })
    rows.sort(key=lambda r: ((r['employee'].get_full_name() or r['employee'].get_username()).lower() if r['employee'] else ''))
    return rows


def employee_cash_history(pharmacy_id, employee, start_date, end_date):
    """V54.44: day-by-day expected cash for ONE employee (newest first), grouped queries only.

    Per business date: cash sales they made (original totals, same rule as the drawer ledger),
    meal money deducted (EARNED meals), meals claimed but not deducted (DEBT / awaiting), and
    expected cash = cash sales - deducted meals. Days with no activity are omitted.
    Reference only: the branch drawer itself always comes from CashLedgerService."""
    from accounts.models import MealLedger
    from sales.models import SaleItem
    line = ExpressionWrapper(F('unit_price') * (F('quantity') + F('returned_quantity')), output_field=_MONEY)
    sales = cash_sales_qs(pharmacy_id).filter(served_by=employee, business_date__range=(start_date, end_date))
    days = {}

    def slot(d):
        return days.setdefault(d, {'date': d, 'sales_count': 0, 'cash_sales': ZERO, 'cash_refunds': ZERO,
                                   'meal_earned': ZERO, 'meal_not_deducted': ZERO, 'meals_count': 0})

    for row in (SaleItem.objects.filter(sale__in=sales.filter(payment_method='CASH').values('pk'))
                .values('sale__business_date')
                .annotate(amount=Coalesce(Sum(line), Value(ZERO, output_field=_MONEY)),
                          n=models.Count('sale_id', distinct=True))):
        r = slot(row['sale__business_date'])
        r['cash_sales'] = row['amount']
        r['sales_count'] = row['n'] or 0
    from sales.models import Sale as _Sale
    for row in (sales.filter(payment_method=_Sale.PaymentMethod.SPLIT).values('business_date')
                .annotate(amount=Coalesce(Sum('cash_paid'), Value(ZERO, output_field=_MONEY)),
                          n=models.Count('pk'))):
        r = slot(row['business_date'])
        r['cash_sales'] += row['amount']
        r['sales_count'] += row['n'] or 0
    for m in MealLedger.objects.filter(pharmacy_id=pharmacy_id, employee=employee, claimed_at__isnull=False,
                                       date__range=(start_date, end_date)).only('date', 'status', 'amount'):
        r = slot(m.date)
        r['meals_count'] += 1
        if m.status == 'EARNED':
            r['meal_earned'] += m.amount or ZERO
        elif m.status in ('DEBT', 'PENDING'):
            r['meal_not_deducted'] += m.amount or ZERO
    for (uid, d), amount in employee_cash_refunds(pharmacy_id, start_date, end_date, employee).items():
        slot(d)['cash_refunds'] += amount
    rows = sorted(days.values(), key=lambda r: r['date'], reverse=True)
    for r in rows:
        r['expected'] = r['cash_sales'] - r['cash_refunds'] - r['meal_earned']
    return rows
