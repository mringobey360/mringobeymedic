import re
from urllib.parse import quote

from django import template
from django.urls import reverse

register = template.Library()


@register.filter
def get_item(mapping, key):
    if not mapping:
        return 0
    try:
        return mapping.get(str(key), 0)
    except AttributeError:
        return 0


def _intl(phone):
    """Normalise a Tanzanian/international phone number to digits for wa.me and tel: links."""
    digits = re.sub(r'\D', '', str(phone or ''))
    if not digits:
        return ''
    if digits.startswith('00'):
        digits = digits[2:]
    if digits.startswith('0'):
        digits = '255' + digits[1:]
    elif len(digits) == 9:
        digits = '255' + digits
    return digits


@register.filter
def intl_phone(value):
    return _intl(value)


def _contact_phone(context, product):
    """Seller branch phone when available, otherwise the Marketplace support WhatsApp number."""
    store = getattr(product, 'seller_store', None)
    phone = ''
    if store is not None:
        phone = getattr(getattr(store, 'pharmacy', None), 'phone_number', '') or ''
    return phone or context.get('SUPPORT_WHATSAPP_PHONE', '') or ''


@register.simple_tag(takes_context=True)
def seller_phone(context, product):
    return _intl(_contact_phone(context, product))


@register.simple_tag(takes_context=True)
def whatsapp_url(context, product):
    number = _intl(_contact_phone(context, product))
    if not number:
        return ''
    request = context.get('request')
    path = reverse('marketplace:detail', args=[product.slug])
    link = request.build_absolute_uri(path) if request is not None else path
    text = "Hello, I'm interested in %s (TSh %s) on SmartPOS Marketplace.\n%s" % (product.name, format(product.price, ',.0f'), link)
    return 'https://wa.me/%s?text=%s' % (number, quote(text))


@register.filter
def mul(value, arg):
    """Multiply two numbers in a template (e.g. quantity × unit price) without losing decimals."""
    try:
        return value * arg
    except (TypeError, ValueError):
        try:
            return float(value) * float(arg)
        except (TypeError, ValueError):
            return 0



@register.simple_tag
def stars(avg, count=None, size=''):
    """Accessible star row. Fills partially for fractional averages (4.3 => 86%)."""
    from django.utils.html import format_html
    try:
        value = max(0.0, min(5.0, float(avg or 0)))
    except (TypeError, ValueError):
        value = 0.0
    return format_html('<span class="mk-stars {}" style="--p:{}%" role="img" aria-label="{} out of 5 stars">\u2605\u2605\u2605\u2605\u2605</span>',
                       size, round(value / 5 * 100, 1), f'{value:.1f}')
