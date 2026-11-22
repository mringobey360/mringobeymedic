"""Professional Layaway SMS templates, credit accounting, event deduplication and delivery audit."""
from django.db import transaction
from django.utils import timezone
from .models import LayawaySMSWallet, LayawaySMSLog


def _t(value, limit=None):
    from sms_integration.utils import _clean_sms_text
    return _clean_sms_text(value, limit)


def _shop_line(pharmacy):
    import re
    name = re.sub(r'\s*\(\d+\)\s*$', '', pharmacy.name or 'SmartPOS')
    return _t(name, 32).upper() or 'SMARTPOS'


def _first_name(plan):
    return (_t((plan.customer_name or 'Mteja').split()[0], 24) or 'Mteja').capitalize()


def _footer(pharmacy):
    phone = _t(getattr(pharmacy, 'phone_number', ''), 20)
    return f'Msaada: {phone}' if phone else 'Asante kwa kuchagua duka letu.'


def _compose(*lines):
    from sms_integration.utils import tidy_sms
    return tidy_sms('\n'.join(str(line) for line in lines if line))


def build_created_sms(plan):
    due = f'Tarehe ya mwisho: {plan.due_date:%d/%m/%Y}' if plan.due_date else ''
    next_step = ('Malipo yamekamilika. Wasiliana na duka kuthibitisha bidhaa ipo kabla ya kuchukua.'
                 if plan.balance <= 0 else 'Hifadhi kumbukumbu hii kwa malipo yajayo.')
    return _compose(_shop_line(plan.pharmacy), f'Habari {_first_name(plan)}, Layaway {plan.reference} imesajiliwa.',
        f'Bidhaa: {_t(plan.product.name, 30)} x{plan.quantity}', f'Jumla TZS {plan.total_amount:,.0f} | Umelipa TZS {plan.amount_paid:,.0f}',
        f'Salio TZS {plan.balance:,.0f}.', due, next_step, _footer(plan.pharmacy))


def build_payment_sms(plan, amount):
    return _compose(_shop_line(plan.pharmacy), f'Tumepokea TZS {amount:,.0f}, Layaway {plan.reference}. Asante {_first_name(plan)}.',
        f'Jumla uliyolipa: TZS {plan.amount_paid:,.0f}. Salio: TZS {plan.balance:,.0f}.',
        f'Bidhaa: {_t(plan.product.name, 28)} x{plan.quantity}.', _footer(plan.pharmacy))


def build_paid_sms(plan, amount=None):
    """Confirms payment only; does not falsely claim the product is in stock/ready."""
    return _compose(_shop_line(plan.pharmacy), f'Habari {_first_name(plan)}, umekamilisha malipo ya Layaway {plan.reference}.',
        f'Jumla TZS {plan.total_amount:,.0f}; salio TZS 0.',
        'Tafadhali wasiliana na duka kuthibitisha upatikanaji wa bidhaa kabla ya kuichukua.', _footer(plan.pharmacy))


def build_handover_sms(plan, quantity, complete):
    remaining = max(0, int(plan.quantity) - int(plan.quantity_handed_over))
    if complete:
        return _compose(_shop_line(plan.pharmacy), f'Habari {_first_name(plan)}, tumekukabidhi bidhaa zote za Layaway {plan.reference}.',
            f'Bidhaa: {_t(plan.product.name, 28)}. Idadi iliyokabidhiwa: {plan.quantity}.',
            'Asante kwa kutuamini. Karibu tena!', _footer(plan.pharmacy))
    return _compose(_shop_line(plan.pharmacy), f'Habari {_first_name(plan)}, tumekukabidhi sehemu ya bidhaa za Layaway {plan.reference}.',
        f'Bidhaa: {_t(plan.product.name, 28)}. Zilizokabidhiwa jumla: {plan.quantity_handed_over}/{plan.quantity}.',
        f'Zilizobaki kukabidhiwa: {remaining}. Hakuna malipo mapya yaliyotozwa wakati wa kukabidhi.', _footer(plan.pharmacy))


def build_reminder_sms(plan, kind):
    if kind == 'REMINDER_3_DAYS':
        lead = f'Ukumbusho: malipo ya Layaway {plan.reference} yanatakiwa ndani ya siku 3.'
    elif kind == 'REMINDER_DUE':
        lead = f'Ukumbusho: leo ndiyo tarehe ya mwisho ya malipo ya Layaway {plan.reference}.'
    else:
        lead = f'Ukumbusho: Layaway {plan.reference} imepita tarehe ya mwisho ya malipo.'
    return _compose(_shop_line(plan.pharmacy), f'Habari {_first_name(plan)}, {lead}',
        f'Salio: TZS {plan.balance:,.0f}. Tafadhali wasiliana na duka ikiwa unahitaji msaada.', _footer(plan.pharmacy))


def send_layaway_sms(pharmacy, phone, message, owner=None):
    """Consume branch Layaway credits only when attempting a provider send; refund credits on rejection."""
    from sms_integration.utils import send_sms, sms_segment_count
    from sms_integration.models import SMSLog
    credits_needed = max(1, int(sms_segment_count(message)))
    with transaction.atomic():
        wallet, _ = LayawaySMSWallet.objects.get_or_create(pharmacy=pharmacy)
        wallet = LayawaySMSWallet.objects.select_for_update().get(pk=wallet.pk)
        if wallet.total_remaining < credits_needed:
            return False, 'NO_CREDITS'
        trial_take = min(wallet.trial_remaining, credits_needed)
        paid_take = credits_needed - trial_take
        wallet.trial_used += trial_take
        wallet.paid_used += paid_take
        wallet.save(update_fields=['trial_used', 'paid_used', 'updated_at'])
    accepted = send_sms(phone, message, category=SMSLog.Category.OTHER, owner=owner, consume_wallet=False)
    if not accepted:
        with transaction.atomic():
            wallet = LayawaySMSWallet.objects.select_for_update().get(pharmacy=pharmacy)
            wallet.trial_used = max(0, wallet.trial_used - trial_take)
            wallet.paid_used = max(0, wallet.paid_used - paid_take)
            wallet.save(update_fields=['trial_used', 'paid_used', 'updated_at'])
        return False, 'SEND_FAILED'
    return True, f'CREDITS_USED_{credits_needed}'


def send_plan_event(plan, event_type, message, *, owner=None, event_key=None, retry_failed=False):
    """Log each customer-facing event and prevent duplicate SMS on double-click/retries."""
    key = (event_key or f'{plan.pk}:{event_type}')[:100]
    with transaction.atomic():
        log, created = LayawaySMSLog.objects.get_or_create(
            event_key=key, defaults={'pharmacy': plan.pharmacy, 'plan': plan, 'event_type': event_type,
                'phone': plan.customer_phone, 'message': message, 'status': LayawaySMSLog.Status.PENDING})
        if not created and log.status == LayawaySMSLog.Status.SENT:
            return True, 'ALREADY_SENT'
        if not created and log.status == LayawaySMSLog.Status.PENDING and log.attempts and not retry_failed:
            return False, 'ALREADY_ATTEMPTED'
        if not created and log.status in (LayawaySMSLog.Status.FAILED, LayawaySMSLog.Status.NO_CREDITS) and not retry_failed:
            return False, log.status
        log.message = message
        log.phone = plan.customer_phone
        log.status = LayawaySMSLog.Status.PENDING
        log.attempts += 1
        log.save(update_fields=['message','phone','status','attempts','updated_at'])
    ok, state = send_layaway_sms(plan.pharmacy, plan.customer_phone, message, owner=owner)
    with transaction.atomic():
        log = LayawaySMSLog.objects.select_for_update().get(event_key=key)
        log.status = LayawaySMSLog.Status.SENT if ok else (LayawaySMSLog.Status.NO_CREDITS if state == 'NO_CREDITS' else LayawaySMSLog.Status.FAILED)
        log.provider_result = state[:255]
        if ok:
            log.sent_at = timezone.now()
        log.save(update_fields=['status','provider_result','sent_at','updated_at'])
    return ok, state
