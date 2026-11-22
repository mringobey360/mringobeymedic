import logging
import random
import string
import csv
from datetime import datetime

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from datetime import timedelta
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from sms_integration.utils import send_employee_credentials_sms, send_otp_sms, send_sms
from .decorators import admin_required, manager_required, branch_display_required, branch_display_only_required
from .forms import (
    LoginForm, EmployeeCreateForm, EmployeeEditForm, ForgotPasswordForm, OTPVerifyForm, StyledSetPasswordForm, ProfileForm,
    EmployeeCompensationForm, WeeklyMealScheduleForm, MealAdjustmentForm, MealTimingSettingsForm, ManagerAssistantCreateForm,
    AttendanceDisplayAccountForm, PinSetupForm, ChangePinForm,
)
from core import ratelimit
from . import device_pin
from . import single_device
from .models import User, OTP, WeeklyMealSchedule, MealAdjustment, MealTimingSettings, MealLedger, MealWorkday, AttendanceRecord, AttendanceVerification, AttendanceExceptionRequest, AttendanceCheckpoint, MealIssueReport, WorkdayReopenRequest, TrustedDevice, UserPin, PinSecurityAudit


logger = logging.getLogger(__name__)


def _safe_idle_next(request):
    """Return a same-site path for post-PIN unlock navigation."""
    target = (request.GET.get('next') or request.POST.get('next') or '').strip()
    if target.startswith('/') and not target.startswith('//'):
        return target
    return reverse('dashboard:home')



def subscription_expired_view(request):
    """Dedicated read-only page shown when a customer has no valid subscription."""
    subscription = getattr(request, 'subscription', None)

    # The lock middleware normally attaches the subscription. For the
    # expired/missing case it intentionally cannot, so resolve the details
    # here for a better customer-facing message.
    if subscription is None and request.user.is_authenticated and not request.user.is_superuser:
        from hq.models import Subscription
        try:
            if request.user.is_manager_role():
                subscription = request.user.business_owner().subscription
            elif request.user.is_assistant_role():
                profile = getattr(request.user, 'assistant_profile', None)
                subscription = profile.manager.subscription if profile and profile.active else None
            elif getattr(request, 'pharmacy', None) is not None:
                manager_ids = request.pharmacy.managers.values_list('pk', flat=True)
                subscription = (
                    Subscription.objects.filter(owner_id__in=manager_ids)
                    .select_related('owner').order_by('-end_date').first()
                )
        except Subscription.DoesNotExist:
            subscription = None

    days_expired = None
    if subscription is not None and subscription.end_date < timezone.localdate():
        days_expired = (timezone.localdate() - subscription.end_date).days

    upgrade_plans = []
    if subscription is not None and getattr(subscription, 'is_trial', False):
        from hq.models import PaymentSettings, SignupRequest, TRIAL_DAYS
        from hq.packages_views import TIER_INFO, TIER_ORDER
        prices = PaymentSettings.load()
        for tier in TIER_ORDER:
            if tier == SignupRequest.Tier.ENTERPRISE:
                continue
            monthly = prices.price_for(SignupRequest.Plan.MONTHLY, tier)
            yearly = prices.price_for(SignupRequest.Plan.YEARLY, tier)
            upgrade_plans.append({
                'tier': tier, 'label': dict(SignupRequest.Tier.choices)[tier],
                'monthly': monthly, 'yearly': yearly,
                'saving': (monthly * 12 - yearly) if monthly * 12 > yearly else None,
                'popular': tier == SignupRequest.Tier.BUSINESS, **TIER_INFO[tier],
            })
    return render(request, 'accounts/subscription_expired.html', {
        'subscription': subscription,
        'days_expired': days_expired,
        'upgrade_plans': upgrade_plans,
        'is_trial_ended': bool(subscription is not None and getattr(subscription, 'is_trial', False)),
    })



@login_required
def branch_subscription_expired_view(request):
    """Shown only for the branch whose own subscription lapsed. Other branches stay usable."""
    from pharmacies.branch_billing import branch_billing_state
    pharmacy = getattr(request, 'pharmacy', None)
    state = branch_billing_state(pharmacy)
    if not state or not state['locked']:
        return redirect('dashboard:home')
    other_branches = []
    if request.user.is_management_user():
        if request.user.is_assistant_role():
            profile = getattr(request.user, 'assistant_profile', None)
            candidates = list(profile.accessible_pharmacies()) if profile and profile.active else []
        else:
            candidates = list(request.user.accessible_management_pharmacies().select_related('addon'))
        for ph in candidates:
            if ph.pk == pharmacy.pk:
                continue
            st = branch_billing_state(ph)
            other_branches.append({'pharmacy': ph, 'locked': bool(st and st['locked'])})
    return render(request, 'accounts/branch_subscription_expired.html', {
        'pharmacy': pharmacy, 'state': state, 'other_branches': other_branches,
        'can_renew': request.user.is_admin_role(),
    })


@login_required
@require_http_methods(["GET", "POST"])
def request_renewal_view(request):
    """Allow a locked pharmacy owner to request renewal without restoring access automatically.

    Payment is still confirmed by the platform superadmin. The request is recorded
    on the existing subscription/signup record, so no new customer account is created.
    """
    if request.user.is_superuser:
        return redirect('hq:subscription_list')
    if not request.user.is_management_user():
        messages.error(request, "Only the business owner can request a subscription renewal.")
        return redirect('accounts:subscription_expired')

    from hq.models import PaymentSettings, Subscription, SignupRequest
    try:
        subscription = request.user.business_owner().subscription
    except Subscription.DoesNotExist:
        messages.error(request, "No subscription was found for this account. Please contact support.")
        return redirect('accounts:subscription_expired')

    payment_settings = PaymentSettings.load()
    from payments.models import PesapalSettings, PaymentGatewaySettings
    from payments import abliner_client
    from payments.services import resolve_gateway, pesapal_usable
    pesapal_settings = PesapalSettings.load()
    gateway_settings = PaymentGatewaySettings.load()
    abliner_ready = abliner_client.is_usable()
    selected_gateway = resolve_gateway() if gateway_settings.is_enabled else None
    online_payment_ready = bool(
        gateway_settings.is_enabled and (
            (selected_gateway == PaymentGatewaySettings.Gateway.ABLINER and abliner_ready)
            or (selected_gateway == PaymentGatewaySettings.Gateway.PESAPAL and pesapal_usable())
        )
    )
    online_gateway_label = (
        'ABLiner' if selected_gateway == PaymentGatewaySettings.Gateway.ABLINER
        else 'PesaPal' if selected_gateway == PaymentGatewaySettings.Gateway.PESAPAL
        else ''
    )
    if request.method == 'POST':
        plan = request.POST.get('plan', subscription.plan)
        tier = request.POST.get('tier', getattr(subscription, 'tier', SignupRequest.Tier.STARTER))
        reference = request.POST.get('payment_reference', '').strip()
        if plan not in (Subscription.Plan.MONTHLY, Subscription.Plan.YEARLY) or tier not in SignupRequest.Tier.values:
            messages.error(request, "Please select a valid renewal plan.")
        else:
            now = timezone.localtime(timezone.now()).strftime('%Y-%m-%d %H:%M')
            line = (
                f"[RENEWAL REQUEST] {now} | Tier: {tier} | Plan: {plan} | "
                f"Payment reference: {reference or 'Not provided'} | Status: PENDING REVIEW"
            )
            signup = subscription.signup_request
            if signup is not None:
                signup.notes = (signup.notes + "\n" if signup.notes else "") + line
                signup.save(update_fields=['notes'])
            messages.success(
                request,
                "Renewal request received. After payment is confirmed, the administrator will reactivate your access."
            )
            return redirect('accounts:subscription_expired' if subscription.is_expired else 'dashboard:home')

    tier_options = [
        (SignupRequest.Tier.STARTER, 'Starter', payment_settings.starter_monthly_price, payment_settings.starter_yearly_price, 1, 3),
        (SignupRequest.Tier.BUSINESS, 'Business', payment_settings.business_monthly_price, payment_settings.business_yearly_price, 2, 10),
        (SignupRequest.Tier.PROFESSIONAL, 'Professional', payment_settings.professional_monthly_price, payment_settings.professional_yearly_price, 5, 25),
        (SignupRequest.Tier.ENTERPRISE, 'Enterprise', payment_settings.enterprise_monthly_price, payment_settings.enterprise_yearly_price, 'Custom', 'Custom'),
    ]
    return render(request, 'accounts/subscription_renewal.html', {
        'subscription': subscription,
        'payment_settings': payment_settings,
        'tier_options': tier_options,
        'pesapal_ready': pesapal_settings.is_enabled and pesapal_settings.is_configured,
        'gateway_settings': gateway_settings,
        'abliner_ready': abliner_ready,
        'online_payment_ready': online_payment_ready,
        'online_gateway_label': online_gateway_label,
        'online_gateway_is_abliner': selected_gateway == PaymentGatewaySettings.Gateway.ABLINER,
    })

LOGIN_MAX_FAILS_PER_USER = 6      # wrong passwords per username ...
LOGIN_MAX_FAILS_PER_IP = 30       # ... and per IP (shared shop Wi-Fi friendly) ...
LOGIN_LOCK_SECONDS = 15 * 60      # ... within this window, then locked for the rest of it


def login_view(request):
    if request.user.is_authenticated:
        if request.user.is_branch_display_role:
            return redirect('accounts:attendance_qr_kiosk')
        return redirect(_post_login_url(request.user))

    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        username = form.cleaned_data['username']
        password = form.cleaned_data['password']
        # Brute-force guard (the PIN already had one; the password form did not).
        ip = ratelimit.client_ip(request)
        blocked_user, wait_user = ratelimit.peek('login-user', username, limit=LOGIN_MAX_FAILS_PER_USER)
        blocked_ip, wait_ip = ratelimit.peek('login-ip', ip, limit=LOGIN_MAX_FAILS_PER_IP)
        if blocked_user or blocked_ip:
            messages.error(request, f"Too many failed sign-in attempts. Please wait {ratelimit.human_wait(max(wait_user, wait_ip))} and try again.")
            user = None
            throttled = True
        else:
            user = authenticate(request, username=username, password=password)
            throttled = False
        if user is not None:
            ratelimit.reset('login-user', username)
            if not user.is_active:
                messages.error(request, "This account has been deactivated. Contact your supervisor.")
            elif (blocking := single_device.check_login_allowed(request, user)) is not None:
                # Correct password, but the account is active on another device.
                messages.error(request, single_device.blocked_message(blocking))
            else:
                login(request, user)
                single_device.claim(request, user)
                # Keep the authentication session short-lived unless the user
                # explicitly asks for a persistent login.  This makes the
                # checkbox functional rather than cosmetic.
                remember_me = request.POST.get('remember_me') == 'on'
                if remember_me:
                    request.session.set_expiry(60 * 60 * 24 * 30)  # 30 days
                else:
                    request.session.set_expiry(0)  # expire when browser closes
                if request.POST.get('reason') == 'forgot':
                    # "Forgot PIN?": password proved identity, now force a new PIN.
                    device_pin.require_reset(user)
                response = redirect(_post_login_url(user))
                # PIN trust is explicit. A password login remains valid even when
                # the user does not trust this browser.
                trusted = request.POST.get('trust_device') == 'on'
                request.session['device_pin_idle_enabled'] = bool(trusted and device_pin.get_pin(user) is not None and not device_pin.get_pin(user).is_locked)
                if trusted:
                    device_pin.trust_device_if_pin_ready(request, user, response)
                else:
                    device_pin.forget_current_device(request)
                    device_pin.clear_device_cookie(response)
                single_device.attach_cookie(request, response)
                return response
        elif not throttled:
            ratelimit.hit('login-user', username, limit=LOGIN_MAX_FAILS_PER_USER, window=LOGIN_LOCK_SECONDS)
            ratelimit.hit('login-ip', ip, limit=LOGIN_MAX_FAILS_PER_IP, window=LOGIN_LOCK_SECONDS)
            messages.error(request, "Incorrect username or password.")

    # PIN sign-in: when this browser is remembered for a user with a PIN, show
    # the PIN pad first. "Use another account" links back with ?use=password.
    pin_reason = request.POST.get('reason') or request.GET.get('reason') or ''
    if pin_reason not in ('forgot', 'locked'):
        pin_reason = ''
    pin_device = None
    if request.method == 'GET' and request.GET.get('use') != 'password':
        pin_device = device_pin.get_device(request)

    from . import google_auth
    return render(request, 'accounts/login.html', {
        **google_auth.template_context(request),
        'form': form,
        'pin_device': pin_device,
        'pin_length': device_pin.PIN_LENGTH,
        'pin_reason': pin_reason,
    })


def _post_login_url(user):
    if user.is_superuser:
        # Platform owner chooses: own HQ dashboard or a customer's branch (audited).
        return reverse('hq:workspace_picker')
    if user.is_branch_display_role:
        return reverse('accounts:attendance_qr_kiosk')
    return reverse('dashboard:home')


def _is_ajax(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


@require_POST
def pin_login_view(request):
    """AJAX endpoint behind the PIN pad on the login page."""
    if request.user.is_authenticated:
        return JsonResponse({'ok': True, 'redirect': _post_login_url(request.user)})

    password_url = reverse('accounts:login') + '?use=password'
    device = device_pin.get_device(request)
    pin_obj = device_pin.get_pin(device.user) if device is not None else None
    if device is None or pin_obj is None:
        resp = JsonResponse({
            'ok': False, 'fallback': True, 'redirect': password_url,
            'error': 'PIN sign-in is not available on this device. Please sign in with your username and password.',
        }, status=403)
        device_pin.clear_device_cookie(resp)
        return resp

    retry_after = device_pin.check_ip_rate_limit(request)
    if retry_after:
        return JsonResponse({
            'ok': False, 'rate_limited': True, 'retry_after': retry_after,
            'error': f'Too many PIN requests from this network. Try again in about {retry_after} seconds.',
        }, status=429)

    pin = (request.POST.get('pin') or '').strip()
    if len(pin) != device_pin.PIN_LENGTH or not pin.isdigit():
        return JsonResponse({'ok': False, 'error': f'Enter your {device_pin.PIN_LENGTH}-digit PIN.'}, status=400)

    result = device_pin.verify_pin(pin_obj, pin)
    if not result['ok']:
        if result['locked']:
            return JsonResponse({
                'ok': False, 'fallback': True, 'redirect': password_url + '&reason=locked',
                'error': 'Your PIN is locked. Sign in with your password to create a new PIN.',
            }, status=403)
        if result['cooldown']:
            seconds = result['retry_after']
            label = f'{seconds} seconds' if seconds < 60 else '5 minutes'
            return JsonResponse({
                'ok': False, 'cooldown': True, 'retry_after': seconds,
                'attempts_left': result['attempts_left'],
                'error': f'Too many wrong PIN attempts. Please wait {label} before trying again.',
            }, status=429)
        left = result['attempts_left']
        plural = 'attempt' if left == 1 else 'attempts'
        return JsonResponse({'ok': False, 'attempts_left': left, 'error': f'Wrong PIN. {left} {plural} left.'}, status=400)

    user = device.user
    blocking = single_device.check_login_allowed(request, user)
    if blocking is not None:
        # PIN was correct, but the account is active on another device.
        return JsonResponse({'ok': False, 'error': single_device.blocked_message(blocking)}, status=403)
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    single_device.claim(request, user)
    request.session.set_expiry(0)
    request.session['device_pin_idle_enabled'] = True
    device_pin.mark_activity(request)
    response = JsonResponse({'ok': True, 'redirect': _post_login_url(user)})
    device_pin.trust_device(request, user, response)
    single_device.attach_cookie(request, response)
    return response


@login_required
def pin_setup_view(request):
    """Mandatory "Create your PIN" page (first time, or after the PIN was locked / reset)."""
    user = request.user
    if not device_pin.pin_required() or not device_pin.role_allowed(user):
        return redirect(_post_login_url(user))
    if user.must_change_password:
        return redirect('accounts:change_password')

    pin_obj = device_pin.get_pin(user)
    is_reset = pin_obj is not None and pin_obj.is_locked
    if pin_obj is not None and not is_reset:
        return redirect('accounts:device_pin')  # already has a PIN; changing it lives on the Device PIN page

    form = PinSetupForm(request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            trusted = bool(form.cleaned_data.get('trust_device'))
            try:
                # Persist the PIN first. Trusted-device registration is a separate,
                # best-effort operation: a device/cookie/database issue must not
                # roll back a valid PIN and trap the user on this page.
                from django.db import transaction
                with transaction.atomic():
                    device_pin.set_pin(user, form.cleaned_data['pin'])
                    _audit_pin_event(user, PinSecurityAudit.Action.CHANGED, request=request, details={'event': 'created'})

                url = _post_login_url(user)
                response = JsonResponse({'ok': True, 'redirect': url}) if _is_ajax(request) else redirect(url)
                device_trusted = False
                if trusted:
                    try:
                        device_pin.trust_device(request, user, response)
                        device_trusted = True
                    except Exception:
                        # Keep the PIN; do not claim the browser is trusted. Log
                        # details server-side while allowing the user to continue.
                        logger.exception('PIN saved but trusted-device registration failed for user_id=%s', user.pk)
                        device_pin.clear_device_cookie(response)
                else:
                    try:
                        device_pin.forget_current_device(request)
                    except Exception:
                        logger.exception('Could not forget current device during PIN setup for user_id=%s', user.pk)
                    device_pin.clear_device_cookie(response)

                request.session['device_pin_idle_enabled'] = device_trusted
                if device_trusted:
                    pass  # no alert: the animated PIN screen already confirms success
                elif trusted:
                    messages.warning(request, 'Your PIN was saved, but this device could not be trusted. You can continue now; use your password on this device next time.')
                else:
                    pass  # no alert on success
                if _is_ajax(request) and trusted and not device_trusted:
                    response.content = __import__('json').dumps({
                        'ok': True, 'redirect': url,
                        'warning': 'Your PIN was saved, but this device could not be trusted. You can continue now; use your password on this device next time.',
                    })
                return response
            except Exception:
                logger.exception('PIN setup failed while saving PIN for user_id=%s', user.pk)
                error = 'We could not save your PIN. Please try again; if this continues, contact your system administrator.'
                if _is_ajax(request):
                    return JsonResponse({'ok': False, 'error': error}, status=500)
                messages.error(request, error)
        error = next(iter(form.errors.get('pin', []) or form.errors.get('pin_confirm', []) or form.non_field_errors()), 'Invalid PIN.')
        if _is_ajax(request):
            return JsonResponse({'ok': False, 'error': error}, status=400)
        messages.error(request, error)

    return render(request, 'accounts/pin_setup.html', {
        'form': form,
        'is_reset': is_reset,
        'pin_length': device_pin.PIN_LENGTH,
    })


@login_required
def device_pin_view(request):
    """Change the PIN and manage the browsers remembered for PIN sign-in."""
    user = request.user
    if not device_pin.pin_required() or not device_pin.role_allowed(user):
        messages.error(request, "PIN sign-in is not available for this account.")
        return redirect('accounts:profile')
    if device_pin.needs_pin_setup(user):
        return redirect('accounts:pin_setup')

    current = device_pin.current_device(request)
    if current is not None and current.user_id != user.pk:
        current = None

    form = ChangePinForm(user=user)
    if request.method == 'POST':
        action = request.POST.get('action', 'change')
        if action == 'change':
            form = ChangePinForm(request.POST, user=user)
            if form.is_valid():
                device_pin.set_pin(user, form.cleaned_data['pin'])
                _audit_pin_event(user, PinSecurityAudit.Action.CHANGED, request=request, details={'event': 'changed'})
                resp = redirect('accounts:device_pin')
                # Changing a PIN never creates trust on a previously untrusted browser.
                if request.session.get('device_pin_idle_enabled') and device_pin.current_device(request):
                    device_pin.trust_device(request, user, resp)
                messages.success(request, "Your PIN was changed.")
                return resp
        elif action == 'revoke':
            target = TrustedDevice.objects.filter(user=user, pk=request.POST.get('device_id')).first()
            if target is not None:
                was_current = current is not None and target.pk == current.pk
                target.delete()
                if was_current:
                    request.session['device_pin_idle_enabled'] = False
                messages.success(request, "Device removed. It will ask for your password next time.")
            return redirect('accounts:device_pin')
        elif action == 'revoke_others':
            device_pin.revoke_devices(user, keep=current)
            messages.success(request, "All other devices were removed.")
            return redirect('accounts:device_pin')

    return render(request, 'accounts/device_pin.html', {
        'form': form,
        'devices': user.trusted_devices.all(),
        'current_device': current,
        'pin_length': device_pin.PIN_LENGTH,
    })


def _audit_pin_event(user, action, actor=None, request=None, details=None):
    try:
        PinSecurityAudit.objects.create(
            user=user, actor=actor, action=action,
            details=details or {},
            ip_address=(request.META.get('REMOTE_ADDR') if request else ''),
            user_agent=(request.META.get('HTTP_USER_AGENT','')[:500] if request else ''),
        )
    except Exception:
        logger.exception('Could not write PIN security audit event for user_id=%s', getattr(user, 'pk', None))


def _notify_pin_reset(user, actor=None):
    try:
        owner = actor.business_owner() if actor and hasattr(actor, 'business_owner') else None
        message = (f"SmartPOS - Your device PIN was reset{(' by ' + (actor.get_full_name() or actor.username)) if actor else ''}.\n"
                   "Sign in with your password and create a new PIN.")
        if user.phone_number:
            send_sms(user.phone_number, message, owner=owner)
    except Exception:
        logger.exception('Could not send PIN reset SMS for user_id=%s', getattr(user, 'pk', None))


@admin_required
@require_http_methods(['POST'])
def employee_reset_pin_view(request, pk):
    """Manager resets a staff member's PIN (e.g. they forgot it). A new PIN is forced at next sign-in."""
    employee = get_object_or_404(User, pk=pk, role=User.Role.EMPLOYEE, pharmacy=request.pharmacy)
    device_pin.reset_pin(employee)
    _audit_pin_event(employee, PinSecurityAudit.Action.MANAGER_RESET, actor=request.user, request=request)
    _notify_pin_reset(employee, actor=request.user)
    logger.info('PIN of user_id=%s reset by user_id=%s', employee.pk, request.user.pk)
    messages.success(request, f"PIN of '{employee.username}' was reset. They will create a new PIN the next time they sign in.")
    return redirect('accounts:employee_list')


@manager_required
@require_http_methods(['POST'])
def assistant_reset_pin_view(request, pk):
    """Owner resets a Manager Assistant's PIN."""
    profile = get_object_or_404(ManagerAssistant, pk=pk, manager=request.user.business_owner())
    device_pin.reset_pin(profile.assistant)
    logger.info('PIN of user_id=%s reset by user_id=%s', profile.assistant_id, request.user.pk)
    messages.success(request, f"PIN of '{profile.assistant.username}' was reset. They will create a new PIN the next time they sign in.")
    return redirect('accounts:assistant_list')


@admin_required
@require_http_methods(['POST'])
def employee_release_device_view(request, pk):
    """Manager frees a staff account that is still marked active on another device
    (e.g. a phone that was lost or left without logging out)."""
    employee = get_object_or_404(User, pk=pk, role=User.Role.EMPLOYEE, pharmacy=request.pharmacy)
    if single_device.release_by_manager(employee, actor=request.user, request=request):
        messages.success(request, f"'{employee.username}' was released. They can now sign in on any device; "
                                  "the previous device is signed out.")
    else:
        messages.info(request, f"'{employee.username}' is not active on any device right now.")
    return redirect('accounts:employee_list')


@manager_required
@require_http_methods(['POST'])
def assistant_release_device_view(request, pk):
    """Owner frees a Manager Assistant account that is still active on another device."""
    profile = get_object_or_404(ManagerAssistant, pk=pk, manager=request.user.business_owner())
    if single_device.release_by_manager(profile.assistant, actor=request.user, request=request):
        messages.success(request, f"'{profile.assistant.username}' was released. They can now sign in on any device; "
                                  "the previous device is signed out.")
    else:
        messages.info(request, f"'{profile.assistant.username}' is not active on any device right now.")
    return redirect('accounts:assistant_list')


@login_required
def device_pin_lock_view(request):
    """Lock screen shown after inactivity without logging the user out."""
    user = request.user
    device = device_pin.get_device(request)
    next_url = _safe_idle_next(request)

    # A transient cookie/device-record problem must never turn an idle lock
    # into a logout. Preserve the authenticated session and continue safely.
    if device is None or device.user_id != user.pk:
        device_pin.unlock_session(request)
        request.session['device_pin_idle_enabled'] = False
        request.session.modified = True
        messages.info(request, "Your session is still active. Device PIN lock was temporarily unavailable.")
        return redirect(next_url)

    return render(request, 'accounts/device_pin_lock.html', {
        'pin_length': device_pin.PIN_LENGTH,
        'device': device,
        'next_url': next_url,
    })


@login_required
@require_POST
def device_pin_unlock_view(request):
    """Verify the PIN and unlock an idle session without logging the user out."""
    device = device_pin.get_device(request)
    next_url = _safe_idle_next(request)
    if device is None or device.user_id != request.user.pk:
        # Do not destroy the authenticated session on an idle-lock failure.
        # The browser can continue with the current session; idle protection is
        # disabled for this session until the trusted device becomes available.
        device_pin.unlock_session(request)
        request.session['device_pin_idle_enabled'] = False
        request.session.modified = True
        return JsonResponse({
            'ok': False,
            'session_preserved': True,
            'redirect': next_url,
            'error': 'This device is no longer trusted. Your account was not signed out.',
        }, status=409)
    retry_after = device_pin.check_ip_rate_limit(request)
    if retry_after:
        return JsonResponse({'ok': False, 'rate_limited': True, 'retry_after': retry_after, 'error': f'Too many PIN requests. Try again in about {retry_after} seconds.'}, status=429)
    pin = (request.POST.get('pin') or '').strip()
    if len(pin) != device_pin.PIN_LENGTH or not pin.isdigit():
        return JsonResponse({'ok': False, 'error': f'Enter your {device_pin.PIN_LENGTH}-digit PIN.'}, status=400)
    result = device_pin.verify_pin(device_pin.get_pin(device.user), pin)
    if not result['ok']:
        if result['locked']:
            device_pin.unlock_session(request)
            request.session['device_pin_idle_enabled'] = False
            return JsonResponse({'ok': False, 'fallback': True, 'redirect': reverse('accounts:logout'), 'error': 'Your PIN is locked. Please sign in again with your password.'}, status=403)
        if result['cooldown']:
            return JsonResponse({'ok': False, 'cooldown': True, 'retry_after': result['retry_after'], 'error': f'Please wait {result["retry_after"]} seconds before trying again.'}, status=429)
        left = result['attempts_left']
        return JsonResponse({'ok': False, 'attempts_left': left, 'error': f'Wrong PIN. {left} {"attempt" if left == 1 else "attempts"} left.'}, status=400)
    device_pin.unlock_session(request)
    device_pin.trust_device(request, request.user, response := JsonResponse({'ok': True, 'redirect': next_url}))
    return response


@login_required
def logout_view(request):
    """Sign out, but return to PIN sign-in on this browser when the user has a usable PIN.

    The browser is explicitly the one the user just authenticated on. Refreshing its
    remembered-device cookie here makes PIN the default after logout even if the
    user did not tick the optional remember-device checkbox during an earlier login.
    Password sign-in remains available only through the visible 'Use another account'
    / recovery links. A locked or missing PIN never gets a PIN-only shortcut.
    """
    user = request.user
    response = redirect('accounts:login')
    pin = device_pin.get_pin(user)
    if (
        pin is not None
        and not pin.is_locked
        and device_pin.pin_required()
        and device_pin.role_allowed(user)
        and not user.must_change_password
    ):
        device_pin.trust_device(request, user, response)
    single_device.release_for_request(request, user)  # free the account for its next sign-in
    logout(request)
    return response


@login_required
def change_password_view(request):
    from .forms import StyledPasswordChangeForm as _StyledForm

    if request.method == 'POST':
        wants_json = request.headers.get('x-requested-with') == 'XMLHttpRequest'
        form = _StyledForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            request.user.must_change_password = False
            request.user.save(update_fields=['must_change_password'])
            update_session_auth_hash(request, user)
            # A new password signs the account out of every other remembered browser.
            current_device = device_pin.current_device(request)
            device_pin.revoke_devices(user, keep=current_device if current_device and current_device.user_id == user.pk else None)
            if wants_json:
                return JsonResponse({'ok': True, 'message': 'Your password has been updated successfully.'})
            messages.success(request, "Your password has been updated successfully.")
            return redirect('dashboard:home')
        if wants_json:
            return JsonResponse({'ok': False, 'errors': {k: [str(e) for e in v] for k, v in form.errors.items()}}, status=400)
    else:
        form = _StyledForm(user=request.user)

    return render(request, 'accounts/change_password.html', {
        'form': form,
        'is_forced': request.user.must_change_password,
    })


@login_required
def profile_view(request):
    form = ProfileForm(request.POST or None, request.FILES or None, instance=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, "Your profile has been updated successfully.")
        return redirect('accounts:profile')

    from . import google_auth
    owner = request.user.role == User.Role.ADMIN and not request.user.is_superuser and not request.user.is_staff
    ctx = {'form': form, 'google_is_owner': owner, 'google_linked': bool(request.user.google_sub)}
    if owner and not ctx['google_linked']:
        ctx.update(google_auth.template_context(request))
    return render(request, 'accounts/profile.html', ctx)


def _generate_temp_password(length=10):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))


@admin_required
def employee_list_view(request):
    employees = User.objects.filter(role=User.Role.EMPLOYEE, pharmacy=request.pharmacy).order_by('-created_at')
    page_obj = Paginator(employees, 20).get_page(request.GET.get('page'))
    highlight = request.GET.get('added') or request.GET.get('updated') or ''
    return render(request, 'accounts/employee_list.html', {
        'page_obj': page_obj,
        'highlight_id': int(highlight) if highlight.isdigit() else None,
    })


@admin_required
def employee_create_view(request):
    if request.pharmacy is None:
        messages.warning(request, "You need an active branch before adding employees.")
        return redirect('pharmacies:list')

    try:
        subscription = request.user.business_owner().subscription
    except Exception:
        subscription = None
    if subscription is None:
        messages.error(request, "Your business subscription could not be found. Please contact support.")
        return redirect('accounts:subscription_expired')

    user_limit = subscription.included_users
    active_employees = User.objects.filter(
        role=User.Role.EMPLOYEE,
        pharmacy__in=request.user.accessible_management_pharmacies().all(),
        is_active=True,
    ).count()
    if 1 + active_employees >= user_limit:
        _plan_name = 'Free Plan' if subscription.is_trial else f"{subscription.get_tier_display()} plan"
        messages.warning(request, f"Your {_plan_name} allows up to {user_limit} users including the owner. Upgrade your plan to add more users.")
        return redirect('accounts:employee_list')

    form = EmployeeCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        active_employees = User.objects.filter(
            role=User.Role.EMPLOYEE,
            pharmacy__in=request.user.accessible_management_pharmacies().all(),
            is_active=True,
        ).count()
        if 1 + active_employees >= subscription.included_users:
            messages.warning(request, "Your plan's user limit has been reached. Upgrade the subscription to add another user.")
            return redirect('accounts:employee_list')
        temp_password = _generate_temp_password()
        employee = form.save(commit=False)
        employee.role = User.Role.EMPLOYEE
        employee.pharmacy = request.pharmacy
        employee.must_change_password = True
        employee.set_password(temp_password)
        employee.save()

        # Every employee starts with an explicit 7-day policy. Weekdays
        # default to all three meals; Saturday/Sunday default to lunch only.
        # The owner can change any day immediately from Salary & Meals.
        for weekday in range(7):
            WeeklyMealSchedule.objects.get_or_create(
                employee=employee,
                weekday=weekday,
                defaults={
                    'breakfast': weekday < 5,
                    'lunch': True,
                    'dinner': weekday < 5,
                },
            )

        sent = send_employee_credentials_sms(employee.phone_number, employee.username, temp_password, owner=request.user.business_owner())
        if sent:
            messages.success(
                request,
                f"Employee '{employee.username}' created. Login details were sent via SMS to {employee.phone_number}."
            )
        else:
            messages.warning(
                request,
                f"Employee '{employee.username}' created, but the SMS could not be sent. "
                f"Temporary password: {temp_password} (share this securely, then ask them to change it)."
            )
        return redirect(f"{reverse('accounts:employee_list')}?added={employee.pk}")

    return render(request, 'accounts/employee_form.html', {'form': form, 'mode': 'create'})


@admin_required
def employee_edit_view(request, pk):
    employee = get_object_or_404(User, pk=pk, role=User.Role.EMPLOYEE, pharmacy=request.pharmacy)
    form = EmployeeEditForm(request.POST or None, instance=employee)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f"Details for '{employee.get_full_name() or employee.username}' were updated.")
        return redirect(f"{reverse('accounts:employee_list')}?updated={employee.pk}")
    return render(request, 'accounts/employee_form.html', {'form': form, 'mode': 'edit', 'employee': employee})


@admin_required
@require_http_methods(["POST"])
def employee_toggle_active_view(request, pk):
    employee = get_object_or_404(User, pk=pk, role=User.Role.EMPLOYEE, pharmacy=request.pharmacy)
    employee.is_active = not employee.is_active
    employee.save(update_fields=['is_active'])
    state = "activated" if employee.is_active else "deactivated"
    messages.success(request, f"Employee '{employee.username}' has been {state}.")
    return redirect('accounts:employee_list')


@admin_required
def employee_compensation_view(request, pk):
    employee = get_object_or_404(User, pk=pk, role=User.Role.EMPLOYEE, pharmacy=request.pharmacy)
    form = EmployeeCompensationForm(request.POST or None, instance=employee)
    schedule_form = WeeklyMealScheduleForm(request.POST or None, employee=employee)
    from .forms import AttendanceMethodsForm
    attendance_form = AttendanceMethodsForm(request.POST if 'save_attendance' in request.POST else None, employee=employee)
    if request.method == 'POST' and 'save_attendance' in request.POST:
        if attendance_form.is_valid():
            employee.attendance_methods = ','.join(m for m in ('GPS', 'QR', 'MANUAL') if m in attendance_form.cleaned_data['methods'])
            employee.save(update_fields=['attendance_methods'])
            messages.success(request, 'Attendance methods updated. The employee will only see the methods you selected.')
            return redirect('accounts:employee_compensation', pk=employee.pk)
    elif request.method == 'POST' and 'save_compensation' in request.POST:
        if form.is_valid():
            form.save()
            messages.success(request, f"Compensation details updated for '{employee.get_full_name() or employee.username}'.")
            return redirect('accounts:employee_compensation', pk=employee.pk)
    elif request.method == 'POST' and 'save_schedule' in request.POST:
        if schedule_form.is_valid():
            for day in range(7):
                WeeklyMealSchedule.objects.update_or_create(
                    employee=employee, weekday=day,
                    defaults={
                        'breakfast': schedule_form.cleaned_data[f'day_{day}_breakfast'],
                        'lunch': schedule_form.cleaned_data[f'day_{day}_lunch'],
                        'dinner': schedule_form.cleaned_data[f'day_{day}_dinner'],
                    },
                )
            messages.success(request, 'Weekly meal policy saved. You can still override any specific date below.')
            return redirect('accounts:employee_compensation', pk=employee.pk)

    # choices are (integer, label); expose only the human-readable day name to the template.
    schedule_days = [
        {'name': label, 'breakfast': schedule_form[f'day_{i}_breakfast'], 'lunch': schedule_form[f'day_{i}_lunch'], 'dinner': schedule_form[f'day_{i}_dinner']}
        for i, (_value, label) in enumerate(WeeklyMealSchedule._meta.get_field('weekday').choices)
    ]
    return render(request, 'accounts/employee_compensation_form.html', {
        'form': form, 'schedule_form': schedule_form, 'attendance_form': attendance_form, 'schedule_days': schedule_days, 'employee': employee,
        'adjustments': employee.meal_adjustments.select_related('created_by')[:20],
    })


@admin_required
def meal_adjustment_create_view(request, pk):
    employee = get_object_or_404(User, pk=pk, role=User.Role.EMPLOYEE, pharmacy=request.pharmacy)
    form = MealAdjustmentForm(request.POST or None, employee=employee)
    if request.method == 'POST' and form.is_valid():
        adjustment_date = form.cleaned_data['date']
        adjustment = MealAdjustment.objects.filter(employee=employee, date=adjustment_date).first()
        if adjustment is None:
            adjustment = form.save(commit=False)
            adjustment.employee = employee
            adjustment.created_by = request.user
        else:
            for field in ('breakfast', 'lunch', 'dinner', 'reason'):
                setattr(adjustment, field, form.cleaned_data[field])
        adjustment.save()
        messages.success(request, f"Meal entitlement for {adjustment.date:%d %b %Y} was updated for '{employee.get_full_name() or employee.username}'.")
        return redirect('accounts:employee_compensation', pk=employee.pk)
    return render(request, 'accounts/meal_adjustment_form.html', {'form': form, 'employee': employee})


@admin_required
def meal_adjustment_delete_view(request, pk, adjustment_id):
    employee = get_object_or_404(User, pk=pk, role=User.Role.EMPLOYEE, pharmacy=request.pharmacy)
    adjustment = get_object_or_404(MealAdjustment, pk=adjustment_id, employee=employee)
    if request.method == 'POST':
        adjustment.delete()
        messages.success(request, 'The date override was removed. The normal weekly meal policy will apply again.')
        return redirect('accounts:employee_compensation', pk=employee.pk)
    return render(request, 'accounts/meal_adjustment_delete.html', {'employee': employee, 'adjustment': adjustment})



@admin_required
def meal_timing_settings_view(request):
    if not getattr(request, 'pharmacy', None):
        return redirect('pharmacies:list')
    settings_obj, _ = MealTimingSettings.objects.get_or_create(pharmacy=request.pharmacy)
    form = MealTimingSettingsForm(request.POST or None, instance=settings_obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Meal cut-off times updated. Salary remains completely separate from meals.')
        return redirect('accounts:meal_timing_settings')
    return render(request, 'accounts/meal_timing_settings.html', {'form': form, 'settings_obj': settings_obj})


@login_required
@require_http_methods(['POST'])
def meal_workday_start_view(request):
    if request.user.is_employee_role():
        messages.warning(request, 'Please use Verify & Start Workday so your workplace attendance can be verified.')
    return redirect('dashboard:home')


@login_required
@require_http_methods(['POST'])
def meal_workday_close_view(request):
    if not request.user.is_employee_role():
        return redirect('dashboard:home')
    today=timezone.localtime(timezone.now()).date()
    attendance=AttendanceRecord.objects.filter(employee=request.user,date=today).first()
    if not attendance or not attendance.is_verified:
        messages.error(request,'A verified attendance record is required before closing the workday.')
        return redirect('dashboard:home')
    from .meal_services import close_workday
    try:
        close_workday(request.user, timezone.now())
        attendance.check_out_at=timezone.now(); attendance.status=AttendanceRecord.Status.CLOSED; attendance.save(update_fields=['check_out_at','status','updated_at'])
        from .attendance_services import queue_owner_attendance_sms
        queue_owner_attendance_sms('OUT', attendance)
        messages.success(request, 'Workday closed and attendance recorded. If this was accidental, your manager can reopen today’s workday from Meals & Attendance.')
    except MealWorkday.DoesNotExist:
        messages.error(request, 'Start your verified workday before closing it.')
    return redirect('dashboard:home')


WORKDAY_REOPEN_REASONS = {
    'MISTAKE': 'Closed by mistake',
    'LATE_CUSTOMER': 'Customer arrived after closing',
    'MISSED_SALES': 'Sales still need to be recorded',
    'OTHER': 'Other reason',
}
WORKDAY_REOPEN_MAX_REQUESTS_PER_DAY = 2


def _notify_workday_reopen(recipients, title, message, level, link_url, pharmacy, dedupe_key=''):
    """Best-effort in-app notification. A notification failure must never block the workflow."""
    try:
        from notifications.utils import notify_many
        notify_many(recipients, title, message=message, category='SYSTEM', level=level,
                    link_url=link_url, pharmacy=pharmacy, dedupe_key=dedupe_key)
    except Exception:
        logging.getLogger(__name__).exception('Workday reopen notification failed')


@admin_required
@require_http_methods(['POST'])
def meal_workday_reopen_view(request, pk):
    employee = get_object_or_404(User, pk=pk, role=User.Role.EMPLOYEE, pharmacy=request.pharmacy)
    from .meal_services import reopen_workday
    try:
        reopen_workday(employee, request.user, request.POST.get('reason', ''))
        _notify_workday_reopen(
            [employee], 'Your workday was reopened',
            f"{request.user.get_full_name() or request.user.username} reopened your workday. You can continue selling.",
            'SUCCESS', reverse('dashboard:home'), employee.pharmacy)
        messages.success(request, f"{employee.get_full_name() or employee.username}’s workday was reopened. They can continue working and request an eligible meal rescan if needed.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect('accounts:meal_issues')


@login_required
@require_http_methods(['POST'])
def workday_reopen_request_view(request):
    """Let an employee ask their own branch manager to reopen today's workday."""
    if not request.user.is_employee_role() or not request.user.pharmacy_id:
        messages.error(request, 'Only assigned employees can request a workday reopening.')
        return redirect('dashboard:home')
    today = timezone.localdate()
    workday = MealWorkday.objects.filter(employee=request.user, date=today).first()
    if not workday or workday.closed_at is None:
        messages.error(request, 'There is no closed workday to reopen today.')
        return redirect('dashboard:home')
    code = (request.POST.get('reason_code') or 'OTHER').strip().upper()
    if code not in WORKDAY_REOPEN_REASONS:
        code = 'OTHER'
    details = (request.POST.get('reason') or '').strip()
    if code == 'OTHER' and len(details) < 5:
        messages.error(request, 'Please explain why the workday needs to be reopened (at least 5 characters).')
        return redirect('dashboard:home')
    reason = WORKDAY_REOPEN_REASONS[code] + (f': {details}' if details else '')
    todays = WorkdayReopenRequest.objects.filter(employee=request.user, work_date=today)
    if todays.filter(status=WorkdayReopenRequest.Status.PENDING).exists():
        messages.info(request, 'Your reopening request is already waiting for the Boss/manager.')
        return redirect('dashboard:home')
    if todays.count() >= WORKDAY_REOPEN_MAX_REQUESTS_PER_DAY:
        messages.error(request, 'You have reached today’s limit for reopening requests. Please speak to your Boss/manager directly.')
        return redirect('dashboard:home')
    req = WorkdayReopenRequest.objects.create(employee=request.user, pharmacy=request.user.pharmacy, work_date=today, reason=reason[:500])
    from notifications.utils import management_recipients
    name = request.user.get_full_name() or request.user.username
    _notify_workday_reopen(
        management_recipients(request.user.pharmacy),
        f'Workday reopening request — {name}',
        f'{name} closed the workday and asks to reopen it. Reason: {reason[:200]}',
        'WARNING', reverse('accounts:meal_issues'), request.user.pharmacy,
        dedupe_key=f'workday-reopen-request:{req.pk}')
    from .attendance_services import queue_owner_attendance_sms
    queue_owner_attendance_sms('REO', req)  # v97.18: SMS the owner (opt-in modes) - a reopen needs a decision
    messages.success(request, 'Request sent to your branch Boss/manager. You will be notified when they decide. Sales stay locked until the Boss approves.')
    return redirect('dashboard:home')


@admin_required
@require_http_methods(['POST'])
def workday_reopen_request_decide_view(request, pk, decision):
    req = get_object_or_404(WorkdayReopenRequest.objects.select_related('employee', 'pharmacy'), pk=pk, pharmacy=request.pharmacy, status=WorkdayReopenRequest.Status.PENDING)
    note = (request.POST.get('decision_note') or '').strip()
    if decision not in ('approve', 'reject'):
        messages.error(request, 'Unknown decision.')
        return redirect('accounts:meal_issues')
    if decision == 'approve':
        from .meal_services import reopen_workday
        try:
            reopen_workday(req.employee, request.user, note or req.reason)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('accounts:meal_issues')
        req.status = WorkdayReopenRequest.Status.APPROVED
        _notify_workday_reopen(
            [req.employee], 'Workday reopen request approved',
            'Your Boss/manager reopened your workday. You can continue selling.' + (f' Note: {note[:200]}' if note else ''),
            'SUCCESS', reverse('dashboard:home'), req.pharmacy, dedupe_key=f'workday-reopen-decision:{req.pk}')
        messages.success(request, f'Workday reopened for {req.employee.get_full_name() or req.employee.username}. They can continue selling.')
    else:
        req.status = WorkdayReopenRequest.Status.REJECTED
        _notify_workday_reopen(
            [req.employee], 'Workday reopen request declined',
            'Your Boss/manager declined the request.' + (f' Reason: {note[:200]}' if note else ' Please speak to them directly.'),
            'DANGER', reverse('dashboard:home'), req.pharmacy, dedupe_key=f'workday-reopen-decision:{req.pk}')
        messages.warning(request, 'Workday reopening request rejected.')
    req.decided_by = request.user
    req.decided_at = timezone.now()
    req.decision_note = note[:500]
    req.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
    return redirect('accounts:meal_issues')


@login_required
def meal_history_view(request):
    """Employee-scoped meal ledger with matching filters and CSV export."""
    if request.user.is_employee_role():
        employee = request.user
    else:
        employee = get_object_or_404(
            User, pk=request.GET.get('employee'), role=User.Role.EMPLOYEE,
            pharmacy=request.pharmacy,
        )

    qs = employee.meal_ledger.all().select_related('pharmacy').order_by('-date', 'cutoff_at')
    status = request.GET.get('status', '').strip()
    meal_type = request.GET.get('meal_type', '').strip()
    date_from_raw = request.GET.get('from', '').strip()
    date_to_raw = request.GET.get('to', '').strip()
    date_from = date_to = None
    try:
        date_from = datetime.strptime(date_from_raw, '%Y-%m-%d').date() if date_from_raw else None
    except ValueError:
        date_from_raw = ''
    try:
        date_to = datetime.strptime(date_to_raw, '%Y-%m-%d').date() if date_to_raw else None
    except ValueError:
        date_to_raw = ''
    if date_from and date_to and date_from > date_to:
        date_from = date_to = None
        date_from_raw = date_to_raw = ''
        messages.warning(request, 'Start date must be on or before end date. The date filter was cleared.')
    if status in dict(MealLedger.Status.choices):
        qs = qs.filter(status=status)
    else:
        status = ''
    if meal_type in dict(MealLedger.MealType.choices):
        qs = qs.filter(meal_type=meal_type)
    else:
        meal_type = ''
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)

    # Settle past-cutoff PENDING meals now, so history never shows an unexplained "Pending".
    from . import meal_services
    now = timezone.now()
    try:
        meal_services.settle_overdue_meals_for(employee, now=now)
    except Exception:
        logging.getLogger(__name__).exception('Meal history settlement failed for %s', employee.pk)

    # One list of display rows used by the page, CSV and PDF so all three always agree.
    # Each row gets an explicit label + explanation; missed/absent meals are marked "Not paid".
    def _row(date, meal_label, cutoff_at, amount, status_key, label, claim, reason, note=''):
        return {'date': date, 'meal': meal_label, 'cutoff_at': cutoff_at, 'amount': amount,
                'status_key': status_key, 'status_label': label, 'claim': claim,
                'reason': reason, 'manual_note': note}

    rows = []
    for item in qs:
        claim = ''
        if item.claimed_at:
            claim = f"{item.get_claim_method_display()} · {timezone.localtime(item.claimed_at):%H:%M}"
        if item.status == MealLedger.Status.EARNED:
            key, label, reason = 'EARNED', 'Earned', item.reason or 'Funded by branch sales.'
        elif item.status == MealLedger.Status.DEBT:
            key, label, reason = 'DEBT', 'Meal debt', item.reason or 'No completed branch sale recorded yet.'
        elif item.status == MealLedger.Status.MISSED:
            key, label = 'NOT_PAID', 'Not paid'
            reason = item.reason or 'Meal was not claimed. Not paid.'
        elif item.status == MealLedger.Status.WAIVED:
            key, label, reason = 'WAIVED', 'Waived', item.reason or 'Waived by manager.'
        elif item.claimed_at:
            key, label = 'CLAIMED', 'Claimed'
            reason = item.reason or 'Claimed. Waiting for branch sales to fund it.'
        elif item.cutoff_at and item.cutoff_at > now:
            key, label = 'PENDING', 'Pending'
            reason = f"Open until {timezone.localtime(item.cutoff_at):%H:%M}. Scan the branch QR to claim."
        else:
            key, label = 'REVIEW', 'Awaiting review'
            reason = item.reason or 'Past cut-off. Waiting for a manager decision.'
        rows.append(_row(item.date, item.get_meal_type_display(), item.cutoff_at, item.amount,
                         key, label, claim, reason, item.manual_note))

    # Days the employee did not come to work have no ledger row: show them as "Not paid".
    include_absent = status in ('', MealLedger.Status.MISSED)
    absent_total = 0
    if include_absent:
        labels = dict(MealLedger.MealType.choices)
        for day, meal, amount, cutoff in meal_services.absent_meal_rows(employee, date_from, date_to, now=now):
            if meal_type and meal != meal_type:
                continue
            rows.append(_row(day, labels.get(meal, meal), cutoff, amount, 'NOT_PAID', 'Not paid', '',
                             meal_services.ABSENT_REASON))
    rows.sort(key=lambda r: r['cutoff_at'])
    rows.sort(key=lambda r: r['date'], reverse=True)  # newest day first, meals in time order

    total_not_paid = sum((r['amount'] for r in rows if r['status_key'] == 'NOT_PAID'), 0)

    # CSV and PDF exports use precisely the same filters as the visible history and are
    # not limited to the current pagination page.
    def _export_cells(r):
        return [
            employee.get_full_name() or employee.username, employee.username,
            employee.pharmacy.name if employee.pharmacy_id else '', r['date'].isoformat(),
            r['meal'], timezone.localtime(r['cutoff_at']).strftime('%H:%M') if r['cutoff_at'] else '',
            str(r['amount']), r['status_label'], r['claim'],
            (r['reason'] + (f" ({r['manual_note']})" if r['manual_note'] else '')),
        ]

    export = request.GET.get('export')
    # Employee downloads are PDF-only. Convert old/bookmarked CSV URLs to the same
    # filtered PDF instead of exposing a CSV export from this page.
    if export == 'csv' and request.user.is_employee_role():
        params = request.GET.copy()
        params['export'] = 'pdf'
        return redirect(f'{request.path}?{params.urlencode()}')
    if export == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="meal-history-{employee.username}.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow(['Employee', 'Username', 'Branch', 'Business date', 'Meal', 'Cut-off', 'Amount (TZS)', 'Status', 'Claim', 'Reason / audit note'])
        for r in rows:
            writer.writerow(_export_cells(r))
        return response

    from django.db.models import Sum
    earned_qs = qs.filter(status=MealLedger.Status.EARNED)
    debt_qs = qs.filter(status=MealLedger.Status.DEBT)
    total_earned = earned_qs.aggregate(v=Sum('amount'))['v'] or 0
    total_debt = debt_qs.aggregate(v=Sum('amount'))['v'] or 0

    if export == 'pdf':
        from xml.sax.saxutils import escape as _esc
        from reportlab.lib.pagesizes import A4, landscape
        from core.pdf_utils import generate_pdf_report
        name = employee.get_full_name() or employee.username
        period = (f"{date_from:%d %b %Y} — {date_to:%d %b %Y}" if date_from and date_to
                  else f"From {date_from:%d %b %Y}" if date_from
                  else f"Up to {date_to:%d %b %Y}" if date_to else 'All dates')
        pdf_rows = [[
            f"{r['date']:%d %b %Y}", _esc(r['meal']),
            timezone.localtime(r['cutoff_at']).strftime('%H:%M') if r['cutoff_at'] else '-',
            f"{r['amount']:,.0f}", _esc(r['status_label']), _esc(r['claim'] or '-'),
            _esc(r['reason'] + (f" ({r['manual_note']})" if r['manual_note'] else '')),
        ] for r in rows]
        summary = [
            ('Employee', _esc(name)),
            ('Branch', _esc(employee.pharmacy.name if employee.pharmacy_id else '-')),
            ('Period', period),
            ('Earned meals', f"TZS {total_earned:,.0f}"),
            ('Meal debt', f"TZS {total_debt:,.0f}"),
            ('Not paid (missed / absent)', f"TZS {total_not_paid:,.0f}"),
        ]
        try:
            return generate_pdf_report(
                title='Meal History',
                subtitle=f"{_esc(name)} — {period}",
                headers=['Date', 'Meal', 'Cut-off', 'Amount (TZS)', 'Status', 'Claim', 'Reason / audit note'],
                rows=pdf_rows, summary=summary,
                filename=f"meal-history-{employee.username}-{timezone.localdate()}.pdf",
                column_widths=[24, 26, 18, 24, 26, 30, 70], pagesize=landscape(A4),
            )
        except Exception:
            logging.getLogger(__name__).exception('Meal history PDF failed')
            messages.error(request, 'Could not create the PDF. Please try again in a moment.')
            return redirect('accounts:meal_history')

    today_qs = employee.meal_ledger.filter(date=timezone.localdate())
    page_obj = Paginator(rows, 20).get_page(request.GET.get('page'))
    return render(request, 'accounts/meal_history.html', {
        'employee': employee, 'page_obj': page_obj, 'status': status,
        'meal_type': meal_type, 'meal_type_choices': MealLedger.MealType.choices,
        'date_from': date_from_raw, 'date_to': date_to_raw,
        'status_choices': MealLedger.Status.choices,
        'total_earned': total_earned, 'total_debt': total_debt, 'total_not_paid': total_not_paid,
        'salary_set': employee.monthly_salary is not None and employee.monthly_salary > 0,
        'today_earned': today_qs.filter(status=MealLedger.Status.EARNED).aggregate(v=Sum('amount'))['v'] or 0,
        'today_debt': today_qs.filter(status=MealLedger.Status.DEBT).aggregate(v=Sum('amount'))['v'] or 0,
        'today_awaiting': today_qs.filter(status=MealLedger.Status.PENDING, claimed_at__isnull=False).aggregate(v=Sum('amount'))['v'] or 0,
    })

def forgot_password_view(request):
    """Phone -> SMS OTP. Works as a normal page (no-JS fallback) and as the
    JSON endpoint behind the Forgot Password dialog on the login page."""
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    form = ForgotPasswordForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        phone_number = form.cleaned_data['phone_number']
        try:
            user = User.objects.get(phone_number=phone_number)
        except User.DoesNotExist:
            if is_ajax:
                return JsonResponse({'ok': False, 'error': "No account found with that phone number."}, status=404)
            messages.error(request, "No account found with that phone number.")
            return render(request, 'accounts/forgot_password.html', {'form': form})

        otp = OTP.objects.create(user=user)
        sent = send_otp_sms(user.phone_number, otp.code)
        if not sent:
            messages.warning(request, f"Could not send SMS. For testing, your OTP code is: {otp.code}")
        request.session['reset_user_id'] = user.id
        messages.success(request, "An OTP code has been sent to your phone number.")
        if is_ajax:
            return JsonResponse({'ok': True, 'redirect': reverse('accounts:verify_otp')})
        return redirect('accounts:verify_otp')

    if request.method == 'POST' and is_ajax:
        error = next(iter(form.errors.get('phone_number', [])), "Please enter a valid phone number.")
        return JsonResponse({'ok': False, 'error': error}, status=400)

    return render(request, 'accounts/forgot_password.html', {'form': form})


def verify_otp_view(request):
    user_id = request.session.get('reset_user_id')
    if not user_id:
        messages.error(request, "Please start the password reset process again.")
        return redirect('accounts:forgot_password')

    form = OTPVerifyForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        code = form.cleaned_data['code']
        otp = OTP.objects.filter(user_id=user_id, code=code).order_by('-created_at').first()
        if otp and otp.is_valid():
            otp.is_used = True
            otp.save(update_fields=['is_used'])
            request.session['otp_verified_user_id'] = user_id
            return redirect('accounts:reset_password')
        messages.error(request, "Invalid or expired OTP code. Please try again.")

    reset_user = User.objects.filter(pk=user_id).only('phone_number').first()
    phone = (reset_user.phone_number or '') if reset_user else ''
    masked_phone = f"{phone[:4]}\u2022\u2022\u2022\u2022{phone[-3:]}" if len(phone) >= 8 else ''
    return render(request, 'accounts/verify_otp.html', {'form': form, 'masked_phone': masked_phone})


def reset_password_view(request):
    user_id = request.session.get('otp_verified_user_id')
    if not user_id:
        messages.error(request, "Please verify your OTP code first.")
        return redirect('accounts:forgot_password')

    user = get_object_or_404(User, pk=user_id)
    form = StyledSetPasswordForm(user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        user.must_change_password = False
        user.save(update_fields=['must_change_password'])
        device_pin.reset_pin(user)  # account recovery: forget the PIN and every device; a new PIN is forced
        _audit_pin_event(user, PinSecurityAudit.Action.ACCOUNT_RECOVERY, request=request)
        _notify_pin_reset(user)
        single_device.release_by_recovery(user, request=request)  # SMS-OTP proved identity: free the account
        for key in ('reset_user_id', 'otp_verified_user_id'):
            request.session.pop(key, None)
        messages.success(request, "Password reset successfully. You can now log in.")
        return redirect('accounts:login')

    return render(request, 'accounts/reset_password.html', {'form': form})


@login_required
@require_http_methods(['POST'])
def attendance_checkin_view(request):
    if not request.user.is_employee_role(): return redirect('dashboard:home')
    from .attendance_services import start_verified_workday
    try:
        a,_=start_verified_workday(request.user,request.POST.get('latitude'),request.POST.get('longitude'),request.POST.get('accuracy'),request)
        messages.success(request,f'Attendance verified. You are {a.check_in_distance_m:.0f}m from your assigned branch.')
    except ValueError as exc: messages.error(request,str(exc))
    return redirect('dashboard:home')

@login_required
@require_http_methods(['POST'])
def attendance_presence_check_view(request):
    if not request.user.is_employee_role(): return redirect('dashboard:home')
    today=timezone.localtime(timezone.now()).date(); a=AttendanceRecord.objects.filter(employee=request.user,date=today,status__in=[AttendanceRecord.Status.VERIFIED, AttendanceRecord.Status.REVIEW]).first()
    if not a: messages.error(request,'A verified attendance session is required before a presence check.'); return redirect('dashboard:home')
    from .attendance_services import record_presence_check
    try:
        event=record_presence_check(a,request.POST.get('latitude'),request.POST.get('longitude'),request.POST.get('accuracy'),request)
        messages.success(request,f'Presence verified at {event.distance_m:.0f}m from your branch.') if event.result==AttendanceVerification.Result.VERIFIED else messages.warning(request,event.note or 'You are outside the workplace boundary; manager review has been triggered.')
    except ValueError as exc: messages.error(request,str(exc))
    return redirect('dashboard:home')

@login_required
@require_http_methods(['POST'])
def attendance_presence_ping_view(request):
    if not request.user.is_employee_role(): return JsonResponse({'ok':False,'message':'Not available for this account.'}, status=403)
    today=timezone.localtime(timezone.now()).date()
    a=AttendanceRecord.objects.filter(employee=request.user,date=today,status__in=[AttendanceRecord.Status.VERIFIED,AttendanceRecord.Status.REVIEW]).first()
    if not a: return JsonResponse({'ok':False,'message':'No active attendance session.'}, status=409)
    from .attendance_services import record_presence_check, mark_presence_unavailable
    try:
        event=record_presence_check(a,request.POST.get('latitude'),request.POST.get('longitude'),request.POST.get('accuracy'),request)
        return JsonResponse({'ok':True,'result':event.result,'distance_m':float(event.distance_m or 0),'accuracy_m':float(event.accuracy_m or 0),'checked_at':timezone.localtime(event.checked_at).isoformat(),'note':event.note,'status':a.presence_status})
    except ValueError as exc:
        mark_presence_unavailable(a,str(exc),request)
        return JsonResponse({'ok':False,'status':a.presence_status,'message':str(exc)}, status=422)

@admin_required
def attendance_presence_status_view(request):
    today=timezone.localtime(timezone.now()).date()
    pharmacy=request.pharmacy
    employees=User.objects.filter(pharmacy=pharmacy,role=User.Role.EMPLOYEE,is_active=True).order_by('first_name','last_name','username')
    records={a.employee_id:a for a in AttendanceRecord.objects.filter(pharmacy=pharmacy,date=today).select_related('employee')}
    rows=[]
    for employee in employees:
        a=records.get(employee.id)
        rows.append({
            'id':employee.id,
            'name':employee.get_full_name() or employee.username,
            'status':a.effective_presence_status if a else 'NOT_CHECKED_IN',
            'label':a.effective_presence_label if a else 'Not checked in',
            'distance_m':float(a.last_presence_distance_m) if a and a.last_presence_distance_m is not None else None,
            'accuracy_m':float(a.last_presence_accuracy_m) if a and a.last_presence_accuracy_m is not None else None,
            'last_presence_at':timezone.localtime(a.last_presence_at).strftime('%d %b, %H:%M') if a and a.last_presence_at else None,
        })
    return JsonResponse({'ok':True,'updated_at':timezone.localtime(timezone.now()).isoformat(),'rows':rows})

@login_required
@require_http_methods(['POST'])
def attendance_exception_view(request):
    if not request.user.is_employee_role(): return redirect('dashboard:home')
    from .attendance_services import request_manual_exception
    try:
        obj=request_manual_exception(request.user,request.POST.get('reason',''),request)
        from notifications.utils import notify_many, management_recipients
        notify_many(management_recipients(request.user.pharmacy),'Attendance exception request',f'{request.user.get_full_name() or request.user.username} requested manual attendance approval for {obj.date:%d %b %Y}.',category='SYSTEM',level='WARNING',link_url='/accounts/attendance/review/',pharmacy=request.user.pharmacy)
        from .attendance_services import queue_owner_attendance_sms
        queue_owner_attendance_sms('EXC', obj, request.user.pharmacy)
        messages.success(request,'Your attendance exception request has been sent to the manager for review.')
    except ValueError as exc: messages.error(request,str(exc))
    return redirect('dashboard:home')

@login_required
def attendance_history_view(request):
    if request.user.is_employee_role(): qs=AttendanceRecord.objects.filter(employee=request.user,pharmacy=request.user.pharmacy).select_related('pharmacy')
    else: qs=AttendanceRecord.objects.filter(pharmacy=request.pharmacy).select_related('employee','pharmacy','reviewed_by')
    qs = qs.order_by('-date', '-id')
    page_obj = Paginator(qs, 25).get_page(request.GET.get('page'))
    return render(request,'accounts/attendance_history.html',{'page_obj':page_obj,'records':page_obj,'is_manager':request.user.is_management_user()})

@admin_required
def attendance_review_view(request):
    qs=AttendanceExceptionRequest.objects.filter(pharmacy=request.pharmacy,status=AttendanceExceptionRequest.Status.PENDING).select_related('employee').order_by('-requested_at')
    page_obj = Paginator(qs, 10).get_page(request.GET.get('page'))
    return render(request,'accounts/attendance_review.html',{'page_obj':page_obj,'requests':page_obj})

@admin_required
@require_http_methods(['POST'])
def attendance_exception_decide_view(request,pk,decision):
    obj=get_object_or_404(AttendanceExceptionRequest.objects.select_related('employee','pharmacy'),pk=pk,pharmacy=request.pharmacy,status=AttendanceExceptionRequest.Status.PENDING)
    from .attendance_services import approve_exception,reject_exception
    try:
        if decision=='approve': approve_exception(obj,request.user,request.POST.get('note','').strip()); messages.success(request,f'Attendance for {obj.employee.get_full_name() or obj.employee.username} was approved.')
        else: reject_exception(obj,request.user,request.POST.get('note','').strip()); messages.info(request,'Attendance exception request rejected.')
    except ValueError as exc: messages.error(request,str(exc))
    return redirect('accounts:attendance_review')

# ---------------------------------------------------------------------------
# Manager Assistant — branch-scoped delegated access
# ---------------------------------------------------------------------------
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from .models import ManagerAssistant, ManagerAssistantAudit


def _manager_subscription(request):
    try:
        return request.user.business_owner().subscription
    except Exception:
        return None


def _active_assistant_count(manager):
    return ManagerAssistant.objects.filter(manager=manager, active=True, assistant__is_active=True).count()



@manager_required
def assistant_list_view(request):
    assistants = (
        ManagerAssistant.objects.filter(manager=request.user.business_owner())
        .select_related('assistant', 'pharmacy')
        .order_by('-active', '-created_at')
    )
    subscription = _manager_subscription(request)
    limit = subscription.assistant_limit if subscription else 0
    active_count = _active_assistant_count(request.user.business_owner())
    return render(request, 'accounts/assistant_list.html', {
        'assistants': assistants,
        'subscription': subscription,
        'assistant_limit': limit,
        'active_assistant_count': active_count,
        'can_add_assistant': bool(subscription and active_count < limit),
        'available_branches': request.user.accessible_management_pharmacies().filter(is_active=True).order_by('name'),
    })


@manager_required
def assistant_create_view(request):
    subscription = _manager_subscription(request)
    if subscription is None:
        messages.error(request, 'An active subscription is required to add a Manager Assistant.')
        return redirect('accounts:assistant_list')

    active_count = _active_assistant_count(request.user.business_owner())
    if active_count >= subscription.assistant_limit:
        messages.info(
            request,
            f'Your {subscription.get_tier_display()} plan allows {subscription.assistant_limit} active Manager Assistant(s). Upgrade your plan to add another assistant.'
        )
        return redirect('accounts:assistant_list')

    branches = request.user.accessible_management_pharmacies().filter(is_active=True).order_by('name')
    if not branches.exists():
        messages.warning(request, 'Create or activate a branch before adding a Manager Assistant.')
        return redirect('pharmacies:list')

    form = ManagerAssistantCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        branch = branches.filter(pk=request.POST.get('pharmacy')).first()
        if branch is None:
            form.add_error(None, 'Select a branch managed by your account.')
        else:
            # Re-check the entitlement inside the transaction so two browser
            # tabs cannot bypass the plan limit at the same time.
            temp_password = _generate_temp_password()
            with transaction.atomic():
                locked_subscription = type(subscription).objects.select_for_update().get(pk=subscription.pk)
                active_count = _active_assistant_count(request.user.business_owner())
                if active_count >= locked_subscription.assistant_limit:
                    messages.info(request, 'The Manager Assistant limit has just been reached. Upgrade your plan to add another assistant.')
                    return redirect('accounts:assistant_list')

                assistant = form.save(commit=False)
                assistant.role = User.Role.ASSISTANT
                assistant.pharmacy = None
                assistant.must_change_password = True
                assistant.is_staff = False
                assistant.is_superuser = False
                assistant.set_password(temp_password)
                assistant.save()
                mode = ManagerAssistant.BranchAccessMode.ALL_MANAGER_BRANCHES
                profile = ManagerAssistant.objects.create(
                    manager=request.user.business_owner(), assistant=assistant, pharmacy=branch,
                    branch_access_mode=mode, permissions=[ManagerAssistant.FULL_ACCESS_PERMISSION],
                    created_by=request.user.business_owner(),
                )
                profile.branches.clear()
                ManagerAssistantAudit.objects.create(
                    profile=profile, actor=request.user, action=ManagerAssistantAudit.Action.CREATED,
                    details={'branch_access_mode': profile.branch_access_mode, 'permissions': profile.permissions,
                             'branches': list(profile.accessible_pharmacies().values_list('id', flat=True))},
                )

            sent = send_employee_credentials_sms(
                assistant.phone_number, assistant.username, temp_password, owner=request.user.business_owner()
            )
            if sent:
                messages.success(request, f"Manager Assistant '{assistant.username}' was created. Login details were sent by SMS.")
            else:
                messages.warning(request, f"Manager Assistant '{assistant.username}' was created, but the SMS could not be sent. Temporary password: {temp_password}")
            return redirect('accounts:assistant_list')

    return render(request, 'accounts/assistant_form.html', {
        'form': form,
        'branches': branches,
        'subscription': subscription,
        'assistant_limit': subscription.assistant_limit,
        'active_assistant_count': active_count,
        'available_branches': branches,
    })


@manager_required
def assistant_permissions_view(request, pk):
    profile = get_object_or_404(
        ManagerAssistant.objects.select_related('assistant', 'pharmacy').prefetch_related('branches'),
        pk=pk, manager=request.user.business_owner(),
    )
    manager_branches = request.user.accessible_management_pharmacies().filter(is_active=True).order_by('name')
    if request.method == 'POST':
        selected = [ManagerAssistant.FULL_ACCESS_PERMISSION]
        mode = ManagerAssistant.BranchAccessMode.ALL_MANAGER_BRANCHES
        old_mode = profile.branch_access_mode
        old_permissions = list(profile.permissions or [])
        old_branches = list(profile.accessible_pharmacies().values_list('id', flat=True))
        profile.permissions = selected
        profile.branch_access_mode = mode
        profile.pharmacy_id = int(request.POST.get('default_pharmacy') or profile.pharmacy_id) if request.POST.get('default_pharmacy', '').isdigit() else profile.pharmacy_id
        if not manager_branches.filter(pk=profile.pharmacy_id).exists():
            profile.pharmacy_id = manager_branches.first().pk if manager_branches.exists() else profile.pharmacy_id
        profile.save(update_fields=['permissions', 'branch_access_mode', 'pharmacy', 'updated_at'])
        selected_ids = [int(v) for v in request.POST.getlist('branches') if v.isdigit()]
        profile.branches.clear()
        new_branches = list(profile.accessible_pharmacies().values_list('id', flat=True))
        if old_permissions != selected:
            ManagerAssistantAudit.objects.create(profile=profile, actor=request.user, action=ManagerAssistantAudit.Action.PERMISSIONS_CHANGED, details={'before': old_permissions, 'after': selected})
        if old_mode != mode or old_branches != new_branches:
            ManagerAssistantAudit.objects.create(profile=profile, actor=request.user, action=ManagerAssistantAudit.Action.BRANCH_SCOPE_CHANGED, details={'before_mode': old_mode, 'after_mode': mode, 'before_branches': old_branches, 'after_branches': new_branches})
        messages.success(request, f"Full operational access and branch scope updated for '{profile.assistant.username}'.")
        return redirect('accounts:assistant_list')
    return render(request, 'accounts/assistant_permissions.html', {
        'profile': profile,
        'permission_choices': ManagerAssistant.PERMISSION_CHOICES,
        'available_branches': manager_branches,
    })


@manager_required
@require_http_methods(['POST'])
def assistant_toggle_active_view(request, pk):
    profile = get_object_or_404(ManagerAssistant, pk=pk, manager=request.user.business_owner())
    if not profile.active:
        subscription = _manager_subscription(request)
        if not subscription:
            messages.error(request, 'An active subscription is required to activate an Assistant.')
            return redirect('accounts:assistant_list')
        if _active_assistant_count(request.user.business_owner()) >= subscription.assistant_limit:
            messages.info(request, f'Your {subscription.get_tier_display()} plan already uses all {subscription.assistant_limit} Assistant seat(s). Upgrade your plan before activating this account.')
            return redirect('accounts:assistant_list')

    profile.active = not profile.active
    profile.assistant.is_active = profile.active
    if not profile.active:
        profile.deactivated_at = timezone.now()
        profile.assistant.save(update_fields=['is_active'])
        profile.save(update_fields=['active', 'deactivated_at', 'updated_at'])
        ManagerAssistantAudit.objects.create(profile=profile, actor=request.user, action=ManagerAssistantAudit.Action.DEACTIVATED)
        messages.success(request, f"Assistant '{profile.assistant.username}' was deactivated. Their history has been preserved.")
    else:
        profile.deactivated_at = None
        profile.assistant.save(update_fields=['is_active'])
        profile.save(update_fields=['active', 'deactivated_at', 'updated_at'])
        ManagerAssistantAudit.objects.create(profile=profile, actor=request.user, action=ManagerAssistantAudit.Action.ACTIVATED)
        messages.success(request, f"Assistant '{profile.assistant.username}' was activated.")
    return redirect('accounts:assistant_list')


# ---- Dynamic branch QR attendance -----------------------------------------
def _branch_qr_payload(request):
    from django.urls import reverse
    import segno
    from .attendance_services import make_branch_qr_token, QR_ROTATE_SECONDS
    if request.pharmacy is None:
        raise ValueError('No active branch is available for this account.')
    token = make_branch_qr_token(request.pharmacy)
    url = request.build_absolute_uri(reverse('accounts:attendance_qr_verify')) + '?t=' + token
    svg = segno.make(url, error='l').svg_inline(scale=10, border=4, dark='#000000', light='#ffffff', omitsize=True)
    return {'svg': svg, 'rotate_seconds': QR_ROTATE_SECONDS}


@admin_required
def attendance_qr_display_view(request):
    display_account = User.objects.filter(
        role=User.Role.BRANCH_DISPLAY,
        pharmacy=request.pharmacy,
    ).first()
    return render(request, 'accounts/attendance_qr_display.html', {
        'display_mode': False,
        'display_account': display_account,
    })


@branch_display_only_required
def attendance_qr_kiosk_view(request):
    return render(request, 'accounts/attendance_qr_kiosk.html', _branch_qr_payload(request))


@branch_display_only_required
def attendance_qr_token_view(request):
    resp = JsonResponse({'ok': True, **_branch_qr_payload(request)})
    resp['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp


@manager_required
def attendance_qr_display_account_view(request):
    """Create, update, rotate, or enable/disable the branch QR display account."""
    branch = request.pharmacy
    if branch is None:
        messages.error(request, 'Select an active branch before managing its attendance display.')
        return redirect('accounts:attendance_qr_display')

    account = User.objects.filter(
        role=User.Role.BRANCH_DISPLAY,
        pharmacy=branch,
    ).first()
    action = request.POST.get('action', 'settings') if request.method == 'POST' else 'settings'

    if request.method == 'POST' and action in {'enable', 'disable', 'toggle'}:
        if account is None:
            messages.info(request, 'Create a display account before changing its access status.')
        else:
            if action == 'toggle':
                account.is_active = not account.is_active
            else:
                account.is_active = action == 'enable'
            account.save(update_fields=['is_active'])
            state = 'enabled' if account.is_active else 'disabled'
            messages.success(request, f'Attendance display access for {branch.name} is now {state}.')
        return redirect('accounts:attendance_qr_display')

    if request.method == 'POST' and action == 'rotate':
        if account is None:
            messages.error(request, 'Create the display account before rotating its password.')
            return redirect('accounts:attendance_qr_display')
        password = _generate_temp_password(16)
        account.set_password(password)
        account.must_change_password = False
        account.save(update_fields=['password', 'must_change_password'])
        return render(request, 'accounts/attendance_display_credentials.html', {
            'branch': branch,
            'display_account': account,
            'new_password': password,
            'action': 'rotated',
        })

    # Create or save customized credentials.
    form = AttendanceDisplayAccountForm(request.POST or None, account=account)
    if request.method == 'POST' and action in {'create', 'save'} and form.is_valid():
        username = form.cleaned_data['username']
        password = form.cleaned_data['password']
        created = account is None
        if created:
            account = User(
                username=username,
                first_name='Attendance',
                last_name='Display',
                role=User.Role.BRANCH_DISPLAY,
                pharmacy=branch,
                phone_number=None,
                is_active=True,
                must_change_password=False,
                is_staff=False,
                is_superuser=False,
            )
            if not password:
                password = _generate_temp_password(16)
        else:
            account.username = username
            if not password:
                # Credentials are only displayed when a password is explicitly changed.
                account.save(update_fields=['username'])
                messages.success(request, 'Display account settings saved successfully.')
                return redirect('accounts:attendance_qr_display')

        account.set_password(password)
        account.is_active = True if created else account.is_active
        account.must_change_password = False
        account.save()
        return render(request, 'accounts/attendance_display_credentials.html', {
            'branch': branch,
            'display_account': account,
            'new_password': password,
            'action': 'created' if created else 'updated',
        })

    if request.method == 'POST' and action in {'create', 'save'}:
        messages.error(request, 'Please correct the highlighted credential fields.')

    return render(request, 'accounts/attendance_qr_display.html', {
        'display_mode': False,
        'display_account': account,
        'credential_form': form,
    })


@login_required
def attendance_qr_scanner_view(request):
    if not request.user.is_employee_role():
        messages.info(request, 'QR attendance scanning is available for employee accounts.')
        return redirect('dashboard:home')
    today_attendance = AttendanceRecord.objects.filter(employee=request.user, date=timezone.localdate()).first()
    meal_scan_ok = bool(today_attendance and today_attendance.is_verified and not today_attendance.check_out_at)
    if 'QR' not in request.user.allowed_attendance_methods and not meal_scan_ok:
        messages.error(request, 'QR attendance is not enabled for your account. Use the attendance method provided by your manager.')
        return redirect('dashboard:home')
    return render(request, 'accounts/attendance_qr_scanner.html', {
        'branch': request.user.pharmacy,
    })


_QR_ACTION_LABELS = {'START': 'Check-in', 'BREAKFAST': 'Breakfast', 'LUNCH': 'Lunch', 'DINNER': 'Dinner', 'CHECKOUT': 'Check-out'}
_QR_MEALS = ('BREAKFAST', 'LUNCH', 'DINNER')


def _build_qr_result(user, action):
    """Plain-string receipt for the post-scan success page (stored in the session)."""
    from .meal_services import meal_windows, _amounts
    local_now = timezone.localtime(timezone.now())
    label = _QR_ACTION_LABELS.get(action, action.title())
    result = {
        'ok': True, 'action': action, 'label': label,
        'employee': user.get_full_name() or user.username,
        'branch': user.pharmacy.name if user.pharmacy_id else '',
        'time': local_now.strftime('%H:%M'),
        'date': local_now.strftime('%A, %d %B %Y'),
        'is_meal': action in _QR_MEALS,
    }
    if action in _QR_MEALS:
        ledger = MealLedger.objects.filter(employee=user, date=local_now.date(), meal_type=action).first()
        start, end, _ = meal_windows(user, local_now.date())[action]
        cutoff = ledger.cutoff_at if ledger else end
        amount = ledger.amount if ledger else _amounts(user)[action]
        result.update({
            'title': f'{label} claimed',
            'subtitle': 'Your meal has been recorded successfully.',
            'amount': f'{amount:,.0f}',
            'window': f'{timezone.localtime(start):%H:%M} - {timezone.localtime(end):%H:%M}',
            'settles': f'{timezone.localtime(cutoff):%H:%M}',
            'note': (f'Your scan is the proof that issues this meal. It is settled automatically after '
                     f'{timezone.localtime(cutoff):%H:%M} once branch sales are confirmed. Once settled, it moves '
                     f'from Today\'s Meal Status to your Meal History.'),
        })
    elif action == 'START':
        result.update({'title': 'Checked in', 'subtitle': 'Your workday has started and attendance is verified.',
                       'note': 'Meal checkpoints open at the times set by your branch. Scan the same live QR to claim each meal.'})
    else:  # CHECKOUT
        result.update({'title': 'Checked out', 'subtitle': 'Your workday is closed.',
                       'note': 'End-of-shift meal accounting is now finalised against the branch sales. Settled meals appear in your Meal History.'})
    return result


@login_required
@require_http_methods(['GET', 'POST'])
def attendance_qr_verify_view(request):
    if not request.user.is_employee_role():
        messages.info(request, 'Branch QR verification is for employee accounts.')
        return redirect('dashboard:home')
    from .attendance_services import (read_branch_qr_token, start_workday_with_qr, _qr_action_for_employee, qr_token_seconds_left,
                                      qr_requires_location, QR_LOCATION_MAX_AGE_SECONDS)
    token = request.POST.get('t') or request.GET.get('t', '')
    needs_gps = qr_requires_location(request.user.pharmacy)
    max_age = QR_LOCATION_MAX_AGE_SECONDS if needs_gps else None

    if request.method == 'POST':
        try:
            location = {
                'latitude': request.POST.get('latitude'),
                'longitude': request.POST.get('longitude'),
                'accuracy': request.POST.get('accuracy'),
            } if needs_gps else None
            action = start_workday_with_qr(request.user, token, request, location)
            request.session['qr_result'] = _build_qr_result(request.user, action)
        except ValueError as exc:
            request.session['qr_result'] = {'ok': False, 'message': str(exc)}
        return redirect('accounts:attendance_qr_result')

    try:
        branch_id = read_branch_qr_token(token, max_age)
        if branch_id != request.user.pharmacy_id:
            raise ValueError('This QR code belongs to a different branch.')
        branch = request.user.pharmacy
        local_now = timezone.localtime(timezone.now())
        today = local_now.date()
        attendance = AttendanceRecord.objects.filter(employee=request.user, date=today).first()
        mode, _label = _qr_action_for_employee(request.user, local_now, attendance)
        wait_message = ''
        if mode == 'WAIT':
            from .meal_services import meal_wait_message
            wait_message = meal_wait_message(request.user, timezone.now(), attendance)
        meal = None
        if mode in _QR_MEALS:
            from .meal_services import meal_windows, _amounts
            start, end, label = meal_windows(request.user, today)[mode]
            meal = {'label': label, 'start': start, 'end': end, 'amount': _amounts(request.user)[mode]}
    except ValueError as exc:
        request.session['qr_result'] = {'ok': False, 'message': str(exc)}
        return redirect('accounts:attendance_qr_result')
    return render(request, 'accounts/attendance_qr_confirm.html', {
        'token': token, 'mode': mode, 'mode_label': _QR_ACTION_LABELS.get(mode, ''), 'wait_message': wait_message,
        'branch': branch, 'meal': meal, 'attendance': attendance, 'now': local_now,
        'employee_name': request.user.get_full_name() or request.user.username,
        'seconds_left': qr_token_seconds_left(token, max_age),
        'needs_gps': needs_gps,
    })


@login_required
@require_http_methods(['POST'])
def attendance_qr_location_check_view(request):
    """Advisory pre-check shown on the scan page: is this device inside the workplace radius?

    Nothing is recorded here and no branch coordinates are returned; the real,
    authoritative check happens again when the scan is confirmed.
    """
    from .attendance_services import verify_location
    if not request.user.is_employee_role():
        return JsonResponse({'ok': False, 'state': 'error', 'message': 'Employee accounts only.'}, status=403)
    try:
        inside, distance, accuracy, note = verify_location(
            request.user, request.POST.get('latitude'), request.POST.get('longitude'), request.POST.get('accuracy'))
    except ValueError as exc:
        return JsonResponse({'ok': False, 'state': 'error', 'message': str(exc)})
    if distance is None:
        return JsonResponse({'ok': False, 'state': 'weak', 'accuracy': round(accuracy), 'message': note})
    allowed = max(30, min(int(request.user.pharmacy.geofence_radius_m or 150), 2000))
    return JsonResponse({
        'ok': bool(inside), 'state': 'inside' if inside else 'outside',
        'distance': round(distance), 'accuracy': round(accuracy), 'allowed': allowed,
    })


@login_required
def attendance_qr_result_view(request):
    """Receipt shown after a scan is confirmed (or refused). One-time: refresh returns to the dashboard."""
    result = request.session.pop('qr_result', None)
    if not result:
        return redirect('dashboard:home')
    return render(request, 'accounts/attendance_qr_result.html', {'r': result})


# ---------------------------------------------------------------------------
# Scan-to-claim meals: employee issue report + manager verification
# ---------------------------------------------------------------------------
@login_required
@require_http_methods(['POST'])
def meal_issue_report_view(request):
    """Employee cannot scan: report it. This never issues the meal; a manager must verify."""
    if not request.user.is_employee_role():
        return redirect('dashboard:home')
    from .meal_services import submit_meal_issue
    from .attendance_services import _ip
    try:
        issue = submit_meal_issue(
            request.user, request.POST.get('meal_type', ''), request.POST.get('reason', ''),
            request.POST.get('details', ''), ip_address=_ip(request), late=request.POST.get('late') == '1',
        )
        if issue.is_late:
            messages.success(request, f'Rescan request {issue.reference} sent to the manager. If approved, the meal is reopened and you scan the branch QR yourself.')
        else:
            messages.success(request, f'Issue {issue.reference} sent to the manager. Your {issue.get_meal_type_display().lower()} stays pending until it is verified.')
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect('dashboard:home')


@admin_required
def meal_issues_view(request):
    from datetime import date as _date
    from .meal_services import branch_meals_today, manual_grant_dates
    today = timezone.localdate()
    try:
        day = _date.fromisoformat(request.GET.get('date', '')) if request.GET.get('date') else today
    except ValueError:
        day = today
    if day > today or day not in manual_grant_dates() + [today]:
        day = today
    counts, rows, grantable = branch_meals_today(request.pharmacy, date_value=day, manager_user=request.user)
    pending = (
        MealIssueReport.objects.filter(pharmacy=request.pharmacy, status=MealIssueReport.Status.PENDING)
        .select_related('employee').order_by('-reported_at')
    )
    recent = (
        MealIssueReport.objects.filter(pharmacy=request.pharmacy).exclude(status=MealIssueReport.Status.PENDING)
        .select_related('employee', 'decided_by').order_by('-decided_at')
    )
    pending_page = Paginator(pending, 10).get_page(request.GET.get('pending_page'))
    # Show the manager each requester's attendance state so a REVIEW case is never a surprise.
    pending_page.object_list = list(pending_page.object_list)
    _att_map = {
        (a.employee_id, a.date): a
        for a in AttendanceRecord.objects.filter(
            pharmacy=request.pharmacy, date__in={x.date for x in pending_page.object_list}, check_in_at__isnull=False)
    }
    for _issue in pending_page.object_list:
        _a = _att_map.get((_issue.employee_id, _issue.date))
        _issue.attendance = _a
        _issue.attendance_in_review = bool(_a and _a.status == AttendanceRecord.Status.REVIEW)
    rows_page = Paginator(rows, 25).get_page(request.GET.get('rows_page'))
    recent_page = Paginator(recent, 20).get_page(request.GET.get('recent_page'))
    closed_workdays = (
        MealWorkday.objects.filter(employee__pharmacy=request.pharmacy, date=today, closed_at__isnull=False)
        .select_related('employee', 'reopened_by').order_by('-closed_at', 'employee__first_name')
    )
    return render(request, 'accounts/meal_issues_review.html', {
        'pending_page': pending_page, 'recent_page': recent_page, 'rows_page': rows_page,
        'pending': pending_page, 'recent': recent_page, 'counts': counts, 'rows': rows_page,
        'today': today, 'day': day, 'is_today': day == today,
        'yesterday': today - timedelta(days=1),
        'grantable': grantable, 'grantable_total': sum(grantable.values()),
        'manual_reasons': MealLedger.ManualReason.choices,
        'meal_choices': MealLedger.MealType.choices,
        'closed_workdays': closed_workdays,
        'branch_requires_location': getattr(request.pharmacy, 'attendance_qr_requires_location', True),
        'workday_reopen_requests': WorkdayReopenRequest.objects.filter(pharmacy=request.pharmacy, status=WorkdayReopenRequest.Status.PENDING).select_related('employee').order_by('requested_at'),
    })


@admin_required
@require_http_methods(['POST'])
def meal_reopen_scan_view(request, pk):
    ledger = get_object_or_404(MealLedger, pk=pk, pharmacy=request.pharmacy)
    from .meal_services import reopen_meal_for_scan
    try:
        reopen_meal_for_scan(ledger, request.user, request.POST.get('reason', ''), request.POST.get('note', ''))
        messages.success(request, f'{ledger.get_meal_type_display()} reopened for {ledger.employee.get_full_name() or ledger.employee.username}. The employee must scan the branch QR now.')
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect(f"{reverse('accounts:meal_issues')}?date={ledger.date.isoformat()}")


@admin_required
@require_http_methods(['POST'])
def meal_manual_grant_view(request, pk):
    """Manager verifies one meal directly (outage recovery). Reason is mandatory."""
    ledger = get_object_or_404(MealLedger, pk=pk, pharmacy=request.pharmacy)
    from .meal_services import manager_grant_meal
    try:
        manager_grant_meal(ledger, request.user, request.POST.get('reason', ''), request.POST.get('note', ''))
        messages.success(request, f'{ledger.get_meal_type_display()} verified for {ledger.employee.get_full_name() or ledger.employee.username}.')
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect(f"{reverse('accounts:meal_issues')}?date={ledger.date.isoformat()}")


@admin_required
@require_http_methods(['POST'])
def meal_manual_grant_missing_view(request):
    """Manager verifies a meal that has no ledger row (employee checked in after the window)."""
    from datetime import date as _date
    from .meal_services import manager_grant_missing_meal
    employee = get_object_or_404(User, pk=request.POST.get('employee'), role=User.Role.EMPLOYEE, pharmacy=request.pharmacy)
    try:
        day = _date.fromisoformat(request.POST.get('date', ''))
        manager_grant_missing_meal(employee, request.POST.get('meal_type', ''), day, request.user,
                                   request.POST.get('reason', ''), request.POST.get('note', ''))
        messages.success(request, f'Meal verified for {employee.get_full_name() or employee.username}.')
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('accounts:meal_issues')
    return redirect(f"{reverse('accounts:meal_issues')}?date={day.isoformat()}")


@admin_required
@require_http_methods(['POST'])
def meal_bulk_grant_view(request):
    """Outage recovery for the whole branch: verify one meal for every unclaimed employee."""
    from datetime import date as _date
    from .meal_services import bulk_grant_meals
    try:
        day = _date.fromisoformat(request.POST.get('date', ''))
        granted, skipped = bulk_grant_meals(
            request.pharmacy, request.user, request.POST.get('meal_type', ''), day,
            request.POST.get('reason', ''), request.POST.get('note', ''),
        )
        messages.success(request, f'{granted} meal(s) verified.' + (f' {skipped} skipped (attendance not verified, or outside the allowed days).' if skipped else ''))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('accounts:meal_issues')
    return redirect(f"{reverse('accounts:meal_issues')}?date={day.isoformat()}")


@admin_required
@require_http_methods(['POST'])
def meal_issue_decide_view(request, pk, decision):
    issue = get_object_or_404(MealIssueReport, pk=pk, pharmacy=request.pharmacy)
    from .meal_services import decide_meal_issue
    action = {'approve': 'reopen', 'reopen': 'reopen', 'credit': 'credit', 'verify': 'verify', 'return': 'return', 'reject': 'reject'}.get(decision, decision)
    try:
        decide_meal_issue(issue, request.user, action, request.POST.get('note', ''), request.POST.get('reason', ''))
        if action == 'reopen':
            messages.success(request, f'{issue.reference} approved. {issue.get_meal_type_display()} was reopened for the employee to scan. No meal was recorded yet.')
        elif action == 'credit':
            messages.success(request, f'{issue.reference} approved. {issue.get_meal_type_display()} was credited to {issue.employee.get_full_name() or issue.employee.username} as if scanned in time, and follows normal meal accounting (earned when sales fund it, otherwise meal debt).')
        elif action == 'verify':
            messages.success(request, f'{issue.reference} verified by you. {issue.get_meal_type_display()} was recorded for {issue.employee.get_full_name() or issue.employee.username}.')
        elif action == 'return':
            messages.info(request, f'{issue.reference} returned to the employee for correction and resubmission.')
        else:
            messages.info(request, f'{issue.reference} rejected. No meal was issued.')
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect('accounts:meal_issues')
