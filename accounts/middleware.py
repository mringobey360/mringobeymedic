import logging

from django.http import JsonResponse
from django.shortcuts import redirect
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from . import device_pin
from . import single_device

logger = logging.getLogger(__name__)


class ForcePasswordChangeMiddleware:
    """
    Redirects any logged-in user who must change their password
    (e.g. new employees using their first SMS-issued password)
    to the change-password page, until they do so.

    Matching is done on request.path (not resolver_match) because
    resolver_match is not yet populated when middleware runs before the view.
    """
    EXEMPT_PATH_PREFIXES = (
        '/static/',
        '/media/',
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self._exempt_paths = None

    def _get_exempt_paths(self):
        if self._exempt_paths is None:
            self._exempt_paths = {
                reverse('accounts:change_password'),
                reverse('accounts:logout'),
            }
        return self._exempt_paths

    def __call__(self, request):
        if (request.user.is_authenticated and not getattr(request, 'impersonating', False)
                and getattr(request.user, 'must_change_password', False)):
            exempt_paths = self._get_exempt_paths()
            is_exempt = request.path in exempt_paths or any(
                request.path.startswith(p) for p in self.EXEMPT_PATH_PREFIXES
            )
            if not is_exempt:
                return redirect(reverse('accounts:change_password'))

        return self.get_response(request)


class PharmacyContextMiddleware:
    """
    Resolves `request.pharmacy` — the pharmacy branch the current request
    should operate within — before any view runs. This is the single source
    of truth that keeps every branch's stock, sales, and reports independent:

    - An Employee is locked to their assigned branch; they can never switch.
    - A Shop Owner/Supervisor can manage several branches; the active one is
      remembered in their session and changed via the branch switcher.
    """

    def __call__(self, request):
        request.pharmacy = None

        try:
            if request.user.is_authenticated:
                if request.user.is_branch_display_role:
                    display_pharmacy = request.user.pharmacy
                    if display_pharmacy is not None and display_pharmacy.is_active:
                        request.pharmacy = display_pharmacy
                elif request.user.is_employee_role():
                    employee_pharmacy = request.user.pharmacy
                    if employee_pharmacy is not None and employee_pharmacy.is_active:
                        request.pharmacy = employee_pharmacy
                elif request.user.is_assistant_role():
                    profile = getattr(request.user, 'assistant_profile', None)
                    if profile and profile.active:
                        allowed = profile.accessible_pharmacies()
                        active_id = request.session.get('active_pharmacy_id')
                        assistant_pharmacy = allowed.filter(pk=active_id).first() if active_id else None
                        if assistant_pharmacy is None:
                            assistant_pharmacy = allowed.filter(pk=profile.pharmacy_id).first() or allowed.first()
                        if assistant_pharmacy is not None:
                            request.pharmacy = assistant_pharmacy
                            request.session['active_pharmacy_id'] = assistant_pharmacy.id
                        else:
                            request.session.pop('active_pharmacy_id', None)
                else:
                    from django.db.models import Q
                    from pharmacies.models import BranchAddOn
                    # A branch that's pending payment or cancelled can never
                    # be auto-selected (or kept selected) as the active branch.
                    managed = request.user.managed_pharmacies.filter(
                        is_active=True
                    ).filter(
                        Q(addon__isnull=True) | Q(addon__status=BranchAddOn.Status.ACTIVE)
                    ).order_by('name')
                    active_id = request.session.get('active_pharmacy_id')
                    pharmacy = managed.filter(pk=active_id).first() if active_id else None
                    if pharmacy is None:
                        # Prefer a branch whose own subscription is in good standing, so a
                        # lapsed branch never becomes the default landing branch.
                        from pharmacies.branch_billing import is_branch_locked
                        candidates = list(managed.select_related('addon'))
                        pharmacy = next((c for c in candidates if not is_branch_locked(c)), None) or (candidates[0] if candidates else None)
                        if pharmacy is not None:
                            request.session['active_pharmacy_id'] = pharmacy.id
                    request.pharmacy = pharmacy
        except Exception:
            # A DB hiccup here should degrade to "no active pharmacy", not crash every page.
            request.pharmacy = None

        return self.get_response(request)

    def __init__(self, get_response):
        self.get_response = get_response


class AssistantPermissionMiddleware:
    """Defense-in-depth permission gate for Manager Assistant accounts.

    Decorators protect shared views; this process_view layer also covers
    legacy/non-decorated endpoints so an assistant cannot bypass the same
    permission model by entering a URL directly.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            return None
        if request.user.is_branch_display_role:
            safe_names = {
                'attendance_qr_kiosk_view',
                'attendance_qr_token_view',
                'logout_view',
                'change_password_view',
            }
            if getattr(view_func, '__name__', '') in safe_names:
                return None
            raise PermissionDenied('Branch display accounts are restricted to the attendance QR screen.')
        if not request.user.is_assistant_role():
            return None
        # A Manager Assistant is a full operational delegate of the business
        # owner. Do not gate normal business views by individual checkboxes.
        # The assistant's active profile + dynamic branch scope are the
        # authoritative server-side controls. Platform/HQ views remain guarded
        # by their own decorators.
        from .decorators import MANAGER_ONLY_NAMES
        name = getattr(view_func, '__name__', '')
        if name in MANAGER_ONLY_NAMES:
            return None
        permission = None
        if request.user.is_assistant_role():
            profile = getattr(request.user, 'assistant_profile', None)
            if profile and profile.active:
                return None
        # Authentication/account utility pages are not feature-gated here.
        safe_names = {
            'login_view', 'logout_view', 'change_password_view', 'profile_view',
            'forgot_password_view', 'verify_otp_view', 'reset_password_view',
            'subscription_expired_view', 'pin_login_view', 'pin_setup_view', 'device_pin_view',
        }
        if name in safe_names:
            return None
        # Only enforce on known application modules. Platform/HQ decorators
        # remain authoritative for unrelated views.
        module = getattr(view_func, '__module__', '')
        if any(module.startswith(prefix) for prefix in ('dashboard.', 'sales.', 'inventory.', 'expenses.', 'accounts.', 'chat.', 'customer_registry.', 'notifications.')):
            raise PermissionDenied('Your Manager Assistant permissions do not allow this area.')
        return None

    def __call__(self, request):
        return self.get_response(request)


class ForcePinSetupMiddleware:
    """Every signed-in user must have a PIN before using the app.

    Users without a PIN (or whose PIN was locked / reset) are redirected to the
    "Create your PIN" page until they set one. Runs after ForcePasswordChange,
    so a user who must change a temporary password does that first.
    """
    EXEMPT_PATH_PREFIXES = (
        '/static/', '/media/', '/admin/', '/payments/', '/assistant/', '/jiunge/',
        '/sw.js', '/favicon.ico', '/robots.txt', '/sitemap.xml', '/offline/', '/server-unavailable/', '/health/', '/ready/',
        '/maintenance/', '/service-status/',
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self._exempt_paths = None

    def _get_exempt_paths(self):
        if self._exempt_paths is None:
            self._exempt_paths = {
                reverse('accounts:pin_setup'),
                reverse('accounts:logout'),
                reverse('accounts:login'),
                reverse('accounts:change_password'),
                reverse('accounts:subscription_expired'),
                reverse('accounts:request_renewal'),
            }
        return self._exempt_paths

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if (user is not None and user.is_authenticated and not getattr(request, 'impersonating', False)
                and device_pin.needs_pin_setup(user)):
            path = request.path
            exempt = path in self._get_exempt_paths() or any(
                path.startswith(p) for p in self.EXEMPT_PATH_PREFIXES
            )
            if not exempt:
                target = reverse('accounts:pin_setup')
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'error': 'pin_setup_required', 'redirect': target}, status=403)
                return redirect(target)
        return self.get_response(request)


class DeviceIdleLockMiddleware:
    """Lock an active trusted-device session after a period of inactivity.

    The authenticated Django session remains intact; only the application UI is
    blocked until the user proves possession of the PIN again.
    """
    EXEMPT_PATHS = ()

    def __init__(self, get_response):
        self.get_response = get_response
        self._exempt_paths = None

    def _get_exempt_paths(self):
        if self._exempt_paths is None:
            self._exempt_paths = {
                reverse('accounts:device_pin_lock'),
                reverse('accounts:device_pin_unlock'),
                reverse('accounts:logout'),
                reverse('accounts:login'),
                reverse('accounts:pin_login'),
                reverse('accounts:pin_setup'),
                reverse('accounts:change_password'),
            }
        return self._exempt_paths

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if (not user or not user.is_authenticated or getattr(request, 'impersonating', False)
                or not request.session.get('device_pin_idle_enabled')):
            return self.get_response(request)

        path = request.path
        if path in self._get_exempt_paths() or path.startswith('/static/') or path.startswith('/media/'):
            return self.get_response(request)

        if request.session.get('device_pin_locked'):
            target = reverse('accounts:device_pin_lock')
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'error': 'device_locked', 'redirect': target}, status=423)
            return redirect(target)

        idle = device_pin.session_idle_seconds(request)
        timeout = max(1, device_pin.IDLE_LOCK_MINUTES) * 60
        if idle >= timeout:
            device = device_pin.get_device(request)
            # Never log the user out because an idle-lock device record/cookie is
            # temporarily unavailable. The authenticated Django session remains
            # intact; simply disable idle-lock for this session and let the
            # request continue. This avoids destructive logout loops on mobile
            # browsers and after cookie eviction.
            if device is None or device.user_id != user.pk:
                request.session['device_pin_idle_enabled'] = False
                request.session.pop('device_pin_locked', None)
                request.session.modified = True
                return self.get_response(request)
            device_pin.lock_session(request)
            return redirect(reverse('accounts:device_pin_lock') + '?next=' + request.get_full_path())

        device_pin.mark_activity(request)
        request.device_pin_idle_enabled = True
        request.device_pin_idle_lock_ms = timeout * 1000
        return self.get_response(request)


class SingleDeviceSessionMiddleware:
    """Enforce "one active device per account" on every request (see single_device.py).

    The device that owns the lock keeps working. Any other still-valid session of the
    same account (e.g. one that was released by a manager and replaced) is signed out
    immediately instead of being allowed to carry on in parallel.
    """
    EXEMPT_PATH_PREFIXES = (
        '/static/', '/media/', '/admin/', '/payments/', '/sw.js', '/favicon.ico', '/robots.txt', '/sitemap.xml',
        '/offline/', '/server-unavailable/', '/health/', '/ready/', '/maintenance/', '/service-status/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if (user is not None and user.is_authenticated
                and not getattr(request, 'impersonating', False)
                and single_device.applies_to(user)
                and not any(request.path.startswith(p) for p in self.EXEMPT_PATH_PREFIXES)):
            try:
                outcome = self._enforce(request, user)
            except Exception:
                # Never lock everybody out because of a bookkeeping failure.
                logger.exception('Single-device check failed for user_id=%s', user.pk)
                outcome = None
            if outcome is not None:
                return outcome
        response = self.get_response(request)
        if getattr(request, '_smartpos_new_dev_token', None) and request.user.is_authenticated:
            single_device.attach_cookie(request, response)
        return response

    def _enforce(self, request, user):
        from django.contrib.auth import logout
        from django.contrib import messages
        from django.utils import timezone
        from .models import ActiveDeviceSession

        lock = ActiveDeviceSession.objects.filter(user=user).first()
        if lock is None:
            # Session that pre-dates this feature (or a released lock): adopt it.
            single_device.claim(request, user)
            return None

        has_cookie = single_device.COOKIE_NAME in request.COOKIES
        mine = single_device.device_hash(request)
        session_key = request.session.session_key
        owns = (has_cookie and lock.device_hash == mine) or (bool(session_key) and lock.session_key == session_key)

        if owns:
            now = timezone.now()
            if (not single_device.is_background_request(request)
                    and (now - lock.last_seen).total_seconds() >= single_device.touch_seconds()):
                ActiveDeviceSession.objects.filter(pk=lock.pk).update(
                    last_seen=now, session_key=session_key or lock.session_key, device_hash=mine)
            elif not has_cookie:
                ActiveDeviceSession.objects.filter(pk=lock.pk).update(device_hash=mine)
            return None

        if not single_device._is_live(lock):
            single_device.claim(request, user)  # previous device is gone; this one takes over
            return None

        # Another device owns the account: end this session.
        logout(request)
        login_url = reverse('accounts:login')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'error': 'signed_in_elsewhere', 'redirect': login_url}, status=401)
        messages.warning(request, 'You were signed out because this account is now active on another device.')
        return redirect(login_url)
