"""
Minimal Anthropic Messages API client (standard library only, so no new
dependency is added to requirements.txt).

The API key is read from settings.ANTHROPIC_API_KEY, which comes from the
environment (.env locally / Railway variables in production). It is never
sent to the browser.
"""
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

API_URL = 'https://api.anthropic.com/v1/messages'
API_VERSION = '2023-06-01'


class AssistantUnavailable(Exception):
    """Raised when the AI service cannot answer. The message is safe to show users."""


def ask_claude(system_prompt, messages, max_tokens=None):
    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        raise AssistantUnavailable('The assistant is not configured yet.')

    payload = {
        'model': settings.ASSISTANT_MODEL,
        'max_tokens': max_tokens or settings.ASSISTANT_MAX_TOKENS,
        'system': system_prompt,
        'messages': messages,
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'content-type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': API_VERSION,
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(request, timeout=settings.ASSISTANT_TIMEOUT) as response:
            body = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        # Log details server-side only; never leak provider errors to users.
        detail = ''
        try:
            detail = exc.read().decode('utf-8', 'replace')[:500]
        except Exception:
            pass
        logger.error('Assistant API HTTP %s: %s', exc.code, detail)
        if exc.code == 429:
            raise AssistantUnavailable('The assistant is very busy right now. Please try again in a minute.')
        raise AssistantUnavailable('The assistant is temporarily unavailable. Please try again shortly.')
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.error('Assistant API connection error: %s', exc)
        raise AssistantUnavailable('The assistant could not be reached. Please try again shortly.')
    except ValueError as exc:
        logger.error('Assistant API returned invalid JSON: %s', exc)
        raise AssistantUnavailable('The assistant returned an unexpected response.')

    text = ''.join(
        block.get('text', '') for block in body.get('content', []) if block.get('type') == 'text'
    ).strip()
    if not text:
        raise AssistantUnavailable('The assistant did not return an answer. Please rephrase your question.')
    return text
