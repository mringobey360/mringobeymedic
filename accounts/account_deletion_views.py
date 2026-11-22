"""Views for permanent self-service account deletion (manager accounts only).

Step 1  /accounts/delete-account/         warning + typed confirmation -> SMS code
Step 2  /accounts/delete-account/verify/  enter code -> everything is erased
"""
import logging
import time

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import ProtectedError
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from . import account_deletion as svc

logger = logging.getLogger(__name__)

VERIFIED_KEY = 'acct_delete_code_verified_at'
RETRY_WINDOW_SECONDS = 10 * 60


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return (forwarded.split(',')[0].strip() if forwarded else request.META.get('REMOTE_ADDR')) or None


def _guard(request):
    """Common checks. Returns a redirect response if the person may not continue."""
    if getattr(request, 'impersonating', False):
        # A platform operator viewing a customer's workspace must never be able to erase it.
        raise PermissionDenied('Accounts cannot be deleted while viewing a customer workspace.')
    blocker = svc.deletion_blocker(request.user)
    if blocker:
        messages.error(request, blocker)
        return redirect('accounts:profile')
    return None


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def delete_account_view(request):
    denied = _guard(request)
    if denied:
        return denied

    error = ''
    if request.method == 'POST':
        word = (request.POST.get('confirm_word') or '').strip().upper()
        if request.POST.get('acknowledge') != 'on':
            error = 'Please tick the box to confirm you understand this cannot be undone.'
        elif word != svc.CONFIRM_WORD:
            error = f'Type {svc.CONFIRM_WORD} exactly to continue.'
        else:
            try:
                svc.issue_code(request.user, ip=_client_ip(request))
            except svc.DeletionError as exc:
                error = str(exc)
            else:
                request.session[svc.SESSION_CONFIRM_KEY] = time.time()
                messages.success(request, 'A confirmation code was sent to your phone.')
                return redirect('accounts:delete_account_verify')

    return render(request, 'accounts/delete_account.html', {
        'summary': svc.deletion_summary(request.user),
        'masked_phone': svc.mask_phone(request.user.phone_number),
        'confirm_word': svc.CONFIRM_WORD,
        'error': error,
    })


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def delete_account_verify_view(request):
    denied = _guard(request)
    if denied:
        return denied

    started = request.session.get(svc.SESSION_CONFIRM_KEY)
    if not started or time.time() - float(started) > svc.SESSION_CONFIRM_MAX_AGE_SECONDS:
        request.session.pop(svc.SESSION_CONFIRM_KEY, None)
        messages.error(request, 'Please start the account deletion again.')
        return redirect('accounts:delete_account')

    error = ''
    if request.method == 'POST':
        if request.POST.get('action') == 'resend':
            try:
                svc.issue_code(request.user, ip=_client_ip(request))
                messages.success(request, 'A new code was sent to your phone.')
            except svc.DeletionError as exc:
                messages.error(request, str(exc))
            return redirect('accounts:delete_account_verify')

        verified_at = request.session.get(VERIFIED_KEY)
        retry_ok = (request.POST.get('action') == 'retry' and verified_at
                    and time.time() - float(verified_at) <= RETRY_WINDOW_SECONDS)
        try:
            if not retry_ok:
                svc.verify_code(request.user, request.POST.get('code'))
                request.session[VERIFIED_KEY] = time.time()   # a failed purge can be retried without a new SMS
        except svc.DeletionError as exc:
            error = str(exc)
        else:
            try:
                result = svc.purge_owner_account(request.user)
            except (svc.DeletionError, ProtectedError) as exc:
                logger.exception('Account deletion blocked for user %s', request.user.pk)
                error = (str(exc) if isinstance(exc, svc.DeletionError) else
                         'Some records could not be removed, so nothing was deleted. '
                         'Please try again in a moment (blocked by: '
                         + ', '.join(svc.protected_model_names(exc)) + ').')
            except Exception:
                logger.exception('Account deletion failed for user %s', request.user.pk)
                error = 'Something went wrong, so nothing was deleted. Please try again.'
            else:
                logout(request)
                request.session.flush()
                messages.success(
                    request,
                    f"Your account and all its data were permanently deleted "
                    f"({result['branches']} branch(es), {result['staff']} staff account(s)).")
                return redirect('accounts:login')

    verified_at = request.session.get(VERIFIED_KEY)
    can_retry = bool(verified_at and time.time() - float(verified_at) <= RETRY_WINDOW_SECONDS)

    return render(request, 'accounts/delete_account_verify.html', {
        'masked_phone': svc.mask_phone(request.user.phone_number),
        'error': error,
        'code_length': svc.CODE_LENGTH,
        'valid_minutes': svc._otp_minutes(),
        'can_retry': can_retry,
    })
