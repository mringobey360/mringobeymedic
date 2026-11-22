"""
Thin, defensive wrapper around the Pesapal API 3.0 (JSON REST) endpoints:
  Auth/RequestToken, URLSetup/RegisterIPN, Transactions/SubmitOrderRequest,
  Transactions/GetTransactionStatus.

Design rules
------------
* Every failure — network, HTTP, malformed JSON, provider error body — is
  turned into a PesapalError. Callers never see a raw ``requests`` exception,
  a KeyError or an AttributeError from a surprise response shape.
* ``PesapalError.detail``     -> safe to show to a customer.
* ``PesapalError.technical``  -> provider/diagnostic text for logs, admin and
                                  ``PesapalTransaction.error_message`` only.
* Credentials and environment come from PesapalSettings (Django Admin);
  nothing is hardcoded.
* The bearer token is cached on the PesapalSettings row (not Django's cache)
  so it is correct across several Gunicorn workers without Redis.
* Only idempotent calls (token, RegisterIPN, GetTransactionStatus) are retried
  on transient failures. SubmitOrderRequest is NEVER blindly retried: a lost
  response could otherwise create two orders for one customer click.
"""
import logging
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

import requests
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from .models import PesapalSettings

logger = logging.getLogger('payments.pesapal')

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30
REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)

DEFAULT_TOKEN_LIFETIME_SECONDS = 5 * 60   # Pesapal tokens live ~5 minutes
TOKEN_SAFETY_MARGIN_SECONDS = 45          # refresh a little before real expiry
MAX_SAFE_ATTEMPTS = 2                     # 1 try + 1 retry for idempotent calls

GENERIC_UNREACHABLE = "Could not reach Pesapal right now. Please try again in a moment."
GENERIC_REJECTED = "Pesapal could not process this request. Please try again, or use the manual payment option."


class PesapalError(Exception):
    """Raised for any Pesapal API failure."""

    def __init__(self, detail, raw=None, technical=''):
        self.detail = str(detail or GENERIC_REJECTED)       # customer-safe
        self.raw = raw
        self.technical = str(technical or self.detail)       # diagnostics only
        super().__init__(self.detail)


# --------------------------------------------------------------- helpers ---

def _headers():
    return {"Accept": "application/json", "Content-Type": "application/json"}


def _safe_json(response):
    """Always returns a dict ({} when the body is empty / not JSON / not an object)."""
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _provider_message(data):
    """Best-effort diagnostic text from a Pesapal error body (never shown to customers)."""
    if not isinstance(data, dict):
        return ''
    err = data.get('error')
    if isinstance(err, dict):
        parts = [str(err.get(k)) for k in ('error_type', 'code', 'message') if err.get(k)]
        if parts:
            return ' / '.join(parts)
    elif isinstance(err, str) and err.strip():
        return err.strip()
    return str(data.get('message') or '').strip()


def _is_error(response, data):
    return response.status_code >= 400 or bool(data.get('error'))


def _raise_for_failure(response, data, *, what, customer_message=GENERIC_REJECTED):
    technical = f"{what}: HTTP {response.status_code} {_provider_message(data) or (response.text or '')[:200]}"
    logger.error("Pesapal %s", technical)
    raise PesapalError(customer_message, raw=data, technical=technical)


def _parse_expiry(value):
    """Pesapal returns ISO timestamps with 7 fractional digits ('...5177702Z'), which
    datetime.fromisoformat can't parse — trim to microseconds first."""
    if not value or not isinstance(value, str):
        return None
    text = re.sub(r'(\.\d{6})\d+', r'\1', value.strip()).replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def to_decimal(value):
    """Decimal from a provider number/str, or None if it isn't a usable amount."""
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


# ----------------------------------------------------------------- token ---

def get_token(pesapal_settings: PesapalSettings, force_refresh=False) -> str:
    """Return a valid bearer token, reusing the cached one when it is still fresh."""
    now = timezone.now()
    if (
        not force_refresh
        and pesapal_settings.cached_token
        and pesapal_settings.cached_token_expires_at
        and pesapal_settings.cached_token_expires_at > now
    ):
        return pesapal_settings.cached_token

    if not pesapal_settings.is_configured:
        raise PesapalError(
            "Online payment is not configured yet.",
            technical="Pesapal Consumer Key/Secret are missing in Django Admin.",
        )

    last_exc = None
    response = None
    for attempt in range(MAX_SAFE_ATTEMPTS):
        try:
            response = requests.post(
                f"{pesapal_settings.base_api_url}/api/Auth/RequestToken",
                headers=_headers(),
                json={
                    "consumer_key": pesapal_settings.consumer_key,
                    "consumer_secret": pesapal_settings.consumer_secret,
                },
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("Pesapal RequestToken network error (attempt %s): %s", attempt + 1, exc)
            continue
        if response.status_code < 500:
            break
        logger.warning("Pesapal RequestToken HTTP %s (attempt %s)", response.status_code, attempt + 1)
    else:
        if response is None:
            raise PesapalError(GENERIC_UNREACHABLE, technical=f"RequestToken network error: {last_exc}") from last_exc

    data = _safe_json(response)
    if _is_error(response, data):
        # Bad credentials is by far the most common cause — tell the OWNER where to look,
        # via technical text; customers only get the generic message.
        _raise_for_failure(
            response, data, what="RequestToken failed (check Consumer Key/Secret and Sandbox/Live setting)",
            customer_message="Online payment is temporarily unavailable. Please use the manual payment option.",
        )

    token = data.get("token")
    if not token or not isinstance(token, str):
        raise PesapalError(GENERIC_REJECTED, raw=data, technical="RequestToken returned no token.")

    lifetime_deadline = now + timedelta(seconds=DEFAULT_TOKEN_LIFETIME_SECONDS - TOKEN_SAFETY_MARGIN_SECONDS)
    provider_expiry = _parse_expiry(data.get("expiryDate"))
    if provider_expiry is not None:
        provider_deadline = provider_expiry - timedelta(seconds=TOKEN_SAFETY_MARGIN_SECONDS)
        # Trust the provider only when it is sane (in the future, not absurdly far away).
        if now < provider_deadline < now + timedelta(minutes=30):
            lifetime_deadline = provider_deadline

    pesapal_settings.cached_token = token
    pesapal_settings.cached_token_expires_at = lifetime_deadline
    pesapal_settings.save(update_fields=["cached_token", "cached_token_expires_at"])
    return token


def _authed_request(pesapal_settings, method, path, *, safe_to_retry, **kwargs):
    """
    Call a Pesapal endpoint with a bearer token.
      * 401  -> refresh the token once and replay.
      * Network error / 5xx -> replay once, but ONLY when safe_to_retry (idempotent calls).
    """
    url = f"{pesapal_settings.base_api_url}{path}"
    token = get_token(pesapal_settings)
    refreshed = False
    attempts = MAX_SAFE_ATTEMPTS if safe_to_retry else 1
    last_exc = None
    response = None

    attempt = 0
    while attempt < attempts:
        attempt += 1
        headers = {**_headers(), "Authorization": f"Bearer {token}"}
        try:
            response = requests.request(method, url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("Pesapal %s %s network error (attempt %s): %s", method, path, attempt, exc)
            response = None
            continue

        if response.status_code == 401 and not refreshed:
            refreshed = True
            token = get_token(pesapal_settings, force_refresh=True)
            attempt -= 1            # an auth refresh must not consume the retry budget
            continue
        if response.status_code >= 500 and attempt < attempts:
            logger.warning("Pesapal %s %s HTTP %s (attempt %s)", method, path, response.status_code, attempt)
            continue
        return response

    if response is None:
        raise PesapalError(GENERIC_UNREACHABLE, technical=f"{method} {path} network error: {last_exc}") from last_exc
    return response


# ------------------------------------------------------------------- IPN ---

def ensure_ipn_registered(pesapal_settings: PesapalSettings, ipn_url: str) -> str:
    """Register (or reuse) the IPN URL with Pesapal. Returns the ipn_id and saves it.

    The registered URL is remembered, so changing the site's public address
    (custom domain, new Railway domain) transparently re-registers instead of
    leaving Pesapal calling a dead URL forever.
    """
    if pesapal_settings.ipn_id and pesapal_settings.ipn_url == ipn_url:
        return pesapal_settings.ipn_id

    r = _authed_request(
        pesapal_settings, "POST", "/api/URLSetup/RegisterIPN", safe_to_retry=True,
        json={"url": ipn_url, "ipn_notification_type": "GET"},
    )
    data = _safe_json(r)
    if _is_error(r, data):
        _raise_for_failure(r, data, what="RegisterIPN failed",
                           customer_message="Online payment is temporarily unavailable. Please use the manual payment option.")
    ipn_id = data.get("ipn_id")
    if not ipn_id:
        raise PesapalError(GENERIC_REJECTED, raw=data, technical="RegisterIPN returned no ipn_id.")

    pesapal_settings.ipn_id = str(ipn_id)
    pesapal_settings.ipn_url = ipn_url
    pesapal_settings.save(update_fields=["ipn_id", "ipn_url"])
    return pesapal_settings.ipn_id


# ----------------------------------------------------------------- order ---

def _clean_phone(phone):
    """Normalise to international format (+255...) - Pesapal rejects odd local formats."""
    raw = re.sub(r'[^\d+]', '', phone or '')
    if not raw:
        return ''
    digits = raw.lstrip('+')
    if raw.startswith('+'):
        return ('+' + digits)[:20]
    if digits.startswith('255') and len(digits) >= 12:
        return ('+' + digits)[:20]
    if digits.startswith('0') and len(digits) == 10:       # 0712345678 -> +255712345678
        return ('+255' + digits[1:])[:20]
    return digits[:20]


def _clean_email(email):
    """Return the email only if it is well-formed; a bad one makes Pesapal reject the whole order."""
    email = (email or '').strip()
    if not email:
        return ''
    try:
        validate_email(email)
    except ValidationError:
        return ''
    return email


def _clean_text(value, limit):
    """Pesapal is picky with typographic characters: swap dashes/quotes for plain ASCII."""
    value = (value or '')
    for bad, good in (('\u2014', '-'), ('\u2013', '-'), ('\u2018', "'"), ('\u2019', "'"), ('\u201c', '"'), ('\u201d', '"')):
        value = value.replace(bad, good)
    return re.sub(r'\s+', ' ', value).strip()[:limit]


def submit_order(pesapal_settings, *, merchant_reference, amount, currency, description,
                 callback_url, ipn_id, email, phone_number, first_name='', last_name='',
                 country_code='TZ', redirect_mode='PARENT_WINDOW', cancellation_url=''):
    """Submit a checkout order. Returns dict with order_tracking_id and redirect_url."""
    try:
        amount_value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        raise PesapalError("This payment amount is not valid.", technical=f"Bad amount: {amount!r}")
    if amount_value <= 0:
        raise PesapalError(
            "This payment has no amount to charge. Please contact support.",
            technical=f"Refusing to submit non-positive amount {amount_value}.",
        )
    email = _clean_email(email)
    phone_number = _clean_phone(phone_number)
    if not email and not phone_number:
        # Pesapal rejects an order that has neither an email nor a phone number.
        raise PesapalError(
            "Add an email address or phone number to your profile so we can start the online payment, "
            "or use the manual payment option.",
            technical="Billing address has neither email nor phone number.",
        )

    payload = {
        "id": str(merchant_reference),
        "currency": currency,
        "amount": float(amount_value),
        "description": _clean_text(description, 100) or 'SmartPOS payment',
        "callback_url": callback_url,
        "redirect_mode": redirect_mode,
        "notification_id": ipn_id,
        "cancellation_url": cancellation_url or callback_url,
        "billing_address": {
            "email_address": email,
            "phone_number": phone_number,
            "country_code": country_code,
            "first_name": _clean_text(first_name, 50) or 'Customer',
            "last_name": _clean_text(last_name, 50),
        },
    }
    r = _authed_request(pesapal_settings, "POST", "/api/Transactions/SubmitOrderRequest",
                        safe_to_retry=False, json=payload)
    data = _safe_json(r)
    if _is_error(r, data):
        provider_text = _provider_message(data).lower()
        if 'amount_exceeds' in provider_text or 'exceeds limit' in provider_text:
            # Pesapal merchant account has a per-transaction cap lower than this amount.
            # Retrying can never succeed, so point the customer to the manual option instead.
            _raise_for_failure(
                r, data, what="SubmitOrderRequest failed (amount is above the Pesapal account's per-transaction limit - ask Pesapal to raise it)",
                customer_message="This amount is above the online payment limit right now. "
                                 "Please use the Mobile money tab to pay, or choose a smaller package.",
            )
        _raise_for_failure(r, data, what="SubmitOrderRequest failed",
                           customer_message="Pesapal could not create the payment order. Please try again.")
    order_tracking_id = data.get("order_tracking_id")
    redirect_url = data.get("redirect_url")
    if not order_tracking_id or not redirect_url:
        raise PesapalError("Pesapal did not return a payment link. Please try again.", raw=data,
                           technical="SubmitOrderRequest returned no order_tracking_id/redirect_url.")
    return data


def get_transaction_status(pesapal_settings, order_tracking_id):
    """Fetch the authoritative status of an order directly from Pesapal."""
    r = _authed_request(
        pesapal_settings, "GET", "/api/Transactions/GetTransactionStatus", safe_to_retry=True,
        params={"orderTrackingId": order_tracking_id},
    )
    data = _safe_json(r)
    if _is_error(r, data):
        _raise_for_failure(r, data, what="GetTransactionStatus failed",
                           customer_message="Could not confirm the payment status with Pesapal right now.")
    if not data:
        raise PesapalError("Could not confirm the payment status with Pesapal right now.",
                           technical="GetTransactionStatus returned an empty/non-JSON body.")
    return data


# ---------------------------------------------------------------- status ---

# Pesapal's numeric status_code -> our vocabulary.
STATUS_CODE_MAP = {0: "INVALID", 1: "COMPLETED", 2: "FAILED", 3: "REVERSED"}

# An order that has not been paid yet is reported by Pesapal with the "not
# completed" code 0. Treating that as a FINAL "invalid" the moment the customer
# lands on checkout would show a scary error while they are still typing their
# PIN, so code 0 is only final once the order is older than this.
INVALID_GRACE_MINUTES = 60


def interpret_status(status_payload, *, created_at=None) -> str:
    """Map a GetTransactionStatus payload to one of our PesapalTransaction.Status values."""
    from .models import PesapalTransaction  # local import to avoid a module cycle at import time

    if not isinstance(status_payload, dict):
        return PesapalTransaction.Status.SUBMITTED

    code = status_payload.get("status_code")
    try:
        code = int(code) if code is not None and code != '' else None
    except (TypeError, ValueError):
        code = None
    description = str(status_payload.get("payment_status_description") or "").strip().lower()

    if code in STATUS_CODE_MAP:
        mapped = STATUS_CODE_MAP[code]
    elif description in ("completed", "failed", "reversed", "invalid"):
        mapped = description.upper()
    else:
        mapped = "SUBMITTED"          # pending / unknown -> keep waiting

    if mapped == "INVALID" and created_at is not None:
        if timezone.now() - created_at < timedelta(minutes=INVALID_GRACE_MINUTES):
            mapped = "SUBMITTED"

    return {
        "COMPLETED": PesapalTransaction.Status.COMPLETED,
        "FAILED": PesapalTransaction.Status.FAILED,
        "REVERSED": PesapalTransaction.Status.REVERSED,
        "INVALID": PesapalTransaction.Status.INVALID,
        "SUBMITTED": PesapalTransaction.Status.SUBMITTED,
    }[mapped]
