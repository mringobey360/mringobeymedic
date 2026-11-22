"""
ABLiner API client (https://abliner.net/api/v1).

* Every POST /deposits is HMAC-signed with the webhook signing secret and carries an
  Idempotency-Key (required for API keys created from 5 Oct 2026; harmless for older ones).
* ABLinerError is a PesapalError subclass, so every existing `except PesapalError`
  in the payment views already shows the customer a clean message instead of a 500.
* `no_payment_created` tells the caller whether it is SAFE to try another provider:
  True  -> ABLiner answered with an error, so no payment prompt was sent;
  False -> timeout / dropped connection, so a prompt MAY have been sent (never fail over).
"""
import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal, InvalidOperation

import requests

from .models import PaymentGatewaySettings
from .pesapal_client import PesapalError

logger = logging.getLogger('payments.abliner')
TIMEOUT = 20
# Creating a deposit makes ABLiner talk to the mobile network (USSD push); it can take well over 20 s.
# (connect, read). Gunicorn's own timeout is 120 s, so 60 s is safe.
DEPOSIT_TIMEOUT = (10, 60)

MIN_AMOUNT = 500
MAX_AMOUNT = 3_000_000

UNAVAILABLE = 'Mobile money payment is temporarily unavailable. Please try again in a moment.'

# ABLiner error `code` -> message that is safe and useful to show the customer.
_FRIENDLY = {
    'invalid_phone': 'That number is not a valid mobile money number. Please check it and try again.',
    'amount_out_of_range': f'Online payment must be between TZS {MIN_AMOUNT:,} and TZS {MAX_AMOUNT:,}.',
    'unsupported_request': 'This mobile network is not available for online payment right now. Try another number or pay by card.',
    'provider_error': 'Your mobile network did not respond. Please try again in a minute.',
    'rate_limited': 'Too many attempts. Please wait a minute and try again.',
    'request_in_progress': 'Your payment request is still being processed. Check your phone and approve the prompt, then wait a few seconds. Please do not pay again.',
}
# Codes that mean OUR configuration is wrong (never the customer's fault).
_CONFIG_CODES = {
    'unauthorized', 'invalid_request_signature', 'webhook_secret_required',
    'idempotency_key_required', 'gateway_not_configured', 'invalid_json',
}


class ABLinerError(PesapalError):
    def __init__(self, customer_message, technical='', *, code='', retryable=False,
                 no_payment_created=False, transaction_id=''):
        super().__init__(customer_message, technical=technical or customer_message)
        self.customer_message = self.detail
        self.code = code
        self.retryable = retryable
        self.no_payment_created = no_payment_created
        self.transaction_id = transaction_id


def is_usable():
    """True when ABLiner is switched on AND has both secrets. Never raises."""
    try:
        cfg = PaymentGatewaySettings.load()
        return bool(cfg.abliner_enabled and cfg.abliner_configured)
    except Exception:  # noqa: BLE001
        return False


def settings_obj():
    cfg = PaymentGatewaySettings.load()
    if not cfg.abliner_enabled or not cfg.abliner_configured:
        raise ABLinerError(UNAVAILABLE, 'ABLiner is not enabled or has no API key / webhook secret.',
                           code='gateway_not_configured', no_payment_created=True)
    return cfg


def _headers(cfg, body=None, idempotency_key=None):
    h = {'Authorization': f'Bearer {cfg.abliner_api_key}', 'Content-Type': 'application/json',
         'Accept': 'application/json'}
    if idempotency_key:
        h['Idempotency-Key'] = str(idempotency_key)
    if body is not None:
        ts = str(int(time.time()))
        raw = body if isinstance(body, bytes) else body.encode('utf-8')
        sig = hmac.new(cfg.abliner_webhook_secret.encode(), ts.encode() + b'.' + raw, hashlib.sha256).hexdigest()
        h['x-abliner-timestamp'] = ts
        h['x-abliner-signature'] = sig
    return h


def _request(cfg, method, path, **kwargs):
    try:
        r = requests.request(method, f'{cfg.abliner_base_url}/api/v1{path}', timeout=kwargs.pop('timeout', TIMEOUT), **kwargs)
    except requests.RequestException as exc:
        # Timeout / connection dropped: we cannot know whether a prompt was sent.
        raise ABLinerError(UNAVAILABLE, f'ABLiner network error: {exc}', code='network_error',
                           retryable=True, no_payment_created=False) from exc
    try:
        data = r.json()
        if not isinstance(data, (dict, list)):
            data = {}
    except ValueError:
        data = {}
    if 200 <= r.status_code < 300:
        return data
    body = data if isinstance(data, dict) else {}
    code = str(body.get('code') or '')
    if r.status_code == 429:
        code = code or 'rate_limited'
    tx_id = str(body.get('transaction_id') or '')
    technical = f"ABLiner HTTP {r.status_code} {code}: {body.get('message') or ''}".strip()
    if code in _CONFIG_CODES or r.status_code in (401, 403):
        logger.error('ABLiner configuration problem: %s', technical)
        customer = UNAVAILABLE
    else:
        customer = _FRIENDLY.get(code) or UNAVAILABLE
    raise ABLinerError(
        customer, technical, code=code, retryable=bool(body.get('retryable')) or r.status_code >= 500,
        no_payment_created=not tx_id, transaction_id=tx_id,
    )


def health_check():
    cfg = settings_obj()
    return _request(cfg, 'GET', '/balance', headers=_headers(cfg))


def create_mobile_deposit(*, amount, phone, reference, callback_url, idempotency_key):
    """Send a Mobile Money USSD push. `phone` must already be 255XXXXXXXXX."""
    cfg = settings_obj()
    try:
        value = int(Decimal(str(amount)))
    except (InvalidOperation, ValueError):
        raise ABLinerError('This payment amount is not valid.', f'Invalid amount: {amount}',
                           code='validation_failed', no_payment_created=True)
    if value < MIN_AMOUNT or value > MAX_AMOUNT:
        raise ABLinerError(_FRIENDLY['amount_out_of_range'], f'ABLiner amount outside limits: {value}',
                           code='amount_out_of_range', no_payment_created=True)
    body = json.dumps(
        {'amount': value, 'method': 'mobile', 'currency': 'TZS', 'phone': phone,
         'reference': reference, 'callback_url': callback_url},
        separators=(',', ':'),
    )
    # `data=body` sends exactly the string that was signed.
    return _request(cfg, 'POST', '/deposits', data=body, timeout=DEPOSIT_TIMEOUT,
                    headers=_headers(cfg, body=body, idempotency_key=idempotency_key))


def get_transactions(*, customer_reference=None, provider_id=None, idempotency_key=None):
    """Look transactions up. Returns the list ABLiner sends back (may be empty)."""
    cfg = settings_obj()
    params = {}
    if customer_reference:
        params['customer_reference'] = str(customer_reference)
    if provider_id:
        params['id'] = str(provider_id)
    if idempotency_key:
        params['idempotency_key'] = str(idempotency_key)
    result = _request(cfg, 'GET', '/transactions', params=params, headers=_headers(cfg))
    rows = result.get('data') if isinstance(result, dict) else result
    if isinstance(rows, dict):
        rows = [rows]
    return rows if isinstance(rows, list) else []


def verify_webhook(raw_body, signature, timestamp):
    cfg = PaymentGatewaySettings.load()
    if not cfg.abliner_webhook_secret or not signature or not timestamp:
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - ts) > 300:
        return False
    expected = hmac.new(cfg.abliner_webhook_secret.encode(), f'{ts}.'.encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
