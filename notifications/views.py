import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse, resolve, Resolver404
from django.utils.timesince import timesince
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import User
from .forms import AnnouncementForm
from .models import Notification
from sms_integration.announcements import ADMINISTRATOR_FOOTER
from .links import clean_action_url, is_external, MAX_LABEL, LABEL_DEFAULT
from .routing import first_accessible
from .utils import notify, notify_many

BELL_FEED_LIMIT = 12

# Login announcement pop-up (V54.70)
ANNOUNCEMENT_POPUP_SESSION_KEY = 'announcement_popup_seen'
ANNOUNCEMENT_POPUP_LIMIT = 5
ANNOUNCEMENT_POPUP_MAX_AGE_DAYS = 30


def _notification_scope(request):
    """Return the visibility scope for the currently active branch.

    Managers see notifications for their active branch plus true global
    notifications. Employees see only their assigned branch plus true global
    notifications. Platform superusers see everything.
    """
    if request.user.is_superuser:
        return Q()

    pharmacy = getattr(request, 'pharmacy', None)
    # These categories describe operational events that belong to a specific
    # branch. A legacy/misconfigured producer that omitted pharmacy must never
    # make one branch's stock/sales/debt/chat alert appear in every branch.
    branch_bound_categories = [
        Notification.Category.STOCK_ALERT,
        Notification.Category.SALES,
        Notification.Category.DEBT,
        Notification.Category.CHAT,
    ]
    # Managers see the selected branch plus genuine global notices. Branch
    # switching changes the feed immediately; operational alerts with a NULL
    # branch are excluded because their ownership cannot be established safely.
    global_notices = Q(pharmacy__isnull=True) & ~Q(category__in=branch_bound_categories)
    if pharmacy is None:
        return global_notices
    return Q(pharmacy=pharmacy) | global_notices


def _safe_notification_link(n, user=None):
    """Keep notification clicks inside the app, on a page the recipient can open.

    * Stale/deleted routes fall back to a sensible landing page for the category.
    * A destination gated to another role (for example an employee holding a
      manager-only review link) falls back to that person's own workspace
      instead of the "Access restricted" screen.
    * Older meal-debt notices saved with the generic /sales/debts/ link are routed
      to the payroll meal-cost overview.
    """
    user = user or getattr(n, 'recipient', None)
    title = (getattr(n, 'title', '') or '').strip().lower()
    message = (getattr(n, 'message', '') or '').strip().lower()
    is_meal_debt = 'meal debt' in title or ('meal' in title and 'debt' in message)
    raw = (n.link_url or '').strip()
    candidates = []
    if is_meal_debt and raw.rstrip('/') == '/sales/debts':
        candidates.append('/dashboard/reports/payroll/')
    if raw:
        candidates.append(raw)
    # Detail links should be stored by the producer; these are the landing pages
    # by type when a legacy route has been removed or is not allowed for the role.
    fallbacks = {
        Notification.Category.STOCK_ALERT: '/inventory/',
        Notification.Category.SALES: '/sales/history/',
        Notification.Category.DEBT: '/sales/debts/',
        Notification.Category.CHAT: '/chat/',
        Notification.Category.ANNOUNCEMENT: '/notifications/',
        Notification.Category.SYSTEM: '/',
        Notification.Category.OTHER: '/notifications/',
    }
    candidates.append(fallbacks.get(n.category, '/notifications/'))
    candidates.append('/notifications/')
    return first_accessible(user, candidates)


def _scoped_notifications(request):
    return Notification.objects.filter(
        Q(recipient=request.user) & _notification_scope(request)
    ).select_related('pharmacy')


def _action_payload(n):
    """Re-validate the stored link at read time (defence in depth)."""
    url = clean_action_url(getattr(n, 'action_url', ''))
    return {
        'action_url': url,
        'action_label': ((getattr(n, 'action_label', '') or '').strip() or LABEL_DEFAULT) if url else '',
        'action_external': is_external(url),
    }


def _serialize(n, user=None):
    return {
        **_action_payload(n),
        'id': n.id,
        'title': n.title,
        'message': n.message,
        'category': n.category,
        'category_display': n.get_category_display(),
        'level': n.level,
        'link_url': _safe_notification_link(n, user),
        'open_url': reverse('notifications:open', args=[n.pk]),
        'is_read': n.is_read,
        'created_at_display': f"{timesince(n.created_at)} ago",
        'created_at_iso': n.created_at.isoformat(),
        'pharmacy_id': n.pharmacy_id,
        'pharmacy_name': n.pharmacy.name if n.pharmacy_id else None,
    }


@login_required
def bell_feed_api(request):
    """Branch-aware live notification feed."""
    qs = _scoped_notifications(request)
    unread_count = qs.filter(is_read=False).count()
    recent = list(qs.order_by('-created_at')[:BELL_FEED_LIMIT])
    return JsonResponse({
        'unread_count': unread_count,
        'notifications': [_serialize(n, request.user) for n in recent],
        'latest_id': recent[0].id if recent else 0,
        'active_pharmacy_id': getattr(getattr(request, 'pharmacy', None), 'pk', None),
    })


@login_required
@require_http_methods(["GET"])
def notification_open_view(request, pk):
    """Open one notification on the right branch and the right page.

    Used by push messages and toasts. It marks the notice read, switches a
    manager/assistant to the branch the event belongs to (only when that branch
    is within their access), then redirects to a destination their role can
    open. It never grants access: the destination view still enforces its own
    permissions.
    """
    notification = get_object_or_404(
        Notification.objects.select_related('pharmacy'), pk=pk, recipient=request.user,
    )
    notification.mark_read()
    user = request.user
    branch_id = notification.pharmacy_id
    active = getattr(request, 'pharmacy', None)
    if (branch_id and not user.is_superuser and user.is_management_user()
            and not getattr(request, 'impersonating', False)
            and getattr(active, 'pk', None) != branch_id):
        if user.accessible_management_pharmacies().filter(pk=branch_id, is_active=True).exists():
            request.session['active_pharmacy_id'] = branch_id
    return redirect(_safe_notification_link(notification, user))


@login_required
@require_http_methods(["GET"])
def announcement_popup_api(request):
    """Unread announcements to show in the centred pop-up after sign-in.

    Branch scope is enforced by ``_scoped_notifications`` (same rule as the bell).
    Anything the user already closed in this login session is skipped, so the
    pop-up appears once per sign-in and never nags on every page.
    """
    seen = list(request.session.get(ANNOUNCEMENT_POPUP_SESSION_KEY, []))
    cutoff = timezone.now() - timedelta(days=ANNOUNCEMENT_POPUP_MAX_AGE_DAYS)
    qs = (_scoped_notifications(request)
          .filter(category=Notification.Category.ANNOUNCEMENT, is_read=False, created_at__gte=cutoff)
          .exclude(pk__in=seen)
          .order_by('-created_at')[:ANNOUNCEMENT_POPUP_LIMIT])
    items = [{
        'id': n.id,
        'title': n.title,
        'message': n.message,
        'level': n.level,
        'created_at_display': f"{timesince(n.created_at)} ago",
        'created_at_iso': n.created_at.isoformat(),
        **_action_payload(n),
    } for n in qs]
    return JsonResponse({'announcements': items, 'list_url': reverse('notifications:list')})


@login_required
@require_http_methods(["POST"])
def announcement_popup_dismiss_api(request):
    """Close the pop-up. ``mark_read`` true = "Got it" (read); false = "Close" (stay unread)."""
    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'ok': False}, status=400)
    raw_ids = payload.get('ids') or []
    ids = []
    for value in raw_ids[:20] if isinstance(raw_ids, list) else []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    scoped = _scoped_notifications(request).filter(
        pk__in=ids, category=Notification.Category.ANNOUNCEMENT)
    valid = list(scoped.values_list('pk', flat=True))
    seen = list(request.session.get(ANNOUNCEMENT_POPUP_SESSION_KEY, []))
    request.session[ANNOUNCEMENT_POPUP_SESSION_KEY] = (seen + [i for i in valid if i not in seen])[-100:]
    if payload.get('mark_read'):
        scoped.filter(is_read=False).update(is_read=True, read_at=timezone.now())
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(["POST"])
def mark_read_api(request, pk):
    # Important: scope is checked before touching the record. A notification
    # from Branch A cannot be marked/opened from Branch B.
    notification = get_object_or_404(_scoped_notifications(request), pk=pk)
    notification.mark_read()
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(["POST"])
def mark_all_read_api(request):
    _scoped_notifications(request).filter(is_read=False).update(
        is_read=True, read_at=timezone.now()
    )
    return JsonResponse({'ok': True})


@login_required
def notification_list_view(request):
    """Durable notification history for the active branch."""
    qs = _scoped_notifications(request).order_by('-created_at')

    category = request.GET.get('category', '')
    status = request.GET.get('status', '')
    if category:
        qs = qs.filter(category=category)
    if status == 'unread':
        qs = qs.filter(is_read=False)
    elif status == 'read':
        qs = qs.filter(is_read=True)

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get('page'))

    # Opening the history page marks only the currently visible branch/global
    # records as read. Another branch's unread count is never changed.
    for _n in page.object_list:
        _n.safe_action = _action_payload(_n)
    visible_ids = list(page.object_list.values_list('id', flat=True))
    if visible_ids:
        _scoped_notifications(request).filter(
            id__in=visible_ids, is_read=False
        ).update(is_read=True, read_at=timezone.now())

    return render(request, 'notifications/list.html', {
        'page_obj': page,
        'category_choices': Notification.Category.choices,
        'current_category': category,
        'current_status': status,
        'unread_count': _scoped_notifications(request).filter(is_read=False).count(),
        'active_pharmacy': getattr(request, 'pharmacy', None),
    })


@login_required
def announcement_create_view(request):
    """Create branch-only or all-branches employee announcements safely."""
    if not (request.user.is_superuser or request.user.is_management_user()):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied(
            'Announcements are available to the system owner or business manager.'
        )

    form = AnnouncementForm(
        request.POST or None,
        is_platform_owner=request.user.is_superuser,
    )
    if request.method == 'POST' and form.is_valid():
        scope = form.cleaned_data['scope']
        action_kwargs = {
            'action_url': form.cleaned_data.get('action_url', ''),
            'action_label': (form.cleaned_data.get('action_label') or '').strip()[:MAX_LABEL] if form.cleaned_data.get('action_url') else '',
        }

        if request.user.is_superuser and scope == 'CUSTOMER_ALL':
            recipients = User.objects.filter(
                role=User.Role.ADMIN, is_active=True, is_superuser=False
            )
            notify_many(
                recipients,
                title=form.cleaned_data['title'],
                message=f"{form.cleaned_data['message'].strip()}\n{ADMINISTRATOR_FOOTER}",
                category=Notification.Category.ANNOUNCEMENT,
                level=Notification.Level.INFO,
                link_url=reverse('notifications:list'),
                pharmacy=None,
                **action_kwargs,
            )
        elif request.user.is_superuser and scope == 'CUSTOMER_SELECTED':
            recipients = User.objects.filter(
                pk__in=form.cleaned_data['customer_ids'],
                role=User.Role.ADMIN,
                is_active=True,
                is_superuser=False,
            )
            notify_many(
                recipients,
                title=form.cleaned_data['title'],
                message=f"{form.cleaned_data['message'].strip()}\n{ADMINISTRATOR_FOOTER}",
                category=Notification.Category.ANNOUNCEMENT,
                level=Notification.Level.INFO,
                link_url=reverse('notifications:list'),
                pharmacy=None,
                **action_kwargs,
            )
        elif scope == 'ALL_BRANCHES':
            # CRITICAL: do not store an all-branches employee announcement as
            # pharmacy=NULL. NULL means GLOBAL and would leak into unrelated
            # branch feeds. Copy it once per branch, preserving branch scope.
            branches = list(request.user.accessible_management_pharmacies().filter(is_active=True))
            total = 0
            for branch in branches:
                recipients = User.objects.filter(
                    role=User.Role.EMPLOYEE,
                    is_active=True,
                    pharmacy=branch,
                )
                sent = notify_many(
                    recipients,
                    title=form.cleaned_data['title'],
                    message=form.cleaned_data['message'],
                    category=Notification.Category.ANNOUNCEMENT,
                    level=Notification.Level.INFO,
                    link_url=reverse('notifications:list'),
                    pharmacy=branch,
                    **action_kwargs,
                )
                total += len(sent)
        else:
            branch = None if request.user.is_superuser else getattr(request, 'pharmacy', None)
            recipients = User.objects.filter(
                pharmacy=branch,
                role=User.Role.EMPLOYEE,
                is_active=True,
            )
            sent = notify_many(
                recipients,
                title=form.cleaned_data['title'],
                message=form.cleaned_data['message'],
                category=Notification.Category.ANNOUNCEMENT,
                level=Notification.Level.INFO,
                link_url=reverse('notifications:list'),
                pharmacy=branch,
                **action_kwargs,
            )
            total = len(sent)

        messages.success(request, f"Announcement sent to {total}.")
        return redirect('notifications:announcement_create')

    return render(request, 'notifications/announcement_form.html', {
        'form': form,
        'is_platform_owner': request.user.is_superuser,
    })


@login_required
@require_http_methods(["GET"])
def web_push_config_api(request):
    from .push import push_is_configured, _public_key
    return JsonResponse({"configured": push_is_configured(), "public_key": _public_key()})

@login_required
@require_http_methods(["POST"])
def web_push_subscribe_api(request):
    from accounts.models import PushSubscription
    import json
    try:
        payload = json.loads(request.body.decode("utf-8"))
        endpoint = (payload.get("endpoint") or "").strip()
        keys = payload.get("keys") or {}
        p256dh = (keys.get("p256dh") or "").strip()
        auth = (keys.get("auth") or "").strip()
        if not endpoint or not p256dh or not auth:
            return JsonResponse({"ok": False, "error": "Invalid push subscription."}, status=400)
        obj, _ = PushSubscription.objects.update_or_create(
            endpoint=endpoint, defaults={"user": request.user, "p256dh": p256dh, "auth": auth, "user_agent": request.META.get("HTTP_USER_AGENT", "")[:500], "active": True, "last_error": ""}
        )
        return JsonResponse({"ok": True, "id": obj.pk})
    except Exception:
        return JsonResponse({"ok": False, "error": "Could not save notification permission."}, status=400)

@login_required
@require_http_methods(["POST"])
def web_push_unsubscribe_api(request):
    from accounts.models import PushSubscription
    import json
    try:
        payload = json.loads(request.body.decode("utf-8"))
        endpoint = (payload.get("endpoint") or "").strip()
        PushSubscription.objects.filter(user=request.user, endpoint=endpoint).update(active=False)
        return JsonResponse({"ok": True})
    except Exception:
        return JsonResponse({"ok": False}, status=400)
