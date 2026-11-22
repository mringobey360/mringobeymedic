"""Shared display-only numeric formatting; never changes stored amounts."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django import template

register = template.Library()

@register.filter(name="smart_number")
def smart_number(value):
    if value is None or value == "":
        return "—"
    try:
        amount = Decimal(str(value).replace(",", ""))
        if not amount.is_finite():
            return "—"
        rounded = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return format(rounded, ",.0f")
    except (InvalidOperation, TypeError, ValueError):
        return "—"
