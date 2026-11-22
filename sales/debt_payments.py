"""One safe way to record money received against a Debt (used by the Debts screen AND the POS deposit)."""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import Debt, DebtPayment


class DebtPaymentError(Exception):
    pass


def normalize_method(value):
    value = (value or '').strip().upper()
    return value if value in (DebtPayment.Method.CASH, DebtPayment.Method.MOBILE_MONEY) else DebtPayment.Method.CASH


@transaction.atomic
def record_debt_payment(debt_id, amount, method, user, *, paid_at=None, kind=DebtPayment.Kind.REPAYMENT, note=''):
    """Lock the debt, cap the payment at the balance, write the DebtPayment and update the debt together.

    kind=DEPOSIT is the part-payment taken at the till while the credit sale is made (the rest stays a debt);
    kind=REPAYMENT is every later payment. The split is what lets reports show a 30,000 credit sale with
    10,000 paid as "10,000 received + 20,000 lent" instead of 30,000 on credit.
    Returns (debt, applied_amount, capped). Raises DebtPaymentError for unusable input."""
    debt = Debt.objects.select_for_update().get(pk=debt_id)
    amount = Decimal(str(amount or 0))
    if debt.is_settled:
        raise DebtPaymentError(f"This debt for {debt.customer_name} is already fully settled.")
    if amount <= 0:
        raise DebtPaymentError('Enter a valid payment amount greater than zero.')
    if kind not in (DebtPayment.Kind.DEPOSIT, DebtPayment.Kind.REPAYMENT):
        kind = DebtPayment.Kind.REPAYMENT
    balance = debt.amount_owed - debt.amount_paid
    applied = min(amount, balance)
    DebtPayment.objects.create(
        debt=debt, pharmacy=debt.pharmacy, amount=applied, payment_method=normalize_method(method),
        kind=kind, note=(note or '').strip()[:200],
        recorded_by=user, paid_at=paid_at or timezone.now(),
    )
    debt.amount_paid += applied
    debt.is_settled = debt.amount_paid >= debt.amount_owed
    debt.save()
    return debt, applied, applied < amount
