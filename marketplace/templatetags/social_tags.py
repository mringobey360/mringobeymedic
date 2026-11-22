from django import template

register = template.Library()


@register.inclusion_tag('marketplace/_social_links.html')
def social_links(store, compact=False, caption=''):
    """Render a seller's social icons (Instagram / TikTok / Facebook). Renders nothing when none are set."""
    links = store.social_links if store else []
    return {'store': store, 'links': links, 'compact': compact, 'caption': caption,
            'any_verified': any(l['verified'] for l in links)}
