"""Late entry (backdated) sales.

A late entry is a sale that REALLY happened on an earlier business day - for
example the cashier wrote it in the paper notebook because the phone/system was
unavailable - and is typed into the system afterwards.

Accounting rules (kept in one place so the POS, reports and cash control agree):

* ``Sale.business_date``  = the day the sale really happened. EVERY report, the
  cash drawer and cash control read this date, so yesterday's sale lands in
  yesterday's numbers and never inflates today's.
* ``Sale.created_at``     = when it was actually typed in. Never rewritten, so
  the audit trail always shows the real entry time next to the business date.
* Stock is deducted at entry time (that is when the system learns about it) and
  the stock movement says "[Late entry for <date>]".
* A late CASH / Cash+Mobile sale (or a credit sale with a cash deposit) goes
  into that day's cash drawer, so that day's cash-control record must exist and
  must not be physically CLOSED. A closed day has to be reopened by the manager
  (audited workflow); nothing is silently changed after a day was verified.
* Never in the future, never older than ``settings.LATE_ENTRY_MAX_DAYS``.
* A reason is mandatory. Employee late entries are queued for the owner
  ("Sales needing review") so the owner can verify them against the notebook.
"""
from dataclasses import dataclass
from datetime import date as _date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

from .models import LateEntryReason, Sale


class LateEntryError(Exception):
    """Validation problem. ``status`` is the HTTP status the API should return.

    ``code`` lets the POS react (e.g. 'closed_day' -> owner may confirm a correction).
    """

    def __init__(self, message, status=400, code='', can_ack=False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.can_ack = can_ack


@dataclass(frozen=True)
class LateEntry:
    business_date: _date
    reason: str
    note: str

    @property
    def is_late(self):
        return self.business_date < timezone.localdate()

    def stock_note_prefix(self):
        return f"[Late entry for {self.business_date:%d %b %Y}] "

    def review_note(self, user):
        label = dict(LateEntryReason.choices).get(self.reason, self.reason)
        who = user.get_full_name() or user.get_username()
        text = (f"Late entry: sale made on {self.business_date:%d %b %Y}, typed in by {who} on "
                f"{timezone.localdate():%d %b %Y}. Reason: {label}.")
        if self.note:
            text += f" Note: {self.note}"
        return text + " Verify against the paper record."


def max_days():
    try:
        return max(0, int(getattr(settings, 'LATE_ENTRY_MAX_DAYS', 3)))
    except (TypeError, ValueError):
        return 3


def parse_late_entry(payload):
    """Return a LateEntry for a payload, or None for an ordinary (today) sale.

    Raises LateEntryError for an invalid request.
    """
    raw = str((payload or {}).get('business_date') or '').strip()
    today = timezone.localdate()
    if not raw:
        return None
    try:
        business_date = _date.fromisoformat(raw)
    except ValueError:
        raise LateEntryError('Invalid sale date.')
    if business_date == today:
        return None
    if business_date > today:
        raise LateEntryError('The sale date cannot be in the future.')
    limit = max_days()
    if limit <= 0:
        raise LateEntryError('Recording sales for an earlier day is switched off.', 403)
    if (today - business_date).days > limit:
        raise LateEntryError(
            f'A sale can only be recorded for the last {limit} day(s). Ask the shop owner for older sales.', 400)

    reason = str(payload.get('late_entry_reason') or '').strip()
    if reason not in LateEntryReason.values:
        raise LateEntryError('Choose why this sale was not recorded on the day.')
    note = str(payload.get('late_entry_note') or '').strip()[:300]
    if reason == LateEntryReason.OTHER and len(note) < 8:
        raise LateEntryError('Explain the reason in the note (at least 8 characters).')
    return LateEntry(business_date=business_date, reason=reason, note=note)


def lock_cash_day_for_late_entry(pharmacy, business_date, *, is_owner_side=False, acknowledged=False):
    """Lock the cash-control day that will receive late cash. Must run inside transaction.atomic().

    Returns ``(cash_day, closed_day_correction)``.

    * Day missing                    -> error.
    * Day OPEN / DAY_ENDED           -> fine; the sale simply joins that day's drawer.
    * Day CLOSED (already verified)  -> the physical count is NEVER rewritten. Only the owner/manager
      may post to it, and only after explicitly confirming. The sale is then "post-close activity" for
      that day, which the existing Cash Control banner + audited *Reopen & Recount* workflow resolves
      (recount the drawer, re-close, today's opening is re-based). Employees are asked to call the owner.
    """
    from expenses.models import CashDay
    day = CashDay.objects.select_for_update().filter(pharmacy=pharmacy, date=business_date).first()
    if day is None:
        raise LateEntryError(
            f'There is no cash-control record for {business_date:%d %b %Y}. '
            'Ask the shop owner to check Cash Control for that day first.', 409)
    if day.status != CashDay.Status.CLOSED:
        return day, False
    when = f'{business_date:%d %b %Y}'
    if not is_owner_side:
        raise LateEntryError(
            f'Cash control for {when} is already closed and verified, so a cash sale cannot be added by an employee. '
            'Ask the shop owner/manager to record it. If it was paid by Mobile Money or on Credit, '
            'you can record it with that payment method instead.', 409, code='closed_day', can_ack=False)
    if not acknowledged:
        raise LateEntryError(
            f'Cash control for {when} is already CLOSED and verified. You can still record this sale on {when}: '
            'it will be flagged as post-close cash on that day, the drawer will not be changed silently, and you '
            'then finish it in Cash Control with Reopen & Recount (audited). Confirm to continue.',
            409, code='closed_day', can_ack=True)
    return day, True


def needs_cash_day(payment_method, deposit_amount=0, deposit_method='CASH'):
    if payment_method in (Sale.PaymentMethod.CASH, Sale.PaymentMethod.SPLIT):
        return True
    if payment_method == Sale.PaymentMethod.DEBT:
        try:
            amount = Decimal(str(deposit_amount or 0))
        except (InvalidOperation, TypeError, ValueError):
            amount = Decimal('0')
        return amount > 0 and str(deposit_method or 'CASH').upper() == 'CASH'
    return False


def paid_at_for(late):
    """Timestamp used for a credit-sale deposit so the money lands in the sale's own day.

    End of the business day (local time) - never in the future, never inside today.
    """
    from datetime import datetime, time
    return timezone.make_aware(datetime.combine(late.business_date, time(23, 59)),
                               timezone.get_current_timezone())


# ---------------------------------------------------------------------------
# Closed-day reconciliation flow (owner)
# ---------------------------------------------------------------------------
CLOSED_DAY_MARK = 'POSTED TO A CLOSED CASH DAY'


def _pending_closed_day_sales(pharmacy):
    return Sale.objects.filter(pharmacy=pharmacy, is_late_entry=True, needs_review=True,
                               review_note__contains=CLOSED_DAY_MARK)


def auto_resolve_reconciled(pharmacy, user=None):
    """Close the loop automatically.

    A sale posted onto a CLOSED cash day stays on the review list until the owner has run
    Reopen & Recount and closed that day again. As soon as the day is CLOSED again with no
    cash activity after its (new) close, the sale is reconciled -> marked resolved, with a
    note. Returns the number of sales resolved. Never raises into the caller.
    """
    try:
        from expenses.models import CashDay
        resolved = 0
        pending = list(_pending_closed_day_sales(pharmacy))
        for business_date in sorted({s.business_date for s in pending}):
            day = CashDay.objects.filter(pharmacy=pharmacy, date=business_date).first()
            if day is None or day.status != CashDay.Status.CLOSED or day.post_close_cash_activity:
                continue
            resolved += _pending_closed_day_sales(pharmacy).filter(business_date=business_date).update(
                needs_review=False, reviewed_at=timezone.now(),
                reviewed_by=user if getattr(user, 'pk', None) else None,
            )
        return resolved
    except Exception:  # noqa: BLE001 - housekeeping must never break a page
        import logging
        logging.getLogger(__name__).exception('late entry auto-resolve failed')
        return 0


def reconcile_overview(pharmacy):
    """One row per business date that still has late sales waiting for the cash day to be re-closed."""
    from expenses.models import CashDay
    from expenses.ledger import CashLedgerService
    by_date = {}
    for sale in (_pending_closed_day_sales(pharmacy).select_related('served_by')
                 .prefetch_related('items').order_by('business_date', 'created_at')):
        by_date.setdefault(sale.business_date, []).append(sale)
    rows = []
    for business_date, sales in sorted(by_date.items()):
        day = CashDay.objects.filter(pharmacy=pharmacy, date=business_date).first()
        if day is None:
            step, summary = 'NO_DAY', None
        elif day.status == CashDay.Status.CLOSED and day.post_close_cash_activity:
            step, summary = 'REOPEN', CashLedgerService.post_close_summary(day)
        elif day.status != CashDay.Status.CLOSED:
            step, summary = 'CLOSE', None
        else:
            step, summary = 'DONE', None
        rows.append({
            'date': business_date, 'day': day, 'step': step, 'summary': summary, 'sales': sales,
            'total': sum((s.total_amount for s in sales), Decimal('0')),
        })
    return rows


def notify_owner(pharmacy, title, message, *, link_url, dedupe_key, level='WARNING'):
    """In-app alert to the branch's owner/managers. Never raises (a sale must not fail on an alert)."""
    try:
        from notifications.utils import notify_many, management_recipients
        from notifications.models import Notification
        notify_many(
            management_recipients(pharmacy), title, message,
            category=Notification.Category.SALES,
            level=getattr(Notification.Level, level, Notification.Level.WARNING),
            link_url=link_url, pharmacy=pharmacy, dedupe_key=dedupe_key,
        )
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('late entry owner notification failed')
