"""Platform-owner "enter customer workspace" (impersonation) — V50.44.

Security model
* Only a real platform superadmin (is_superuser) can start it; never an owner/assistant.
* No customer password is used or revealed. The superadmin's own login stays intact:
  the middleware only swaps `request.user` for the request, never calls login().
* Target must be an active, non-superuser Shop Owner (ADMIN) of the chosen branch.
* Every entry is audited (ImpersonationSession) with reason, mode, IP and end cause.
* Default mode is VIEW ONLY: any state-changing request is refused. FULL mode must be
  chosen explicitly. Credential changes (password / PIN) are blocked in both modes.
* Auto-expires after ImpersonationSession.DURATION_MINUTES.
* A banner is shown on every page while inside a customer workspace.
"""
from functools import wraps

from django.utils import timezone

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import ImpersonationSession

SESSION_KEY = 'imp_session_id'
SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')
# Always allowed even in view-only mode (they do not change customer data).
ALWAYS_ALLOWED_POST = ('/hq/workspace/', '/logout/', '/static/')
# Never allowed while impersonating (credentials stay with the customer).
BLOCKED_PREFIXES = ('/change-password/', '/pin/', '/device-pin/', '/profile/', '/accounts/change-password/',
                    '/accounts/delete-account/')


def _client_ip(request):
    fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return (fwd.split(',')[0].strip() if fwd else request.META.get('REMOTE_ADDR')) or None


def pick_owner_for_branch(pharmacy):
    User = get_user_model()
    return (pharmacy.managers.filter(role=User.Role.ADMIN, is_superuser=False, is_active=True)
            .order_by('id').first())


def _end(request, cause):
    sid = request.session.pop(SESSION_KEY, None)
    request.session.pop('active_pharmacy_id', None)
    if sid:
        log = ImpersonationSession.objects.filter(pk=sid).first()
        if log is not None:
            log.close(cause)


class ImpersonationMiddleware:
    """Placed after AuthenticationMiddleware and before PharmacyContextMiddleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.impersonating = False
        request.real_user = None
        request.impersonation = None
        sid = request.session.get(SESSION_KEY)
        user = getattr(request, 'user', None)
        if sid:
            if user is None or not user.is_authenticated or not user.is_superuser:
                request.session.pop(SESSION_KEY, None)
            else:
                log = ImpersonationSession.objects.filter(pk=sid, admin_id=user.pk).select_related('target').first()
                target = log.target if log else None
                if log is None or log.ended_at is not None or target is None or not target.is_active:
                    _end(request, 'EXPIRED')
                    messages.info(request, 'Customer workspace session ended.')
                elif log.is_expired:
                    _end(request, 'EXPIRED')
                    messages.warning(request, f'Customer workspace session expired after {ImpersonationSession.DURATION_MINUTES} minutes. You are back in your own account.')
                else:
                    blocked = self._blocked(request, log)
                    request.real_user = user
                    request.user = target
                    request.impersonating = True
                    request.impersonation = log
                    if blocked is not None:
                        return blocked
        return self.get_response(request)

    @staticmethod
    def _blocked(request, log):
        path = request.path
        if any(path.startswith(p) for p in BLOCKED_PREFIXES) and request.method not in SAFE_METHODS:
            return _deny(request, 'Credentials cannot be changed while viewing a customer workspace.')
        if log.mode == ImpersonationSession.Mode.VIEW and request.method not in SAFE_METHODS:
            if not any(path.startswith(p) for p in ALWAYS_ALLOWED_POST):
                return _deny(request, 'View-only mode: this action would change customer data. '
                                      'Exit and re-enter with Full access if you really need to make changes.')
        return None


def _deny(request, message):
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', ''):
        return JsonResponse({'error': 'impersonation_blocked', 'message': message}, status=403)
    return HttpResponseForbidden(message)


def impersonation_context(request):
    log = getattr(request, 'impersonation', None)
    if not log:
        return {}
    return {
        'IMPERSONATING': True,
        'IMPERSONATION': log,
        'IMPERSONATION_MINUTES_LEFT': max(0, int((log.expires_at - timezone.now()).total_seconds() // 60)),
        'IMPERSONATION_EXIT_URL': reverse('hq:workspace_exit'),
    }


def real_superadmin_required(view):
    """Gate on the REAL logged-in user, ignoring any active impersonation swap."""
    @wraps(view)
    def wrapped(request, *a, **kw):
        real = getattr(request, 'real_user', None) or request.user
        if not real.is_authenticated:
            return redirect(f"{reverse('accounts:login')}?next={request.path}")
        if not real.is_superuser:
            raise PermissionDenied('Platform superadmins only.')
        request.real_user = real
        return view(request, *a, **kw)
    return wrapped


@real_superadmin_required
def workspace_picker_view(request):
    """Shown right after superadmin login: my own dashboard, or a customer's branch."""
    from pharmacies.models import Pharmacy
    from django.db.models import Prefetch
    User = get_user_model()
    # Picker is a "home" screen: leave any customer workspace first.
    if request.session.get(SESSION_KEY):
        _end(request, 'SWITCHED')
        request.impersonating = False
    branches = (Pharmacy.objects.all().order_by('name')
                .prefetch_related(Prefetch('managers', queryset=User.objects.filter(
                    role=User.Role.ADMIN, is_superuser=False, is_active=True).order_by('id'), to_attr='owners')))
    rows = []
    for ph in branches:
        owner = ph.owners[0] if ph.owners else None
        sub = None
        if owner is not None:
            try:
                sub = owner.subscription
            except Exception:
                sub = None
        rows.append({'pharmacy': ph, 'owner': owner, 'subscription': sub,
                     'enterable': owner is not None})
    recent = ImpersonationSession.objects.filter(admin=request.real_user)[:5]
    return render(request, 'hq/workspace_picker.html', {
        'rows': rows, 'recent': recent,
        'reasons': ImpersonationSession.Reason.choices,
        'minutes': ImpersonationSession.DURATION_MINUTES,
    })


@real_superadmin_required
@require_POST
def workspace_enter_view(request):
    from pharmacies.models import Pharmacy
    try:
        pharmacy = Pharmacy.objects.get(pk=int(request.POST.get('pharmacy', '')))
    except (ValueError, Pharmacy.DoesNotExist):
        messages.error(request, 'Choose a valid branch.')
        return redirect('hq:workspace_picker')
    owner = pick_owner_for_branch(pharmacy)
    if owner is None:
        messages.error(request, f'{pharmacy.name} has no active shop owner account to open.')
        return redirect('hq:workspace_picker')
    mode = request.POST.get('mode', 'VIEW')
    mode = mode if mode in ImpersonationSession.Mode.values else 'VIEW'
    reason = request.POST.get('reason', 'SUPPORT')
    reason = reason if reason in ImpersonationSession.Reason.values else 'SUPPORT'
    if request.session.get(SESSION_KEY):
        _end(request, 'SWITCHED')
    log = ImpersonationSession.objects.create(
        admin=request.real_user, admin_username=request.real_user.get_username(),
        target=owner, target_username=owner.get_username(),
        pharmacy_id_snapshot=pharmacy.pk, pharmacy_name=pharmacy.name,
        mode=mode, reason=reason, note=(request.POST.get('note') or '')[:300],
        ip_address=_client_ip(request), user_agent=(request.META.get('HTTP_USER_AGENT') or '')[:300])
    request.session[SESSION_KEY] = log.pk
    request.session['active_pharmacy_id'] = pharmacy.pk
    messages.success(request, f'You are now viewing {pharmacy.name} as its owner '
                              f'({log.get_mode_display().lower()}). Session ends automatically in {ImpersonationSession.DURATION_MINUTES} minutes.')
    return redirect('dashboard:home')


@real_superadmin_required
@require_POST
def workspace_exit_view(request):
    _end(request, 'EXIT')
    messages.success(request, 'You have left the customer workspace and are back in your own account.')
    return redirect('hq:dashboard')


@real_superadmin_required
def workspace_log_view(request):
    logs = ImpersonationSession.objects.all()[:200]
    return render(request, 'hq/workspace_log.html', {'logs': logs})


from django.contrib.auth.signals import user_logged_out
from django.dispatch import receiver


@receiver(user_logged_out, dispatch_uid='hq_close_impersonation_on_logout')
def _close_on_logout(sender, request=None, **kwargs):
    sid = request.session.get(SESSION_KEY) if request is not None and hasattr(request, 'session') else None
    if sid:
        log = ImpersonationSession.objects.filter(pk=sid).first()
        if log is not None:
            log.close('LOGOUT')
