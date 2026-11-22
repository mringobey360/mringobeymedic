import json
import logging
import uuid

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from hq.models import PaymentSettings, SignupRequest
from sms_integration.models import SMSDebtPackage, SMSReceiptPackage

from .client import AssistantUnavailable, ask_claude
from .knowledge import build_public_prompt, build_staff_prompt
from .offline import public_offline_answer, staff_offline_answer

logger = logging.getLogger(__name__)

MAX_HISTORY = 10          # messages kept per request (the browser keeps the full chat)
MAX_MESSAGE_CHARS = 1200  # per message


# ------------------------------------------------------------- helpers ----

def _json_error(message, status):
    return JsonResponse({'error': message}, status=status)


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def _hit(key, window_seconds):
    """Increment a counter in the cache (atomic where the backend supports it)."""
    try:
        if cache.add(key, 1, window_seconds):
            return 1
        count = cache.incr(key)
    except ValueError:  # key expired between add() and incr()
        cache.set(key, 1, window_seconds)
        return 1
    except Exception:  # cache backend down: never block customers because of it
        logger.warning('Assistant rate-limit cache unavailable', exc_info=True)
        return 0
    return count if isinstance(count, int) else 0


def _too_many(message='You are sending messages too quickly. Please wait a moment and try again.'):
    return _json_error(message, 429)


def _parse_messages(request):
    """
    Validate the chat history sent by the browser and return a clean list that
    the Messages API accepts: alternating user/assistant turns, starting and
    ending with a user turn. Returns (messages, context_dict) or raises ValueError.
    """
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        raise ValueError('Invalid request.')

    raw = data.get('messages')
    if not isinstance(raw, list) or not raw:
        raise ValueError('Please type a question.')

    cleaned = []
    for item in raw[-MAX_HISTORY:]:
        if not isinstance(item, dict):
            continue
        role = item.get('role')
        content = item.get('content')
        if role not in ('user', 'assistant') or not isinstance(content, str):
            continue
        content = content.strip()[:MAX_MESSAGE_CHARS]
        if not content:
            continue
        if cleaned and cleaned[-1]['role'] == role:
            cleaned[-1]['content'] += '\n' + content  # merge consecutive same-role turns
        else:
            cleaned.append({'role': role, 'content': content})

    while cleaned and cleaned[0]['role'] != 'user':
        cleaned.pop(0)
    if not cleaned or cleaned[-1]['role'] != 'user':
        raise ValueError('Please type a question.')

    context = data.get('context') if isinstance(data.get('context'), dict) else {}
    return cleaned, context


def _reply(system_prompt, messages):
    try:
        text = ask_claude(system_prompt, messages)
    except AssistantUnavailable as exc:
        return _json_error(str(exc), 503)
    except Exception:  # never let an unexpected bug become a 500 page inside the chat widget
        logger.exception('Unexpected assistant failure')
        return _json_error('Something went wrong. Please try again.', 500)
    return JsonResponse({'reply': text})


def _assistant_active():
    # Works for free without any key (offline FAQ engine below). An API key is
    # an optional upgrade to smarter, AI-generated answers — not a requirement.
    return bool(settings.ASSISTANT_ENABLED)


def _ai_configured():
    return bool(settings.ANTHROPIC_API_KEY)


# --------------------------------------------------------------- public ----

@require_POST
def public_chat_api(request):
    """Assistant for visitors who are not logged in (signup / payment / status)."""
    if not _assistant_active():
        return _json_error('The assistant is not available right now.', 503)

    # Cost protection: per-IP short window + per-IP daily + a global daily cap.
    ip = _client_ip(request)
    today = timezone.localdate().isoformat()
    if _hit(f'assistant:pub:ip10:{ip}', 600) > settings.ASSISTANT_PUBLIC_PER_10_MIN:
        return _too_many()
    if _hit(f'assistant:pub:ipday:{ip}:{today}', 86400) > settings.ASSISTANT_PUBLIC_PER_DAY:
        return _too_many('Daily message limit reached. Please contact support for further help.')
    if _hit(f'assistant:pub:global:{today}', 86400) > settings.ASSISTANT_PUBLIC_GLOBAL_PER_DAY:
        return _json_error('The assistant is very busy today. Please contact support.', 503)

    try:
        messages, context = _parse_messages(request)
    except ValueError as exc:
        return _json_error(str(exc), 400)

    # Optional: the visitor is on their own private signup link — give the bot
    # that one registration's status so it can answer "what's happening with mine?".
    signup = None
    raw_id = context.get('signup_id')
    if isinstance(raw_id, str) and raw_id:
        try:
            signup = (
                SignupRequest.objects.select_related('sms_package', 'receipt_sms_package')
                .filter(public_id=uuid.UUID(raw_id)).first()
            )
        except (ValueError, AttributeError):
            signup = None

    payment_settings = PaymentSettings.load()

    if not _ai_configured():
        # Free mode: local keyword-matched FAQ, no API call, no cost.
        last_user_text = messages[-1]['content']
        text = public_offline_answer(last_user_text, payment_settings=payment_settings, signup=signup)
        return JsonResponse({'reply': text})

    debt_packages = list(SMSDebtPackage.objects.filter(is_active=True))
    receipt_packages = list(SMSReceiptPackage.objects.filter(is_active=True))
    system_prompt = build_public_prompt(payment_settings, debt_packages, receipt_packages, signup)
    return _reply(system_prompt, messages)


# ---------------------------------------------------------------- staff ----

@require_POST
def staff_chat_api(request):
    """Assistant for logged-in shop owners and employees."""
    if not request.user.is_authenticated:
        return _json_error('Your session has expired. Please refresh the page and sign in again.', 401)
    if not _assistant_active():
        return _json_error('The assistant is not available right now.', 503)

    user_id = request.user.pk
    today = timezone.localdate().isoformat()
    if _hit(f'assistant:staff:hour:{user_id}', 3600) > settings.ASSISTANT_STAFF_PER_HOUR:
        return _too_many()
    if _hit(f'assistant:staff:day:{user_id}:{today}', 86400) > settings.ASSISTANT_STAFF_PER_DAY:
        return _too_many('Daily message limit reached. Please try again tomorrow.')

    try:
        messages, context = _parse_messages(request)
    except ValueError as exc:
        return _json_error(str(exc), 400)

    page_title = str(context.get('page_title', ''))[:80]
    page_path = str(context.get('page_path', ''))[:120]

    # /assistant/ is exempt from the subscription lock (so people with an expired
    # plan can still ask how to renew), which means the middleware did not set
    # request.subscription. Owners' own subscription is looked up directly.
    subscription = getattr(request, 'subscription', None)
    if subscription is None and request.user.is_admin_role():
        try:
            subscription = request.user.subscription
        except Exception:
            subscription = None

    payment_settings = PaymentSettings.load()

    if not _ai_configured():
        # Free mode: local keyword-matched FAQ, no API call, no cost.
        last_user_text = messages[-1]['content']
        text = staff_offline_answer(
            last_user_text, user=request.user,
            pharmacy=getattr(request, 'pharmacy', None),
            subscription=subscription, payment_settings=payment_settings,
        )
        return JsonResponse({'reply': text})

    system_prompt = build_staff_prompt(
        user=request.user,
        pharmacy=getattr(request, 'pharmacy', None),
        subscription=subscription,
        payment_settings=payment_settings,
        page_title=page_title,
        page_path=page_path,
    )
    return _reply(system_prompt, messages)
