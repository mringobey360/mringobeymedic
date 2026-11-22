"""
The glue between a PesapalTransaction and the rest of the system.

submit_to_pesapal(): creates the Pesapal order and stores the checkout link.
refresh_status():    asks Pesapal for the authoritative status and, the FIRST
    time it comes back COMPLETED *and verifies* (same merchant reference,
    amount and currency), activates exactly the right thing — a subscription,
    a renewal, a branch add-on, or an SMS / report package — by reusing the
    same activation code the manual (HQ-confirmed) flow uses, so behaviour
    (notifications, SMS, receipts) is identical whichever way the customer paid.

Safety properties
-----------------
* Activation runs in ONE database transaction. If anything inside fails, ALL of
  it rolls back (no half-created accounts, no credits without an approved
  purchase) and the platform owner is alerted ONCE per distinct failure.
* A row lock on the PesapalTransaction means an IPN callback and a browser poll
  racing each other can never activate twice.
* A payment that Pesapal says is COMPLETED but whose amount/currency/reference
  does not match what we asked for is NOT activated; it is parked as INVALID and
  the owner is alerted.
* Money that arrives for something that was already activated (e.g. HQ approved
  the same purchase by hand while the customer paid online) is flagged to the
  owner as a possible duplicate payment instead of vanishing silently.
"""
import hashlib
import json
import time
import logging
import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction as db_transaction
from django.db.models import Q, F
from django.urls import reverse
from django.utils import timezone

from . import pesapal_client, abliner_client, networks
from .privacy import redact_payload, redact_text
from core import ratelimit
from .models import PaymentEvent, PaymentGatewaySettings, PesapalSettings, PesapalTransaction

logger = logging.getLogger('payments.services')

AMOUNT_TOLERANCE = Decimal('0.50')   # TZS has no minor unit; tolerate rounding only
DEFAULT_RESUME_WINDOW_SECONDS = 1800
_PLAN_PREFIX_RE = re.compile(r'^\[[^\]]*\]\s*')


class ActivationError(Exception):
    """Raised by an activation handler when a paid item cannot be activated."""


# ============================================================ creation ===

def pesapal_usable():
    """True when Pesapal is switched on AND has its keys. Never raises."""
    try:
        cfg = PesapalSettings.load()
        return bool(cfg.is_enabled and cfg.is_configured)
    except Exception:  # noqa: BLE001
        return False


def resolve_gateway(requested=None):
    """
    Which provider a NEW payment should use.

    `requested` (e.g. the customer chose "pay by card") wins, otherwise the platform
    owner's switch in Django Admin. If the wanted provider is not actually usable
    (turned off, keys missing) but the other one is, use the other one, so a missing
    key never strands a customer on an error page. Existing payments never move.
    """
    G = PaymentGatewaySettings.Gateway
    cfg = PaymentGatewaySettings.load()
    wanted = requested or (cfg.active_gateway if cfg.is_enabled else G.PESAPAL)
    usable = {G.ABLINER: abliner_client.is_usable(), G.PESAPAL: pesapal_usable()}
    if not usable.get(wanted):
        other = G.PESAPAL if wanted == G.ABLINER else G.ABLINER
        if usable.get(other):
            logger.warning('Gateway %s is not usable; using %s for a new payment.', wanted, other)
            return other
    return wanted


def create_transaction(*, purpose, target, amount, description, owner=None,
                       payer_name='', payer_email='', payer_phone='', currency='TZS', gateway=None):
    try:
        amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    except Exception as exc:
        raise pesapal_client.PesapalError(
            "This payment amount is not valid. Please contact support.",
            technical=f"create_transaction: bad amount {amount!r}: {exc}",
        ) from exc
    if amount <= 0:
        raise pesapal_client.PesapalError(
            "This item has no price set, so it cannot be paid online. Please contact support.",
            technical=f"create_transaction: non-positive amount {amount} for {purpose}",
        )
    content_type = ContentType.objects.get_for_model(target)
    return PesapalTransaction.objects.create(
        gateway=resolve_gateway(gateway), purpose=purpose, content_type=content_type, object_id=target.pk,
        owner=owner, payer_name=(payer_name or '')[:150], payer_email=(payer_email or '')[:254],
        payer_phone=(payer_phone or '')[:20], description=(description or '')[:255],
        amount=amount, currency=currency,
    )


def find_resumable_transaction(purpose, target, window_seconds=DEFAULT_RESUME_WINDOW_SECONDS,
                               description_prefix=None, amount=None, gateway=None):
    """
    Look for an already-SUBMITTED ("Awaiting Payment") transaction for this exact
    purpose/target created within the last `window_seconds`, so a double-click or
    reopened page resumes the order already sent to Pesapal instead of spawning a
    second one.

    `description_prefix` narrows renewals to the same requested tier/plan.
    `amount` narrows to the same price: if the price changed in the meantime the
    old order must NOT be resumed (the customer would pay the stale amount).
    """
    content_type = ContentType.objects.get_for_model(target)
    cutoff = timezone.now() - timedelta(seconds=window_seconds)
    S = PesapalTransaction.Status
    # Pesapal: an order that was submitted and has a hosted page to go back to.
    # ABLiner: the customer picks network + number on OUR checkout, so a payment that is
    # still PENDING (form not yet filled) or SUBMITTED (prompt sent) is also resumable.
    qs = PesapalTransaction.objects.filter(
        content_type=content_type, object_id=target.pk, purpose=purpose, created_at__gte=cutoff,
    ).filter(
        (Q(status=S.SUBMITTED) & ~Q(redirect_url=''))
        | Q(gateway=PaymentGatewaySettings.Gateway.ABLINER, status__in=[S.PENDING, S.SUBMITTED])
    )
    # Never resume a checkout created for a different provider. The provider is
    # pinned to each transaction; otherwise an ABLiner button can reopen an old
    # PesaPal iframe after the administrator switches the active gateway.
    if gateway is not None:
        qs = qs.filter(gateway=gateway)
    if description_prefix is not None:
        qs = qs.filter(description__startswith=description_prefix)
    if amount is not None:
        qs = qs.filter(amount=Decimal(str(amount)).quantize(Decimal('0.01')))
    return qs.order_by('-created_at').first()


def _site_base_url(pesapal_settings, request=None):
    if pesapal_settings.site_base_url:
        return pesapal_settings.site_base_url.rstrip('/')
    if request is not None:
        return f"{request.scheme}://{request.get_host()}"
    raise pesapal_client.PesapalError(
        "Online payment is not fully configured yet. Please use the manual payment option.",
        technical="Set 'Site base URL' in Pesapal Settings (Django Admin) so payments work outside a browser request.",
    )


def submit_to_pesapal(txn: PesapalTransaction, request=None) -> PesapalTransaction:
    """Submit through the gateway pinned to this transaction.

    The historical function name is retained for backward compatibility with
    existing views; it is now a gateway dispatcher rather than a PesaPal-only call.
    """
    if txn.gateway == PaymentGatewaySettings.Gateway.ABLINER:
        return submit_to_abliner(txn, request=request)
    return _submit_to_pesapal(txn, request=request)


def _submit_to_pesapal(txn: PesapalTransaction, request=None) -> PesapalTransaction:
    pesapal_settings = PesapalSettings.load()
    if not pesapal_settings.is_enabled:
        raise pesapal_client.PesapalError('Online payment is currently turned off. Please use the manual payment option.', technical='PesapalSettings.is_enabled is False.')
    if not pesapal_settings.is_configured:
        raise pesapal_client.PesapalError('Online payment is not configured yet. Please use the manual payment option, or contact support.', technical='PesapalSettings has no Consumer Key/Secret.')
    base = _site_base_url(pesapal_settings, request)
    ipn_url = base + reverse('payments:ipn')
    callback_url = base + reverse('payments:return')
    ipn_id = pesapal_client.ensure_ipn_registered(pesapal_settings, ipn_url)
    first_name, _, last_name = (txn.payer_name or '').strip().partition(' ')
    public_description = _PLAN_PREFIX_RE.sub('', txn.description or '') or txn.get_purpose_display()
    data = pesapal_client.submit_order(
        pesapal_settings, merchant_reference=txn.merchant_reference, amount=txn.amount,
        currency=txn.currency, description=public_description, callback_url=callback_url,
        cancellation_url=base + reverse('payments:cancel', kwargs={'merchant_reference': txn.merchant_reference}),
        redirect_mode='PARENT_WINDOW', ipn_id=ipn_id, email=txn.payer_email,
        phone_number=txn.payer_phone, first_name=first_name, last_name=last_name,
    )
    txn.order_tracking_id = str(data['order_tracking_id'])[:100]
    txn.redirect_url = str(data['redirect_url'])[:200]
    txn.status = PesapalTransaction.Status.SUBMITTED
    txn.error_message = ''
    txn.raw_response = redact_text(str(data))[:5000]
    txn.save(update_fields=['order_tracking_id','redirect_url','status','error_message','raw_response','updated_at'])
    return txn


MAX_ABLINER_ATTEMPTS = 3            # prompts per payment (first send + 2 re-sends)
ABLINER_RESEND_AFTER_SECONDS = 45   # a prompt is given this long before offering a re-send

_ABLINER_FAILED_WORDS = ('failed', 'cancelled', 'canceled', 'reversed', 'expired', 'rejected')


def abliner_attempts(txn):
    """How many payment prompts have been sent for this payment so far."""
    return PaymentEvent.objects.filter(
        payment=txn, gateway='ABLINER', event_type='PAYMENT_SUBMITTED',
    ).count()


def abliner_sent_seconds_ago(txn):
    """Seconds since the latest prompt was sent (0 when none)."""
    last = PaymentEvent.objects.filter(
        payment=txn, gateway='ABLINER', event_type='PAYMENT_SUBMITTED',
    ).order_by('-created_at').first()
    return max(0, int((timezone.now() - last.created_at).total_seconds())) if last else 0


def abliner_resend_wait(txn):
    """Seconds the customer must still wait before a re-send is allowed (0 = allowed now)."""
    last = PaymentEvent.objects.filter(
        payment=txn, gateway='ABLINER', event_type='PAYMENT_SUBMITTED',
    ).order_by('-created_at').first()
    if last is None:
        return 0
    elapsed = (timezone.now() - last.created_at).total_seconds()
    return max(0, int(ABLINER_RESEND_AFTER_SECONDS - elapsed))


def _recover_abliner_deposit(txn, key, attempts=3, pause=3):
    """Find a deposit ABLiner already created for this payment (after a timeout / 409). None if none."""
    for i in range(attempts):
        try:
            rows = abliner_client.get_transactions(idempotency_key=key) or \
                abliner_client.get_transactions(customer_reference=txn.merchant_reference)
        except Exception as exc:  # noqa: BLE001
            logger.warning('ABLiner recovery lookup failed for txn #%s: %s', txn.pk, exc)
            rows = []
        rows = [r for r in rows if isinstance(r, dict)]
        if rows:
            return rows[0]
        if i < attempts - 1:
            time.sleep(pause)
    return None


PHONE_PROMPT_LIMIT = 6


def submit_to_abliner(txn: PesapalTransaction, request=None, *, phone=None, network_key=None) -> PesapalTransaction:
    """
    Send the mobile-money prompt (USSD push) to the customer's phone.

    May be called again on the SAME payment (customer typed a wrong number, or no prompt
    arrived). Each send uses its own Idempotency-Key, so ABLiner treats it as a new
    prompt, while the shared customer reference lets one confirmation settle the payment.
    Raises ABLinerError (a PesapalError) with a customer-safe message; nothing is changed
    on the payment in that case except the diagnostic error text.
    """
    S = PesapalTransaction.Status
    if txn.status not in (S.PENDING, S.SUBMITTED):
        raise abliner_client.ABLinerError(
            'This payment can no longer be started. Please begin a new payment.',
            f'submit_to_abliner on txn #{txn.pk} in status {txn.status}', no_payment_created=True)
    msisdn = networks.normalize_msisdn(phone or txn.payer_phone)
    if not msisdn:
        raise abliner_client.ABLinerError(
            'Enter a valid mobile money number, for example 0712 345 678.',
            f'Invalid phone: {phone or txn.payer_phone!r}', code='invalid_phone', no_payment_created=True)

    # A payment form must never become a tool for pushing payment prompts at other people's
    # phones: at most PHONE_PROMPT_LIMIT prompts per number per hour across ALL payments.
    allowed, _retry = ratelimit.hit('abliner-phone', msisdn, limit=PHONE_PROMPT_LIMIT, window=3600)
    if not allowed:
        raise abliner_client.ABLinerError(
            'Too many payment requests were sent to this number. Please wait an hour or use another number.',
            f'txn #{txn.pk}: per-number prompt limit reached', code='phone_rate_limited', no_payment_created=True)

    attempt = abliner_attempts(txn)
    if attempt >= MAX_ABLINER_ATTEMPTS:
        raise abliner_client.ABLinerError(
            'The maximum number of payment prompts was reached. Please pay by card or contact support.',
            f'txn #{txn.pk}: {attempt} prompts already sent', no_payment_created=True)
    wait = abliner_resend_wait(txn)
    if attempt and wait:
        raise abliner_client.ABLinerError(
            f'Please wait {wait} seconds before asking for a new prompt.',
            f'txn #{txn.pk}: resend requested too early', no_payment_created=True)

    network = networks.network_by_key(network_key) or networks.detect_network(msisdn)
    callback_url = _site_base_url(PesapalSettings.load(), request) + reverse('payments:abliner_webhook')
    key = str(txn.merchant_reference) if attempt == 0 else f'{txn.merchant_reference}-{attempt}'
    try:
        try:
            data = abliner_client.create_mobile_deposit(
                amount=txn.amount, phone=msisdn, reference=str(txn.merchant_reference),
                callback_url=callback_url, idempotency_key=key,
            )
        except abliner_client.ABLinerError as first:
            # A timeout, or "409 request_in_progress" from a retry, does NOT mean the prompt failed:
            # ABLiner may have created it. Look it up before giving up, so the customer is never
            # told it failed (and never asked to pay twice) when the prompt is already on the phone.
            if first.code not in ('network_error', 'request_in_progress'):
                raise
            row = _recover_abliner_deposit(txn, key)
            if row is None:
                if first.code == 'request_in_progress':
                    first.customer_message = first.detail = abliner_client._FRIENDLY['request_in_progress']
                raise
            data = {'data': row}
    except abliner_client.ABLinerError as exc:
        PesapalTransaction.objects.filter(pk=txn.pk).update(error_message=exc.technical[:255], updated_at=timezone.now())
        PaymentEvent.objects.create(
            payment=txn, gateway='ABLINER', event_type='PAYMENT_SUBMIT_FAILED', success=False,
            message=exc.technical[:255], payload={'code': exc.code, 'no_payment_created': exc.no_payment_created},
        )
        raise

    payload = data.get('data') or {}
    txn.order_tracking_id = str(payload.get('id') or payload.get('reference') or txn.merchant_reference)[:100]
    txn.redirect_url = ''
    txn.payer_phone = msisdn
    txn.payment_method = (network['label'] if network else 'Mobile money')[:60]
    txn.status = S.SUBMITTED
    txn.error_message = ''
    txn.status_description = str(payload.get('message') or 'Awaiting approval on the phone')[:255]
    txn.raw_response = redact_text(json.dumps(data, default=str))[:5000]
    txn.save(update_fields=['order_tracking_id', 'redirect_url', 'payer_phone', 'payment_method', 'status',
                            'error_message', 'status_description', 'raw_response', 'updated_at'])
    PaymentEvent.objects.create(
        payment=txn, gateway='ABLINER', event_type='PAYMENT_SUBMITTED',
        message=f"Prompt #{attempt + 1} sent to {networks.mask_msisdn(msisdn)}"
                f"{' (' + network['label'] + ')' if network else ''}",
        payload=redact_payload(data),
    )
    return txn


def switch_to_pesapal(txn: PesapalTransaction, request=None) -> PesapalTransaction:
    """
    Move a payment that has NOT sent any mobile-money prompt over to Pesapal
    (customer chose to pay by card, or ABLiner could not start). Refused once a prompt
    exists, because the customer could then end up paying twice.
    """
    if txn.gateway != PaymentGatewaySettings.Gateway.ABLINER or txn.status != PesapalTransaction.Status.PENDING \
            or abliner_attempts(txn):
        raise pesapal_client.PesapalError(
            'This payment can no longer be switched to another method.',
            technical=f'switch_to_pesapal refused for txn #{txn.pk} ({txn.gateway}/{txn.status})')
    if not pesapal_usable():
        raise pesapal_client.PesapalError(
            'Card payment is not available right now. Please use mobile money or the manual option.',
            technical='switch_to_pesapal: Pesapal is not enabled/configured.')
    PesapalTransaction.objects.filter(pk=txn.pk).update(gateway=PaymentGatewaySettings.Gateway.PESAPAL, payment_method='')
    txn.refresh_from_db()
    PaymentEvent.objects.create(payment=txn, gateway='PESAPAL', event_type='GATEWAY_SWITCHED',
                                message='Customer chose to pay by card instead of mobile money.')
    return _submit_to_pesapal(txn, request=request)


# ===================================================== status refresh ===


def _abliner_pick(rows):
    """
    Collapse every prompt ever sent for one payment into ONE verdict.
    Completed beats everything (money arrived); a prompt that is still live beats an older
    prompt that expired; only when every prompt failed is the payment failed.
    """
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return None
    status_of = lambda r: str(r.get('status') or '').lower()  # noqa: E731
    for r in rows:
        if status_of(r) == 'completed':
            return r, 'COMPLETED'
    live = [r for r in rows if not any(w in status_of(r) for w in _ABLINER_FAILED_WORDS)]
    if live:
        return live[0], 'SUBMITTED'
    r = rows[0]
    word = status_of(r)
    verdict = 'REVERSED' if 'revers' in word else ('CANCELLED' if 'cancel' in word else 'FAILED')
    return r, verdict


def refresh_abliner_status(txn: PesapalTransaction, *, max_age_seconds=None) -> PesapalTransaction:
    try:
        if txn.status == PesapalTransaction.Status.COMPLETED and not txn.is_processed:
            _activate(txn)                       # paid earlier, activation failed: retry it only
            txn.refresh_from_db()
            return txn
        if max_age_seconds and txn.last_checked_at and \
                (timezone.now() - txn.last_checked_at).total_seconds() < max_age_seconds:
            return txn
        rows = abliner_client.get_transactions(customer_reference=txn.merchant_reference)
        if not rows and txn.order_tracking_id:
            rows = abliner_client.get_transactions(provider_id=txn.order_tracking_id)
        picked = _abliner_pick(rows)
        PesapalTransaction.objects.filter(pk=txn.pk).update(last_checked_at=timezone.now())
        if picked is None:
            txn.refresh_from_db()
            return txn
        data, verdict = picked
        normalized = {
            'merchant_reference': data.get('customer_reference') or data.get('reference'),
            'amount': data.get('amount'), 'currency': data.get('currency'),
            'payment_method': data.get('method'),
            'confirmation_code': data.get('provider_reference') or data.get('reference'),
            'status_code': 200, 'payment_status_description': str(data.get('status') or '').lower(),
            'status': verdict,
        }
        _apply_provider_status(txn.pk, normalized, gateway='ABLINER', raw=data)
        txn.refresh_from_db()
        if txn.status == PesapalTransaction.Status.COMPLETED and not txn.is_processed:
            _activate(txn)
            txn.refresh_from_db()
        return txn
    except Exception as exc:  # noqa: BLE001 - polling must never break the page
        logger.warning('ABLiner status refresh failed for txn #%s: %s', txn.pk, exc)
        PesapalTransaction.objects.filter(pk=txn.pk).update(
            error_message=str(exc)[:255], last_checked_at=timezone.now(), updated_at=timezone.now())
        return PesapalTransaction.objects.get(pk=txn.pk)


def _apply_provider_status(txn_pk, data, *, gateway, raw=None):
    with db_transaction.atomic():
        locked = PesapalTransaction.objects.select_for_update().get(pk=txn_pk)
        if locked.is_processed:
            return
        new_status = data.get('status') or 'SUBMITTED'
        if new_status == 'COMPLETED':
            problems = _verify_completed(locked, data)
            if problems:
                new_status = 'INVALID'
        else:
            problems = []
        locked.status = new_status
        locked.confirmation_code = str(data.get('confirmation_code') or '')[:100]
        provided_method = str(data.get('payment_method') or '')[:60]
        generic = provided_method.lower() in ('', 'mobile', 'mobile_money', 'mobile money')
        if not (gateway == 'ABLINER' and generic):      # keep "M-Pesa" / "Airtel Money" the customer chose
            locked.payment_method = provided_method
        locked.status_description = ('Verification failed: ' + '; '.join(problems))[:255] if problems else str(data.get('payment_status_description') or '')[:255]
        locked.provider_status_code = data.get('status_code')
        locked.last_checked_at = timezone.now()
        locked.raw_response = redact_text(json.dumps(raw, default=str))[:5000] if raw is not None else locked.raw_response
        locked.error_message = ''
        if new_status == 'COMPLETED' and not locked.completed_at:
            locked.completed_at = timezone.now()
        locked.save()
        PaymentEvent.objects.create(payment=locked, gateway=gateway, event_type='PAYMENT_STATUS', provider_status=new_status, message=locked.status_description, payload=redact_payload(raw or data))


def _verify_completed(txn, data):
    """Return a list of problems when a COMPLETED payload doesn't match what we asked for."""
    problems = []
    reference = str(data.get('merchant_reference') or '').strip().lower().replace('-', '')
    if reference and reference != str(txn.merchant_reference).lower().replace('-', ''):
        problems.append(f"merchant reference {reference} does not match")
    paid = pesapal_client.to_decimal(data.get('amount'))
    if paid is None:
        logger.warning("Pesapal status for txn #%s carried no amount; accepting on status alone.", txn.pk)
    elif paid + AMOUNT_TOLERANCE < txn.amount:
        problems.append(f"paid {paid} is less than the expected {txn.amount}")
    currency = str(data.get('currency') or '').strip().upper()
    if currency and currency != (txn.currency or '').upper():
        problems.append(f"currency {currency} does not match {txn.currency}")
    return problems


def _parse_provider_datetime(value):
    if not value or not isinstance(value, str):
        return None
    return pesapal_client._parse_expiry(value)


def refresh_status(txn: PesapalTransaction, *, max_age_seconds=None) -> PesapalTransaction:
    """
    Ask Pesapal for the authoritative status; activate on the FIRST verified completion only.

    `max_age_seconds` lets browser-driven callers (the polling page) avoid hammering
    Pesapal: if this transaction was checked more recently than that, return as is.
    IPN callbacks and admin re-checks leave it None and always check.
    Never raises: every failure is recorded on the transaction and logged.
    """
    try:
        txn.refresh_from_db()
    except PesapalTransaction.DoesNotExist:
        return txn
    if txn.is_processed or not txn.order_tracking_id:
        return txn

    if txn.gateway == PaymentGatewaySettings.Gateway.ABLINER:
        return refresh_abliner_status(txn, max_age_seconds=max_age_seconds)

    if max_age_seconds and txn.last_checked_at and (timezone.now() - txn.last_checked_at).total_seconds() < max_age_seconds:
        return txn

    # Paid + verified earlier but activation failed: retry the activation itself,
    # no need to bother Pesapal again.
    if txn.status == PesapalTransaction.Status.COMPLETED:
        _activate(txn)
        txn.refresh_from_db()
        return txn

    pesapal_settings = PesapalSettings.load()
    try:
        data = pesapal_client.get_transaction_status(pesapal_settings, txn.order_tracking_id)
    except pesapal_client.PesapalError as exc:
        PesapalTransaction.objects.filter(pk=txn.pk).update(
            error_message=exc.technical[:255], last_checked_at=timezone.now(), updated_at=timezone.now(),
        )
        txn.refresh_from_db()
        return txn
    except Exception:
        logger.exception("Unexpected error checking Pesapal status for txn #%s", txn.pk)
        return txn

    try:
        _apply_status(txn.pk, data)
    except Exception:
        logger.exception("Could not apply Pesapal status to txn #%s", txn.pk)
    txn.refresh_from_db()

    if txn.status == PesapalTransaction.Status.COMPLETED and not txn.is_processed:
        _activate(txn)
        txn.refresh_from_db()
    return txn


def _apply_status(txn_pk, data):
    """Write the provider's answer onto the row under a lock (no stale overwrites)."""
    with db_transaction.atomic():
        locked = PesapalTransaction.objects.select_for_update().get(pk=txn_pk)
        if locked.is_processed:
            return
        new_status = pesapal_client.interpret_status(data, created_at=locked.created_at)
        problems = []
        if new_status == PesapalTransaction.Status.COMPLETED:
            problems = _verify_completed(locked, data)
            if problems:
                new_status = PesapalTransaction.Status.INVALID

        locked.status = new_status
        locked.confirmation_code = str(data.get('confirmation_code') or '')[:100]
        locked.payment_method = str(data.get('payment_method') or '')[:60]
        description = str(data.get('payment_status_description') or '')[:255]
        locked.status_description = ("Verification failed: " + '; '.join(problems))[:255] if problems else description
        code = data.get('status_code')
        try:
            locked.provider_status_code = int(code) if code not in (None, '') else None
        except (TypeError, ValueError):
            locked.provider_status_code = None
        locked.payment_account = str(data.get('payment_account') or '')[:80]
        locked.provider_created_at = _parse_provider_datetime(data.get('created_date'))
        locked.last_checked_at = timezone.now()
        locked.raw_response = redact_text(str(data))[:5000]
        locked.error_message = ''
        if new_status == PesapalTransaction.Status.COMPLETED and not locked.completed_at:
            locked.completed_at = timezone.now()
        locked.save()
        PaymentEvent.objects.create(payment=locked, gateway='PESAPAL', event_type='PAYMENT_STATUS', provider_status=new_status, message=locked.status_description, payload=redact_payload(data))

    if problems:
        logger.error("Pesapal payment verification failed for txn #%s: %s", txn_pk, problems)
        _alert_superadmins(
            title='ACTION NEEDED: Pesapal payment did not match the order',
            message=(f"Pesapal reported payment {locked.merchant_reference} as completed, but it was NOT activated "
                     f"because: {'; '.join(problems)}. Check it in Django Admin before activating anything by hand."),
            txn=locked, dedupe=f"pesapal-mismatch:{locked.pk}",
        )


# ============================================================ activation ===

def _target_for(txn):
    target = txn.target
    if target is None:
        raise ActivationError("The record this payment was for no longer exists.")
    return target


def _activate(txn: PesapalTransaction) -> bool:
    """
    Run the right activation exactly once. Returns True when the transaction is
    now fully processed.

    The lock + single atomic block guarantee that two near-simultaneous callers
    (IPN + browser poll) never double-activate, and that a failure rolls back
    EVERYTHING the handler touched.
    """
    applied = True
    try:
        with db_transaction.atomic():
            locked = PesapalTransaction.objects.select_for_update().get(pk=txn.pk)
            if locked.is_processed:
                return True
            if locked.status != PesapalTransaction.Status.COMPLETED:
                return False          # e.g. reversed while we were waiting for the lock
            handler = _HANDLERS.get(locked.purpose)
            if handler is None:
                raise ActivationError("No activation handler is registered for this payment type.")
            result = handler(locked)
            applied = result is not False
            locked.processed_at = timezone.now()
            locked.error_message = ''
            locked.save(update_fields=['processed_at', 'error_message', 'updated_at'])
    except Exception as exc:
        logger.exception("Failed to activate PesapalTransaction #%s (%s)", txn.pk, txn.purpose)
        message = f"Paid, but activation failed: {exc}"[:255]
        PesapalTransaction.objects.filter(pk=txn.pk).update(
            error_message=message, last_checked_at=timezone.now(), updated_at=timezone.now(),
        )
        _alert_activation_failed(txn, str(exc))
        _sms_platform_owner(txn, outcome='failed')
        return False

    txn.refresh_from_db()
    if applied:
        _notify_owner_activation_succeeded(txn)
        _notify_customer_payment_received(txn)
        _send_payment_receipt_sms(txn)
        _sms_platform_owner(txn)
    else:
        _sms_platform_owner(txn, outcome='duplicate')
        _alert_superadmins(
            title='Possible duplicate payment (Pesapal)',
            message=(f"{_who(txn)} paid TZS {txn.amount:,.0f} for {txn.get_purpose_display()} "
                     f"(ref {txn.merchant_reference}), but that item had ALREADY been activated, so nothing new "
                     "was applied. Please review and refund or credit the customer if appropriate."),
            txn=txn, dedupe=f"pesapal-duplicate:{txn.pk}",
        )
    return True


def _superadmins():
    from accounts.models import User
    return User.objects.filter(is_superuser=True, is_active=True)


def _who(txn):
    return txn.payer_name or (txn.owner.get_full_name() if txn.owner_id else '') or txn.payer_phone or 'A customer'


def _alert_superadmins(*, title, message, txn, dedupe='', level=None):
    """Best-effort: an alert that fails must never break payment processing."""
    try:
        from notifications.models import Notification
        from notifications.utils import notify_many
        notify_many(
            _superadmins(), title, message,
            category=Notification.Category.SYSTEM,
            level=level or Notification.Level.WARNING,
            link_url=reverse('admin:payments_pesapaltransaction_change', args=[txn.pk]),
            dedupe_key=dedupe,
        )
    except Exception:
        logger.exception("Could not deliver superadmin alert %r for txn #%s", title, txn.pk)


def _notify_owner_activation_succeeded(txn):
    """Purely informational — the customer already has access."""
    try:
        from notifications.models import Notification
        from notifications.utils import notify_many
        notify_many(
            _superadmins(), f'Online payment received ({txn.gateway.title()})',
            f"{_who(txn)} paid TZS {txn.amount:,.0f} for {txn.get_purpose_display()} via {txn.gateway.title()} "
            f"({txn.payment_method or 'online'}). Already activated automatically — no action needed.",
            category=Notification.Category.SYSTEM, level=Notification.Level.SUCCESS,
            link_url=reverse('admin:payments_pesapaltransaction_change', args=[txn.pk]),
            dedupe_key=f"pesapal-activated:{txn.pk}",
        )
    except Exception:
        logger.exception("Could not notify owner of a successful Pesapal activation for txn #%s", txn.pk)


def _alert_activation_failed(txn, error_detail):
    """The customer PAID but our activation step failed — the one case where money moved
    and nothing happened for the customer, so it must reach the platform owner."""
    _alert_superadmins(
        title='ACTION NEEDED: Pesapal payment could not be activated',
        message=(f"{_who(txn)} paid TZS {txn.amount:,.0f} for {txn.get_purpose_display()} (ref {txn.merchant_reference}) "
                 f"and Pesapal confirmed it, but activating it in the system failed: {error_detail}. "
                 "Nothing was half-applied. Fix the cause, then use 'Re-check status with Pesapal now' on this "
                 "transaction in Django Admin (it retries the activation), or activate it manually from the HQ page."),
        txn=txn, dedupe=f"pesapal-actfail:{txn.pk}:{hashlib.md5(error_detail.encode('utf-8', 'ignore')).hexdigest()[:10]}",
    )


def _reference(txn):
    provider = 'ABLiner' if txn.gateway == PaymentGatewaySettings.Gateway.ABLINER else 'PesaPal'
    return f"{provider} online payment — {txn.confirmation_code or txn.order_tracking_id}"


# ------------------------------------------------------------- handlers ---
# Each handler returns True when it applied something, False when the target had
# already been activated (-> duplicate-payment alert), or raises ActivationError.

def _activate_subscription(txn):
    from hq.models import SignupRequest
    from hq.services import activate_signup_from_payment
    signup = _target_for(txn)
    if signup.status != SignupRequest.Status.PENDING_PAYMENT:
        return False
    activate_signup_from_payment(signup, reference_note=_reference(txn), amount_paid=txn.amount)
    return True


def _activate_subscription_renewal(txn):
    from hq.services import renew_subscription_from_payment
    subscription = _target_for(txn)
    # The requested tier and billing period are stashed as "[TIER|PLAN] ..." so
    # access only changes after Pesapal confirms payment.
    tier = plan = None
    if txn.description.startswith('[') and ']' in txn.description:
        token = txn.description[1:txn.description.index(']')]
        if '|' in token:
            tier, plan = token.split('|', 1)
        else:
            plan = token
    renew_subscription_from_payment(
        subscription, plan=plan, tier=tier, reference_note=_reference(txn), amount_paid=txn.amount,
    )
    return True


def _activate_branch_addon(txn):
    addon = _target_for(txn)
    activated = addon.activate_from_payment(reference=_reference(txn))
    if not activated:
        if addon.status == addon.Status.ACTIVE:
            return False
        raise ActivationError(f"Branch '{addon.pharmacy.name}' is {addon.get_status_display()} and cannot be activated by this payment.")
    _after_commit(lambda: _notify_branch(addon, renewed=False))
    return True


def _activate_branch_addon_renewal(txn):
    addon = _target_for(txn)
    if not addon.renew_from_payment(reference=_reference(txn)):
        raise ActivationError(f"Branch '{addon.pharmacy.name}' is {addon.get_status_display()} and cannot be renewed.")
    _after_commit(lambda: _notify_branch(addon, renewed=True))
    return True


def _notify_branch(addon, *, renewed):
    try:
        from notifications.models import Notification
        from notifications.utils import notify_many
        if renewed:
            title = 'Branch renewed'
            body = (f"Your branch '{addon.pharmacy.name}' has been renewed online until "
                    f"{addon.next_billing_date:%d %b %Y}.")
            sms = f"SmartPOS - Payment received\nBranch '{addon.pharmacy.name}' renewed until {addon.next_billing_date:%d %b %Y}."
        else:
            title = 'Branch activated'
            body = f"Your branch '{addon.pharmacy.name}' has been paid for online and is now active."
            sms = f"SmartPOS - Payment received\nBranch '{addon.pharmacy.name}' is now active."
        notify_many([addon.owner], title, body, category=Notification.Category.SYSTEM,
                    level=Notification.Level.SUCCESS, link_url=reverse('pharmacies:list'))
        from sms_integration.utils import with_security_notice
        _sms_best_effort(addon.owner.phone_number, with_security_notice(sms))
    except Exception:
        logger.exception("Could not notify branch owner for add-on #%s", addon.pk)


def _activate_purchase(txn, approve_func_name, module):
    import importlib
    from sms_integration.services import ONLINE_CLOSED_NOTE
    purchase = _target_for(txn)
    # The customer paid after we had auto-closed their abandoned checkout (e.g. they
    # returned to the Pesapal page later): the money is real, so re-open and honour it.
    if purchase.status == purchase.Status.REJECTED and (purchase.admin_note or '').startswith(ONLINE_CLOSED_NOTE):
        type(purchase).objects.filter(pk=purchase.pk).update(
            status=purchase.Status.PENDING, reviewed_at=None, admin_note='',
        )
        purchase.refresh_from_db()
    if purchase.status != purchase.Status.PENDING:
        return False
    approve = getattr(importlib.import_module(module), approve_func_name)
    approve(purchase, reviewed_by=None, reference=_reference(txn))
    return True


def _activate_sms_debt_package(txn):
    return _activate_purchase(txn, 'approve_debt_sms_purchase', 'sms_integration.services')


def _activate_sms_receipt_package(txn):
    return _activate_purchase(txn, 'approve_receipt_sms_purchase', 'sms_integration.services')


def _activate_sms_marketing_package(txn):
    return _activate_purchase(txn, 'approve_marketing_sms_purchase', 'sms_integration.services')


def _activate_daily_report_package(txn):
    return _activate_purchase(txn, 'approve_daily_report_purchase', 'daily_reports.services')

def _activate_layaway_sms_package(txn):
    purchase = _target_for(txn)
    if purchase is None:
        raise ActivationError('Layaway SMS purchase no longer exists.')
    from layaway.services import approve_layaway_sms_purchase
    return approve_layaway_sms_purchase(purchase, reviewed_by=None, reference=_reference(txn))


def _activate_marketplace_service(txn):
    from marketplace.models import OnlineServiceSubscription
    target = _target_for(txn)
    if not isinstance(target, OnlineServiceSubscription):
        raise ActivationError('Marketplace service subscription record is missing.')
    if target.status == OnlineServiceSubscription.Status.ACTIVE and target.period_end and target.period_end > timezone.now():
        return True
    if Decimal(str(txn.amount)) != Decimal(str(target.monthly_amount)):
        raise ActivationError('Marketplace service payment amount does not match the requested plan.')
    now = timezone.now()
    target.status = OnlineServiceSubscription.Status.ACTIVE
    target.period_start = now
    target.period_end = now + timedelta(days=30)
    target.last_payment_reference = str(txn.order_tracking_id or txn.merchant_reference)
    target.save(update_fields=['status','period_start','period_end','last_payment_reference','updated_at'])
    # Mark the customer request active only after the payment service has verified the provider transaction.
    try:
        from marketplace.models import OnlineServiceRequest
        service_request = OnlineServiceRequest.objects.filter(subscription=target).first()
        if service_request:
            service_request.status = OnlineServiceRequest.Status.ACTIVE
            service_request.reviewed_at = now
            service_request.admin_note = 'Automatically activated after verified payment.'
            service_request.save(update_fields=['status', 'reviewed_at', 'admin_note', 'updated_at'])
            from notifications.utils import notify
            from notifications.models import Notification
            if service_request.requested_by:
                notify(service_request.requested_by, title='Online service activated',
                    message=f'{target.store.public_name}: {target.get_service_display()} is now active for 30 days.',
                    category=Notification.Category.SYSTEM, level=Notification.Level.SUCCESS,
                    link_url='/marketplace/seller/', pharmacy=target.store.pharmacy,
                    dedupe_key=f'marketplace-service-paid:{service_request.pk}')
    except Exception:
        logger.exception('Unable to update Marketplace service request after payment activation')
    if target.service == OnlineServiceSubscription.Service.ONLINE_SELLING:
        target.store.is_published = True
        target.store.save(update_fields=['is_published','updated_at'])
    elif target.service == OnlineServiceSubscription.Service.ORDER_SMS:
        target.sms_used = 0
        target.save(update_fields=['sms_used','updated_at'])
    return True


def _activate_marketplace_order(txn):
    """Create the official Marketplace order only after the gateway confirms payment."""
    from marketplace.models import PaymentAttempt, Order, OrderItem, Product
    attempt = _target_for(txn)
    if not isinstance(attempt, PaymentAttempt):
        raise ActivationError('Marketplace checkout snapshot is missing.')
    if attempt.status == PaymentAttempt.Status.PAID:
        return True
    if txn.status != PesapalTransaction.Status.COMPLETED or Decimal(str(txn.amount)) != Decimal(str(attempt.total)):
        raise ActivationError('Marketplace payment amount does not match the checkout total.')
    rows = attempt.items_snapshot or []
    ids = [int(row['product_id']) for row in rows]
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=ids, is_active=True, category__is_active=True)}
    for row in rows:
        product = products.get(int(row['product_id']))
        if not product or int(row['quantity']) > product.available_qty:
            raise ActivationError('Payment received, but stock needs a manual review before fulfilment.')
    order, created = Order.objects.get_or_create(
        reference=attempt.reference,
        defaults={
            'seller_store': attempt.seller_store,
            'customer_name': attempt.customer_name, 'phone': (attempt.phone or txn.payer_phone),
            'location': attempt.location, 'notes': attempt.notes,
            'total': attempt.total, 'payment_status': Order.PaymentStatus.PAID,
            'payment_method': txn.payment_method or 'Mobile Money',
            'payment_reference': txn.order_tracking_id or str(txn.merchant_reference),
            'paid_at': timezone.now(),
        },
    )
    if created:
        OrderItem.objects.bulk_create([
            OrderItem(order=order, product=products[int(row['product_id'])], name=row['name'],
                      quantity=int(row['quantity']), unit_price=Decimal(str(row['unit_price'])),
                      is_digital=products[int(row['product_id'])].is_digital)
            for row in rows
        ])
        for row in rows:
            product = products[int(row['product_id'])]
            if product.is_digital:
                continue  # downloads have no shelf stock
            product.stock -= int(row['quantity'])
            product.save(update_fields=['stock', 'updated_at'])
        try:
            from marketplace import orders as marketplace_orders
            marketplace_orders.record_created(order)
            marketplace_orders.provision_digital(order)
            db_transaction.on_commit(lambda: marketplace_orders.notify_new_order(order))
        except Exception:
            logger.warning('Could not record/notify new Marketplace order %s', order.pk, exc_info=True)
        # Optional seller-paid SMS add-on: buyer payment/receipt SMS remains system-funded.
        try:
            from marketplace.models import OnlineServiceSubscription
            store_ids = {products[int(row['product_id'])].seller_store_id for row in rows if products[int(row['product_id'])].seller_store_id}
            for store_id in store_ids:
                store = next((products[int(row['product_id'])].seller_store for row in rows if products[int(row['product_id'])].seller_store_id == store_id), None)
                if not store or not store.pharmacy.phone_number:
                    continue
                addon = OnlineServiceSubscription.objects.select_for_update().filter(store=store, service=OnlineServiceSubscription.Service.ORDER_SMS, status=OnlineServiceSubscription.Status.ACTIVE, period_end__gt=timezone.now()).first()
                if not addon or addon.sms_used >= addon.sms_quota:
                    continue
                def _send_seller_order_alert(store=store, addon_id=addon.pk, order_ref=str(order.reference)):
                    try:
                        from sms_integration.models import SMSLog
                        from sms_integration.utils import send_sms
                        sent = send_sms(store.pharmacy.phone_number, f'SmartPOS Marketplace: New paid order {order_ref[:8]} for TSh {order.total:,.0f}. Open Seller Centre to manage the order.', category=SMSLog.Category.OTHER, owner=None, consume_wallet=False)
                        if sent:
                            OnlineServiceSubscription.objects.filter(pk=addon_id, sms_used__lt=F('sms_quota')).update(sms_used=F('sms_used') + 1)
                    except Exception:
                        logger.warning('Marketplace seller order SMS failed for store %s', store.pk, exc_info=True)
                db_transaction.on_commit(_send_seller_order_alert)
        except Exception:
            logger.warning('Could not prepare Marketplace seller SMS alert.', exc_info=True)
    attempt.status = PaymentAttempt.Status.PAID
    attempt.payment_reference = str(txn.order_tracking_id or txn.merchant_reference)[:120]
    attempt.save(update_fields=['status', 'payment_reference', 'updated_at'])
    return True


def _after_commit(func):
    """Run `func` once the surrounding transaction commits (immediately if none is open),
    so SMS / notifications are never sent for work that later rolls back."""
    db_transaction.on_commit(func)


def _sms_best_effort(phone, message):
    try:
        from sms_integration.models import SMSLog
        from sms_integration.utils import send_sms
        send_sms(phone, message, category=SMSLog.Category.OTHER, owner=None, consume_wallet=False)
    except Exception:
        logger.warning("Best-effort SMS to %s failed", phone, exc_info=True)


def _owner_alert_phones():
    """Platform-owner numbers for payment alerts: the saved owner notification phone,
    else the phone of each active superadmin. De-duplicated, never raises."""
    phones = []
    try:
        from hq.models import PaymentSettings
        saved = (getattr(PaymentSettings.load(), 'owner_notification_phone', '') or '').strip()
        if saved:
            phones.append(saved)
        else:
            phones += [(u.phone_number or '').strip() for u in _superadmins()]
    except Exception:
        logger.warning("Could not resolve platform-owner alert phones", exc_info=True)
    return list(dict.fromkeys(p for p in phones if p))


def _sms_platform_owner(txn, *, outcome='received'):
    """SMS the platform owner about a confirmed payment. Best-effort, system-funded."""
    try:
        who = _who(txn)
        ref = txn.confirmation_code or txn.merchant_reference
        base = f"TZS {txn.amount:,.0f} from {who} for {txn.get_purpose_display()} via {txn.gateway.title()}"
        if outcome == 'failed':
            text = f"SmartPOS ACTION NEEDED: Paid but NOT activated. {base}. Ref: {ref}. Check Django Admin."
        elif outcome == 'duplicate':
            text = f"SmartPOS: Duplicate payment. {base}. Ref: {ref}. Already active, review for refund."
        else:
            text = f"SmartPOS: Payment received. {base}. Ref: {ref}."
        for phone in _owner_alert_phones():
            _sms_best_effort(phone, text[:300])
    except Exception:
        logger.warning("Platform-owner payment SMS failed for txn #%s", txn.pk, exc_info=True)


def _active_until_text(txn):
    """'20 Nov 2026' for purchases that extend a subscription, else ''. Never raises."""
    try:
        P = PesapalTransaction.Purpose
        target = _target_for(txn)
        if txn.purpose == P.SUBSCRIPTION_RENEWAL:
            return target.end_date.strftime('%d %b %Y')
        if txn.purpose in (P.BRANCH_ADDON, P.BRANCH_ADDON_RENEWAL) and target.next_billing_date:
            return target.next_billing_date.strftime('%d %b %Y')
    except Exception:
        logger.debug("Could not work out the active-until date for txn #%s", txn.pk, exc_info=True)
    return ''


def _notify_customer_payment_received(txn):
    """In-app confirmation for the paying customer (the SMS may not arrive). Best-effort."""
    try:
        if not txn.owner_id or getattr(txn.owner, 'is_superuser', False):
            return
        from notifications.models import Notification
        from notifications.utils import notify_many
        until = _active_until_text(txn)
        body = (f"We received TZS {txn.amount:,.0f} for {txn.get_purpose_display()}."
                + (f" Active until {until}." if until else "")
                + f" Reference: {txn.confirmation_code or txn.merchant_reference}.")
        notify_many(
            [txn.owner], 'Payment received', body,
            category=Notification.Category.SYSTEM, level=Notification.Level.SUCCESS,
            link_url=reverse('payments:receipt', kwargs={'merchant_reference': txn.merchant_reference}),
            dedupe_key=f"payment-received:{txn.pk}",
        )
    except Exception:
        logger.exception("Could not notify the customer of payment #%s", txn.pk)


def _send_payment_receipt_sms(txn):
    """Send a system-funded payment confirmation with a public, unguessable receipt link."""
    try:
        # The activation lock makes this run once per successfully applied transaction.
        if not txn.payer_phone and txn.owner_id:
            phone = getattr(txn.owner, 'phone_number', '') or ''
        else:
            phone = txn.payer_phone or ''
        phone = str(phone).strip()
        if not phone:
            logger.info("Payment #%s confirmed without a payer phone; receipt SMS skipped.", txn.pk)
            return

        base = (PesapalSettings.load().site_base_url or '').strip().rstrip('/')
        if not base:
            configured = getattr(settings, 'SITE_URL', '') or ''
            base = str(configured).strip().rstrip('/')
        if not base:
            hosts = getattr(settings, 'ALLOWED_HOSTS', [])
            host = next((h for h in hosts if h and '*' not in h and not h.startswith('.')), '')
            if host:
                base = 'https://' + host
        if not base:
            logger.warning("Payment receipt SMS skipped for txn #%s: public site URL is not configured.", txn.pk)
            return

        receipt_url = base + reverse('payments:receipt', kwargs={'merchant_reference': txn.merchant_reference})
        paid_at = timezone.localtime(txn.completed_at or timezone.now()).strftime('%d %b %Y %H:%M')
        ref = str(txn.confirmation_code or txn.merchant_reference)
        active_until = _active_until_text(txn)
        message = (
            "SmartPOS - Payment received\n"
            f"Amount: TZS {txn.amount:,.0f}\n"
            f"For: {txn.get_purpose_display()}\n"
            + (f"Active until: {active_until}\n" if active_until else "")
            + f"Ref: {ref}\n"
            f"Date: {paid_at}\n"
            f"Receipt: {receipt_url}"
        )
        from sms_integration.models import SMSLog
        from sms_integration.utils import send_sms, with_security_notice
        message = with_security_notice(message)
        send_sms(phone, message[:1000], category=SMSLog.Category.OTHER, owner=None, consume_wallet=False)
        PaymentEvent.objects.create(
            payment=txn, gateway=txn.gateway, event_type='PAYMENT_RECEIPT_SMS_SENT',
            provider_status='sent', message=f'Payment confirmation and receipt link sent to {phone[-4:]}',
        )
    except Exception:
        logger.warning("Payment confirmation SMS failed for txn #%s.", txn.pk, exc_info=True)
        try:
            PaymentEvent.objects.create(
                payment=txn, gateway=txn.gateway, event_type='PAYMENT_RECEIPT_SMS_FAILED',
                success=False, provider_status='failed',
                message='Payment succeeded; system-funded receipt SMS delivery failed.',
            )
        except Exception:
            logger.exception("Could not record payment receipt SMS failure for txn #%s.", txn.pk)


_HANDLERS = {
    PesapalTransaction.Purpose.SUBSCRIPTION: _activate_subscription,
    PesapalTransaction.Purpose.SUBSCRIPTION_RENEWAL: _activate_subscription_renewal,
    PesapalTransaction.Purpose.BRANCH_ADDON: _activate_branch_addon,
    PesapalTransaction.Purpose.BRANCH_ADDON_RENEWAL: _activate_branch_addon_renewal,
    PesapalTransaction.Purpose.SMS_DEBT_PACKAGE: _activate_sms_debt_package,
    PesapalTransaction.Purpose.SMS_RECEIPT_PACKAGE: _activate_sms_receipt_package,
    PesapalTransaction.Purpose.SMS_MARKETING_PACKAGE: _activate_sms_marketing_package,
    PesapalTransaction.Purpose.DAILY_REPORT_PACKAGE: _activate_daily_report_package,
    PesapalTransaction.Purpose.LAYAWAY_SMS_PACKAGE: _activate_layaway_sms_package,
    PesapalTransaction.Purpose.MARKETPLACE_ORDER: _activate_marketplace_order,
    PesapalTransaction.Purpose.MARKETPLACE_ONLINE_SELLING: _activate_marketplace_service,
    PesapalTransaction.Purpose.MARKETPLACE_ORDER_SMS: _activate_marketplace_service,
}
