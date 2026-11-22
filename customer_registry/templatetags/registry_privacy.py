from django import template

from payments.privacy import mask_phone

register = template.Library()


@register.filter
def phone_for(phone, user):
    """Full number for managers; masked ('+255 712 *** 345' style) for everyone else."""
    try:
        if user.is_authenticated and user.is_management_user():
            return phone
    except Exception:  # noqa: BLE001
        pass
    return mask_phone(phone)


@register.filter
def can_see_phone(user):
    try:
        return bool(user.is_authenticated and user.is_management_user())
    except Exception:  # noqa: BLE001
        return False
