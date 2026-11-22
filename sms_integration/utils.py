import logging
from django.db import transaction
from accounts.phone_utils import normalize_phone_number, is_sms_deliverable
from .models import SMSSettings, SMSLog, SMSWallet, CustomerSMSCredentials, SMSDebtSubscription, SMSReceiptSubscription, SMSMarketingSubscription

logger = logging.getLogger(__name__)

_CATEGORY_TO_TOGGLE = {
    SMSLog.Category.CREDENTIALS: 'send_employee_credentials',
    SMSLog.Category.OTP: 'send_otp',
    SMSLog.Category.DAILY_SUMMARY: 'send_daily_summary',
    SMSLog.Category.LOW_STOCK: 'send_low_stock_alerts',
    SMSLog.Category.EXPIRY: 'send_expiry_alerts',
    SMSLog.Category.DEBT: 'send_debt_reminders',
    SMSLog.Category.RECEIPT: 'send_receipt_sms',
    SMSLog.Category.PAYMENT_RECEIPT: 'send_payment_receipts',
    SMSLog.Category.CUSTOMER_MARKETING: 'send_customer_marketing',
    SMSLog.Category.ATTENDANCE: 'send_attendance_alerts',
}


def _reserve_credit(owner):
    if owner is None or owner.is_superuser:
        return True
    with transaction.atomic():
        wallet, _ = SMSWallet.objects.select_for_update().get_or_create(owner=owner, defaults={'credits_granted': 500})
        if wallet.credits_remaining <= 0:
            return False
        wallet.credits_used += 1
        wallet.save(update_fields=['credits_used', 'updated_at'])
    return True


def _release_credit(owner):
    if owner is None or owner.is_superuser:
        return
    with transaction.atomic():
        wallet = SMSWallet.objects.select_for_update().filter(owner=owner).first()
        if wallet and wallet.credits_used > 0:
            wallet.credits_used -= 1
            wallet.save(update_fields=['credits_used', 'updated_at'])



def _reserve_debt_sms_credit(owner):
    """Reserve one credit from the separate Premium Debt SMS subscription."""
    from django.db.models import F
    with transaction.atomic():
        # Credits never expire: no end_date test, only a remaining balance.
        sub = (SMSDebtSubscription.objects.select_for_update()
               .filter(owner=owner, status=SMSDebtSubscription.Status.ACTIVE, sms_used__lt=F('sms_credits'))
               .order_by('id').first())
        if not sub or sub.credits_remaining <= 0:
            return False
        sub.sms_used += 1
        sub.save(update_fields=['sms_used', 'updated_at'])
        return True

def _release_debt_sms_credit(owner):
    with transaction.atomic():
        sub = SMSDebtSubscription.objects.select_for_update().filter(owner=owner, status=SMSDebtSubscription.Status.ACTIVE, sms_used__gt=0).order_by('id').first()
        if sub and sub.sms_used > 0:
            sub.sms_used -= 1
            sub.save(update_fields=['sms_used','updated_at'])

def has_active_debt_sms(owner):
    from django.db.models import F
    return SMSDebtSubscription.objects.filter(owner=owner, status=SMSDebtSubscription.Status.ACTIVE, sms_used__lt=F('sms_credits')).exists()


def sms_character_count(message: str) -> int:
    return len(message)


def sms_segment_count(message: str) -> int:
    # GSM-7 basic + common extension characters. Transactional messages are
    # deliberately composed in ASCII, but this also makes the audit accurate
    # for other outgoing SMS categories.
    gsm7_basic = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
    gsm7_extended = set("^{}\\[~]|€")
    if all(ch in gsm7_basic or ch in gsm7_extended for ch in message):
        return 1 if len(message) <= 160 else (len(message) + 152) // 153
    return 1 if len(message) <= 70 else (len(message) + 66) // 67


def _reserve_marketing_sms_credits(owner, segments):
    """Atomically reserve the exact billable SMS segments required by a campaign recipient."""
    from django.utils import timezone
    if not owner or segments <= 0:
        return False
    from django.db.models import F
    with transaction.atomic():
        # Marketing credits never expire: no end_date test, only enough remaining credits.
        sub = (SMSMarketingSubscription.objects.select_for_update()
               .filter(owner=owner, status=SMSMarketingSubscription.Status.ACTIVE,
                       sms_credits__gte=F('sms_used') + segments)
               .order_by('id').first())
        if not sub or sub.credits_remaining < segments:
            return False
        sub.sms_used += segments
        sub.save(update_fields=['sms_used', 'updated_at'])
        return True


def release_marketing_sms_credits(owner, segments):
    """Return previously reserved marketing credits after a provider failure."""
    if not owner or segments <= 0:
        return
    with transaction.atomic():
        sub = (SMSMarketingSubscription.objects.select_for_update()
               .filter(owner=owner, status=SMSMarketingSubscription.Status.ACTIVE, sms_used__gt=0)
               .order_by('id').first())
        if sub:
            sub.sms_used = max(sub.sms_used - segments, 0)
            sub.save(update_fields=['sms_used', 'updated_at'])


def _reserve_marketing_sms_credit(owner):
    # Backward-compatible helper for any older caller: one SMS segment.
    return _reserve_marketing_sms_credits(owner, 1)


def _release_marketing_sms_credit(owner):
    release_marketing_sms_credits(owner, 1)


def has_active_marketing_sms(owner):
    from django.utils import timezone
    from django.db.models import F
    return SMSMarketingSubscription.objects.filter(
        owner=owner, status=SMSMarketingSubscription.Status.ACTIVE, sms_used__lt=F('sms_credits')
    ).exists()


def send_customer_marketing_sms(phone_number, message, owner=None, segments=None, credits_already_reserved=False):
    """Send a paid marketing SMS. Credits are normally reserved by the campaign before queueing."""
    billable_segments = segments or sms_segment_count(message)
    # Direct/legacy calls still reserve their own exact segment count.
    if owner is None:
        return False
    if not credits_already_reserved and not _reserve_marketing_sms_credits(owner, billable_segments):
        return False
    sent = send_sms(phone_number, message, SMSLog.Category.CUSTOMER_MARKETING, owner=owner, consume_wallet=False)
    if not sent and not credits_already_reserved:
        release_marketing_sms_credits(owner, billable_segments)
    return sent



SECURITY_NOTICE = "ONYO: SmartPOS haitakupigia kuomba PIN, password wala OTP. Usimpe mtu yeyote. Ukipigiwa, kata simu."


def tidy_sms(message):
    """Remove unnecessary gaps: no blank lines, no double spaces, no trailing spaces."""
    lines = []
    for raw in str(message or '').replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        line = ' '.join(raw.split())
        if line:
            lines.append(line)
    return '\n'.join(lines)


def with_security_notice(message):
    """Append the anti-fraud warning (once) to payment confirmation SMS sent to customers."""
    message = tidy_sms(message)
    if 'ONYO:' in message:
        return message
    return f"{message}\n{SECURITY_NOTICE}"


def send_sms(phone_number: str, message: str, category: str = SMSLog.Category.OTHER, owner=None, consume_wallet=True) -> bool:
    """Send an SMS through Africa's Talking with a complete audit trail.

    Owner-specific credentials are used when enabled; otherwise the platform
    credentials from SMSSettings are used. Standard SMS calls can consume the
    owner's standard wallet. Premium debt/receipt/marketing flows pass
    ``consume_wallet=False`` because they reserve their own dedicated credits.
    """
    message = tidy_sms(message)
    if category == SMSLog.Category.PAYMENT_RECEIPT:
        message = with_security_notice(message)
    character_count = sms_character_count(message)
    segments = sms_segment_count(message)

    try:
        phone_number = normalize_phone_number(phone_number)
    except Exception as exc:
        SMSLog.objects.create(
            category=category,
            recipient=str(phone_number),
            message=message,
            was_sent=False,
            error_message=str(exc),
            owner=owner,
            character_count=character_count,
            sms_segments=segments,
        )
        return False

    # Reject numbers Africa's Talking would refuse (InvalidPhoneNumber) before any credit is spent
    # or any API call is made. The bad number is kept in the SMS log so it can be corrected.
    if not is_sms_deliverable(phone_number):
        logger.warning('Skipped SMS: invalid phone number %s', phone_number)
        SMSLog.objects.create(
            category=category,
            recipient=phone_number,
            message=message,
            was_sent=False,
            error_message='Invalid phone number. Use a mobile number like 0712345678 or +255712345678.',
            owner=owner,
            character_count=character_count,
            sms_segments=segments,
            delivery_status='FAILED',
        )
        return False

    settings_obj = SMSSettings.load()
    toggle = _CATEGORY_TO_TOGGLE.get(category)

    if not settings_obj.is_enabled:
        SMSLog.objects.create(
            category=category,
            recipient=phone_number,
            message=message,
            was_sent=False,
            error_message='SMS is disabled globally in SMS Settings.',
            owner=owner,
            character_count=character_count,
            sms_segments=segments,
        )
        return False

    if toggle and not getattr(settings_obj, toggle, True):
        SMSLog.objects.create(
            category=category,
            recipient=phone_number,
            message=message,
            was_sent=False,
            error_message=f"SMS category '{category}' is turned off in SMS Settings.",
            owner=owner,
            character_count=character_count,
            sms_segments=segments,
        )
        return False

    credit_reserved = False
    if consume_wallet and owner is not None:
        if not _reserve_credit(owner):
            SMSLog.objects.create(
                category=category,
                recipient=phone_number,
                message=message,
                was_sent=False,
                error_message='Standard SMS allowance exhausted. Your included SMS balance has reached 0.',
                owner=owner,
                character_count=character_count,
                sms_segments=segments,
            )
            return False
        credit_reserved = True

    credentials = None
    if owner is not None:
        try:
            custom = owner.custom_sms_credentials
            if custom.access_enabled and custom.has_complete_credentials():
                credentials = (custom.at_username, custom.at_api_key, custom.sender_id)
        except CustomerSMSCredentials.DoesNotExist:
            pass

    username = credentials[0] if credentials else settings_obj.at_username
    api_key = credentials[1] if credentials else settings_obj.at_api_key
    sender_id = credentials[2] if credentials else settings_obj.sender_id

    if not username or not api_key:
        if credit_reserved:
            _release_credit(owner)
        SMSLog.objects.create(
            category=category,
            recipient=phone_number,
            message=message,
            was_sent=False,
            error_message="Africa's Talking username/API key is not configured.",
            owner=owner,
            character_count=character_count,
            sms_segments=segments,
        )
        return False

    try:
        import africastalking

        africastalking.initialize(username, api_key)

        def _at_send(use_sender):
            kwargs = {'sender_id': sender_id} if (use_sender and sender_id) else {}
            resp = africastalking.SMS.send(message, [phone_number], **kwargs)
            data = (resp.get('SMSMessageData') or {}) if isinstance(resp, dict) else {}
            return resp, (data.get('Recipients') or []), str(data.get('Message') or '').strip()

        # Africa's Talking has two layers of SMS status. The immediate API
        # response tells us whether AT accepted the message; actual handset
        # delivery is asynchronous and comes later through a delivery report.
        # Accept 100/101/102 as provider-accepted states and persist the
        # provider identifiers so delivery can be traced in production.
        response, recipients, at_message = _at_send(True)

        # AT sometimes answers with an EMPTY Recipients list (sender ID not allowed on that
        # network, e.g. some Halotel 061x/062x numbers, or a transient gateway hiccup).
        # Retry once the same way, then once WITHOUT the custom sender ID (AT default route).
        if not recipients:
            logger.warning('AT empty recipients for %s (message=%r) - retrying', phone_number, at_message)
            import time
            time.sleep(1)
            response, recipients, at_message = _at_send(True)
        if not recipients and sender_id:
            logger.warning('AT empty recipients again for %s - retrying without sender_id', phone_number)
            response, recipients, at_message = _at_send(False)

        if not recipients:
            raise RuntimeError(
                "Africa's Talking returned no recipient status"
                + (f": {at_message}" if at_message else '.')
                + f" (raw={response!r})"
            )

        recipient = recipients[0] or {}
        raw_code = recipient.get('statusCode')
        try:
            status_code = int(raw_code) if raw_code is not None else None
        except (TypeError, ValueError):
            status_code = None
        provider_status = str(recipient.get('status') or '').strip()
        message_id = str(recipient.get('messageId') or '').strip()
        accepted_codes = {100, 101, 102}
        accepted_statuses = {'success', 'processed', 'sent', 'queued', 'submitted', 'buffered'}

        accepted = (status_code in accepted_codes) or (
            status_code is None and provider_status.lower() in accepted_statuses
        )
        if not accepted:
            detail = provider_status or str(raw_code or 'unknown')
            raise RuntimeError(
                f"Africa's Talking rejected SMS ({detail}). "
                f"statusCode={raw_code or 'n/a'}"
            )

        SMSLog.objects.create(
            category=category,
            recipient=phone_number,
            message=message,
            was_sent=True,
            owner=owner,
            character_count=character_count,
            sms_segments=segments,
            provider_status=provider_status or str(raw_code or ''),
            provider_message_id=message_id,
            delivery_status='ACCEPTED',
        )
        return True
    except Exception as exc:
        if credit_reserved:
            _release_credit(owner)
        logger.exception('Failed to send SMS to %s', phone_number)
        SMSLog.objects.create(
            category=category,
            recipient=phone_number,
            message=message,
            was_sent=False,
            error_message=str(exc),
            owner=owner,
            character_count=character_count,
            sms_segments=segments,
            delivery_status='FAILED',
            delivery_error=str(exc),
        )
        return False


def send_employee_credentials_sms(phone_number, username, temp_password, owner=None):
    message = (
        "SmartPOS - Your account is ready.\n"
        f"Username: {username}\n"
        f"Password: {temp_password}\n"
        "Log in and change your password right away.\n"
        "Never share these details."
    )
    return send_sms(phone_number, message, SMSLog.Category.CREDENTIALS, owner=owner)


def send_otp_sms(phone_number, code, owner=None):
    message = f"SmartPOS - Your password reset code is {code}.\nValid for 5 minutes. Never share it with anyone."
    return send_sms(phone_number, message, SMSLog.Category.OTP, owner=owner)


def send_daily_summary_sms(phone_number, sales_total, profit_total, low_stock_count, owner=None):
    message = (
        "SmartPOS - Daily Summary\n"
        f"Sales: TZS {sales_total:,.0f}\n"
        f"Profit: TZS {profit_total:,.0f}\n"
        f"Low stock: {low_stock_count} item(s)"
    )
    return send_sms(phone_number, message, SMSLog.Category.DAILY_SUMMARY, owner=owner)


def send_low_stock_sms(phone_number, product_name, quantity, owner=None):
    message = f"SmartPOS - Low stock\n{product_name}: {quantity} unit(s) left.\nPlease reorder soon."
    return send_sms(phone_number, message, SMSLog.Category.LOW_STOCK, owner=owner)


def send_expiry_sms(phone_number, product_name, days_left, owner=None):
    message = f"SmartPOS - Expiry alert\n{product_name} expires in {days_left} day(s).\nPlease review this stock."
    return send_sms(phone_number, message, SMSLog.Category.EXPIRY, owner=owner)


def _reserve_receipt_sms_credit(owner):
    """Reserve one credit from the separate Premium Receipt SMS subscription."""
    from django.db.models import F
    with transaction.atomic():
        # Credits never expire: no end_date test, only a remaining balance.
        sub = (SMSReceiptSubscription.objects.select_for_update()
               .filter(owner=owner, status=SMSReceiptSubscription.Status.ACTIVE, sms_used__lt=F('sms_credits'))
               .order_by('id').first())
        if not sub or sub.credits_remaining <= 0:
            return False
        sub.sms_used += 1
        sub.save(update_fields=['sms_used', 'updated_at'])
        return True

def _release_receipt_sms_credit(owner):
    with transaction.atomic():
        sub = (SMSReceiptSubscription.objects.select_for_update()
               .filter(owner=owner, status=SMSReceiptSubscription.Status.ACTIVE, sms_used__gt=0)
               .order_by('id').first())
        if sub and sub.sms_used > 0:
            sub.sms_used -= 1
            sub.save(update_fields=['sms_used', 'updated_at'])

def has_active_receipt_sms(owner):
    from django.utils import timezone
    from django.db.models import F
    return SMSReceiptSubscription.objects.filter(
        owner=owner, status=SMSReceiptSubscription.Status.ACTIVE, sms_used__lt=F('sms_credits')
    ).exists()

def _clean_sms_text(value, limit=None):
    import unicodedata
    text = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode('ascii')
    text = ' '.join(text.split())
    return text[:limit] if limit else text


def build_receipt_sms_text(customer_name, receipt_url, pharmacy=None, sale=None):
    """Professional, well-structured customer receipt SMS (plain GSM text, 2 segments max)."""
    from django.utils import timezone
    shop = _clean_sms_text(pharmacy.name if pharmacy else 'SmartPOS', 32).upper()
    first_name = _clean_sms_text((customer_name or 'Customer').strip().split()[0], 30) or 'Customer'
    phone = _clean_sms_text(getattr(pharmacy, 'phone_number', ''), 20)
    lines = [shop, f"Habari {first_name}, asante kwa ununuzi wako."]
    if sale is not None:
        number = f"PM-{sale.pharmacy_id}-{(sale.sale_number or sale.id):08d}"
        lines.append(f"Risiti {number}: TZS {sale.total_amount:,.0f}")
    lines.append(f"Pakua: {receipt_url}")
    if phone:
        lines.append(f"Msaada: {phone}")
    return tidy_sms('\n'.join(lines))


def send_receipt_sms(phone_number, customer_name, receipt_url, owner=None, pharmacy=None, sale=None):
    """Send a personalized secure receipt SMS using the dedicated Receipt SMS balance."""
    if owner is None or not _reserve_receipt_sms_credit(owner):
        return False

    message = build_receipt_sms_text(customer_name, receipt_url, pharmacy=pharmacy, sale=sale)

    sent = send_sms(phone_number, message, SMSLog.Category.RECEIPT, owner=owner, consume_wallet=False)
    if not sent:
        _release_receipt_sms_credit(owner)
    return sent


def send_debt_reminder_sms(phone_number, customer_name, amount, owner=None, pharmacy=None):
    pharmacy_name = _clean_sms_text(pharmacy.name if pharmacy else 'SmartPOS', 32).upper()
    first_name = _clean_sms_text((customer_name or 'Mteja').strip().split()[0] if (customer_name or '').strip() else 'Mteja', 30) or 'Mteja'
    phone = _clean_sms_text(getattr(pharmacy, 'phone_number', ''), 20)
    message = tidy_sms(
        f"{pharmacy_name}\n"
        f"Habari {first_name}, una deni la TZS {amount:,.0f}. Tafadhali lipa mapema.\n"
        + (f"Msaada: {phone}" if phone else "")
    )
    if owner is None or not _reserve_debt_sms_credit(owner):
        return False
    # Debt SMS uses the customer's own custom credentials when enabled, or
    # the platform default credentials when custom credentials are unavailable.
    # It does NOT consume the customer's separate standard SMS wallet.
    sent = send_sms(phone_number, message, SMSLog.Category.DEBT, owner=owner, consume_wallet=False)
    if not sent:
        _release_debt_sms_credit(owner)
    return sent
