from django import template

register = template.Library()


@register.simple_tag
def elided_page_range(paginator, current_page, on_each_side=1, on_ends=1):
    """
    Returns a page-number sequence with ellipsis markers for large paginations,
    e.g. [1, '…', 4, 5, 6, '…', 42] instead of listing every single page.
    """
    return list(paginator.get_elided_page_range(current_page, on_each_side=on_each_side, on_ends=on_ends))


@register.simple_tag(takes_context=True)
def querystring_replace(context, **kwargs):
    """
    Returns the current request's querystring with the given key(s) replaced/added —
    used so pagination links preserve active search/filter parameters.
    """
    request = context['request']
    query = request.GET.copy()
    for key, value in kwargs.items():
        query[key] = value
    return query.urlencode()


@register.simple_tag(takes_context=True)
def page_url(context, number, param='page', anchor=''):
    """
    Returns "?<current querystring with <param>=<number>>" so every filter/search in the
    URL is preserved. `param` lets a page hold two independent paginated tables
    (e.g. ?page=2&reorder_page=3).
    """
    request = context['request']
    query = request.GET.copy()
    query.pop('partial', None)  # internal live-search flag, never part of a shareable link
    query[param] = number
    return '?' + query.urlencode() + (('#' + anchor) if anchor else '')
