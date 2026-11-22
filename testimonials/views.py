import json
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from accounts.models import User

from . import services
from .models import (TestimonialAuditLog, TestimonialInvitation, TestimonialReview,
                     TestimonialSettings)

MAX_SUBMITS_PER_HOUR = 5


def superadmin_required(view):
    """Server-side gate: only the platform superadmin (is_superuser)."""
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied('Testimonials management is for platform superadmins only.')
        return view(request, *args, **kwargs)
    return wrapped


RECIPIENT_ROLES = (User.Role.ADMIN, User.Role.ASSISTANT, User.Role.EMPLOYEE)


def _customers(role=''):
    """Anyone who uses the system: owners, assistant managers and employees (never the superadmin
    and never the branch attendance display accounts)."""
    qs = User.objects.filter(role__in=RECIPIENT_ROLES, is_superuser=False, is_active=True)
    if role in RECIPIENT_ROLES:
        qs = qs.filter(role=role)
    return qs


# --------------------------------------------------------------------------- admin
@superadmin_required
@never_cache
def admin_dashboard(request):
    cfg = TestimonialSettings.load()
    tab = request.GET.get('tab', 'reviews')
    if tab not in ('reviews', 'customers', 'invitations', 'audit'):
        tab = 'reviews'
    q = (request.GET.get('q') or '').strip()
    status = (request.GET.get('status') or '').upper()

    inv = TestimonialInvitation.objects
    invited = inv.count()
    answered = inv.filter(status=TestimonialInvitation.Status.ANSWERED).count()
    approved = TestimonialReview.objects.filter(status='APPROVED')
    stats = {
        'invited': invited,
        'answered': answered,
        'waiting': inv.filter(status='AUTHORIZED').count(),
        'response_rate': round(answered * 100 / invited) if invited else 0,
        'pending': TestimonialReview.objects.filter(status='PENDING').count(),
        'approved': approved.count(),
        'average': round(float(approved.aggregate(a=Avg('rating'))['a'] or 0), 1),
    }
    ctx = {'cfg': cfg, 'tab': tab, 'stats': stats, 'q': q, 'status': status}

    if tab == 'reviews':
        qs = TestimonialReview.objects.select_related('customer', 'moderated_by')
        if status in ('PENDING', 'APPROVED', 'REJECTED'):
            qs = qs.filter(status=status)
        ctx['page_obj'] = Paginator(qs, 20).get_page(request.GET.get('page'))
    elif tab == 'customers':
        role = (request.GET.get('role') or '').upper()
        ctx['role'] = role
        ctx['roles'] = [(r.value, r.label) for r in RECIPIENT_ROLES]
        qs = _customers(role).select_related('pharmacy').order_by('first_name', 'username')
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) |
                           Q(last_name__icontains=q) | Q(phone_number__icontains=q))
        page = Paginator(qs, 25).get_page(request.GET.get('page'))
        ids = [u.pk for u in page]
        latest = {}
        for i in TestimonialInvitation.objects.filter(customer_id__in=ids).order_by('authorized_at'):
            latest[i.customer_id] = i
        for u in page:
            u.t_invite = latest.get(u.pk)
        ctx['page_obj'] = page
    elif tab == 'invitations':
        qs = TestimonialInvitation.objects.select_related('customer', 'authorized_by')
        if status in dict(TestimonialInvitation.Status.choices):
            qs = qs.filter(status=status)
        ctx['page_obj'] = Paginator(qs, 25).get_page(request.GET.get('page'))
    else:
        ctx['page_obj'] = Paginator(TestimonialAuditLog.objects.select_related('actor', 'target_user'),
                                    30).get_page(request.GET.get('page'))
    return render(request, 'testimonials/admin_dashboard.html', ctx)


def _back(request, tab='reviews'):
    return redirect(f'/hq/testimonials/?tab={tab}')


@superadmin_required
@require_POST
def admin_settings(request):
    cfg = TestimonialSettings.load()
    was = cfg.enabled
    cfg.enabled = request.POST.get('enabled') == 'on'
    cfg.show_on_landing = request.POST.get('show_on_landing') == 'on'
    try:
        days = int(request.POST.get('snooze_days', cfg.snooze_days))
        cfg.snooze_days = max(1, min(60, days))
    except (TypeError, ValueError):
        pass
    cfg.updated_by = request.user
    cfg.save()
    services.log(request.user, 'SETTINGS',
                 detail=f'enabled={cfg.enabled} (was {was}), landing={cfg.show_on_landing}, snooze={cfg.snooze_days}d')
    messages.success(request, 'Testimonial settings saved.' + ('' if cfg.enabled else ' Rating popups are OFF.'))
    return _back(request, 'reviews')


@superadmin_required
@require_POST
def admin_invite(request):
    """Authorise the popup for chosen users, or for everyone matching a role/search filter."""
    if request.POST.get('scope') == 'filter':
        role = (request.POST.get('role') or '').upper()
        qs = _customers(role)
        q = (request.POST.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) |
                           Q(last_name__icontains=q) | Q(phone_number__icontains=q))
        users = list(qs[:2000])
    else:
        ids = []
        for raw in request.POST.getlist('customers'):
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        users = list(_customers().filter(pk__in=ids)) if ids else []
    if not users:
        messages.warning(request, 'Select at least one user first.')
        return _back(request, 'customers')
    created = skipped = 0
    for user in users:
        try:
            with transaction.atomic():
                inv = TestimonialInvitation.objects.create(customer=user, authorized_by=request.user)
                services.log(request.user, 'INVITE_AUTHORIZED', target_user=user, invitation=inv)
            created += 1
        except IntegrityError:
            skipped += 1  # already has an open invitation
    msg = f'{created} invitation(s) authorised.'
    if skipped:
        msg += f' {skipped} already had an open invitation.'
    if not TestimonialSettings.load().enabled:
        msg += ' Note: popups are currently switched OFF, so users will see nothing until you enable them.'
    messages.success(request, msg)
    return _back(request, 'invitations')


@superadmin_required
@require_POST
def admin_revoke(request, pk):
    with transaction.atomic():
        inv = get_object_or_404(TestimonialInvitation.objects.select_for_update(), pk=pk)
        if inv.status == TestimonialInvitation.Status.AUTHORIZED:
            inv.status = TestimonialInvitation.Status.REVOKED
            inv.save(update_fields=['status'])
            services.log(request.user, 'INVITE_REVOKED', target_user=inv.customer, invitation=inv)
            messages.success(request, 'Invitation revoked. The customer will not see the popup.')
        else:
            messages.info(request, 'Only waiting invitations can be revoked.')
    return _back(request, 'invitations')


@superadmin_required
@require_POST
def admin_moderate(request, pk, action):
    target = {'approve': 'APPROVED', 'reject': 'REJECTED', 'unpublish': 'PENDING'}.get(action)
    if not target:
        raise PermissionDenied('Unknown action.')
    with transaction.atomic():
        review = get_object_or_404(TestimonialReview.objects.select_for_update(), pk=pk)
        review.status = target
        review.moderated_by = request.user
        review.moderated_at = timezone.now()
        review.save(update_fields=['status', 'moderated_by', 'moderated_at'])
        services.log(request.user, f'REVIEW_{action.upper()}', target_user=review.customer, review=review,
                     detail=f'{review.rating}★')
    messages.success(request, {'APPROVED': 'Review approved and now live on the landing page.',
                               'REJECTED': 'Review rejected. It will not be shown publicly.',
                               'PENDING': 'Review unpublished and moved back to Pending.'}[target])
    return _back(request, 'reviews')


# --------------------------------------------------------------------------- customer popup
def _eligible(request):
    u = request.user
    return (u.is_authenticated and not u.is_superuser and u.role in RECIPIENT_ROLES
            and not getattr(request, 'impersonating', False))


def _open_invitation(user):
    now = timezone.now()
    return (TestimonialInvitation.objects
            .filter(customer=user, status=TestimonialInvitation.Status.AUTHORIZED)
            .filter(Q(snoozed_until__isnull=True) | Q(snoozed_until__lte=now)).first())


def _business_default(user):
    try:
        if getattr(user, 'pharmacy_id', None):
            return user.pharmacy.name
        ph = user.managed_pharmacies.order_by('name').first()
        return ph.name if ph else ''
    except Exception:
        return ''


@login_required
@never_cache
@require_GET
def popup_state(request):
    if not _eligible(request) or not TestimonialSettings.load().enabled:
        return JsonResponse({'show': False})
    inv = _open_invitation(request.user)
    if not inv:
        return JsonResponse({'show': False})
    if inv.first_shown_at is None:
        inv.first_shown_at = timezone.now()
        inv.save(update_fields=['first_shown_at'])
    return JsonResponse({'show': True, 'name': request.user.get_full_name() or request.user.username,
                         'role': request.user.get_role_display(),
                         'business': _business_default(request.user)})


def _json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return None


@login_required
@require_POST
def popup_submit(request):
    if not _eligible(request) or not TestimonialSettings.load().enabled:
        return JsonResponse({'ok': False, 'message': 'Not available.'}, status=403)
    key = f'testi_submit_{request.user.pk}'
    count = cache.get(key, 0)
    if count >= MAX_SUBMITS_PER_HOUR:
        return JsonResponse({'ok': False, 'message': 'Too many attempts. Please try again later.'}, status=429)
    cache.set(key, count + 1, 3600)

    data = _json_body(request)
    if data is None:
        return JsonResponse({'ok': False, 'message': 'Invalid request.'}, status=400)
    try:
        rating = int(data.get('rating', 0))
    except (TypeError, ValueError):
        rating = 0
    comment = str(data.get('comment', '')).strip()
    if rating not in range(1, 6):
        return JsonResponse({'ok': False, 'message': 'Please choose a star rating from 1 to 5.'}, status=400)
    if len(comment) < 10:
        return JsonResponse({'ok': False, 'message': 'Please write at least 10 characters.'}, status=400)
    comment = comment[:600]
    consent = bool(data.get('consent'))
    name = str(data.get('display_name', '')).strip()[:80] if consent else ''
    business = str(data.get('business_name', '')).strip()[:80] if consent else ''

    with transaction.atomic():
        inv = (TestimonialInvitation.objects.select_for_update()
               .filter(customer=request.user, status=TestimonialInvitation.Status.AUTHORIZED).first())
        if inv is None:  # no invitation, or already answered (double click / replay)
            return JsonResponse({'ok': False, 'message': 'This invitation is no longer available.'}, status=409)
        review = TestimonialReview.objects.create(
            invitation=inv, customer=request.user, rating=rating, comment=comment,
            display_name=name, business_name=business, consent_public_name=consent,
            reviewer_role=request.user.get_role_display()[:40])
        inv.status = TestimonialInvitation.Status.ANSWERED
        inv.responded_at = timezone.now()
        inv.save(update_fields=['status', 'responded_at'])
        services.log(request.user, 'REVIEW_SUBMITTED', target_user=request.user, invitation=inv, review=review,
                     detail=f'{rating}★')
    _notify_admins(request.user, review)
    return JsonResponse({'ok': True, 'message': 'Thank you! Your review was sent and will appear after approval.'})


def _notify_admins(user, review):
    try:
        from notifications.models import Notification
        for admin in User.objects.filter(is_superuser=True, is_active=True).iterator():
            Notification.objects.create(
                recipient=admin, category='SYSTEM', level='INFO', title='New testimonial to review',
                message=f'{user.get_full_name() or user.username} left a {review.rating}★ review.',
                link_url='/hq/testimonials/?tab=reviews&status=PENDING',
                dedupe_key=f'testimonial-{review.pk}-admin-{admin.pk}')
    except Exception:
        pass  # a notification problem must never lose the review


@login_required
@require_POST
def popup_later(request):
    if not _eligible(request):
        return JsonResponse({'ok': False}, status=403)
    cfg = TestimonialSettings.load()
    n = TestimonialInvitation.objects.filter(
        customer=request.user, status=TestimonialInvitation.Status.AUTHORIZED
    ).update(snoozed_until=timezone.now() + timedelta(days=cfg.snooze_days))
    return JsonResponse({'ok': bool(n)})


@login_required
@require_POST
def popup_decline(request):
    if not _eligible(request):
        return JsonResponse({'ok': False}, status=403)
    with transaction.atomic():
        inv = (TestimonialInvitation.objects.select_for_update()
               .filter(customer=request.user, status=TestimonialInvitation.Status.AUTHORIZED).first())
        if inv:
            inv.status = TestimonialInvitation.Status.DECLINED
            inv.responded_at = timezone.now()
            inv.save(update_fields=['status', 'responded_at'])
            services.log(request.user, 'INVITE_DECLINED', target_user=request.user, invitation=inv)
    return JsonResponse({'ok': True})
