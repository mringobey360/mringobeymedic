"""Platform (HQ) deletion of a customer or staff account, protected by an SMS code.

The code is sent to the *platform administrator's own phone* and is bound to the single account
being deleted, so a platform operator can never wipe an account with one stray click.

  step 1  staff_delete_view (hq/views.py)  -> typed DELETE  -> SMS code  -> verify page
  step 2  account_delete_verify_view       -> correct code  -> everything is erased
"""
import logging
import time

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from accounts import account_deletion as svc
from accounts.models import User

from .views import superadmin_required

logger = logging.getLogger(__name__)
SESSION_KEY = 'hq_delete_confirmed'
MAX_AGE = svc.SESSION_CONFIRM_MAX_AGE_SECONDS


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return (forwarded.split(',')[0].strip() if forwarded else request.META.get('REMOTE_ADDR')) or None


def _back(next_name):
    return redirect('hq:customer_list' if next_name == 'customers' else 'hq:staff_list')


def start_deletion(request, target, next_name=''):
    """Validate, send the code to the operator's phone, and go to the verify page."""
    if getattr(request, 'impersonating', False):
        raise PermissionDenied('Accounts cannot be deleted while viewing a customer workspace.')
    blocker = svc.platform_deletion_blocker(request.user, target)
    if blocker:
        messages.error(request, blocker)
        return _back(next_name)
    try:
        svc.issue_code(request.user, ip=_client_ip(request), target=target)
    except svc.DeletionError as exc:
        messages.error(request, str(exc))
        return _back(next_name)
    request.session[SESSION_KEY] = {'target': target.pk, 'at': time.time(), 'next': next_name}
    messages.success(request, f'A confirmation code was sent to your phone ({svc.mask_phone(request.user.phone_number)}).')
    return redirect('hq:account_delete_verify', pk=target.pk)


@never_cache
@superadmin_required
@require_http_methods(['GET', 'POST'])
def account_delete_verify_view(request, pk):
    target = get_object_or_404(User, pk=pk)
    if getattr(request, 'impersonating', False):
        raise PermissionDenied('Accounts cannot be deleted while viewing a customer workspace.')
    state = request.session.get(SESSION_KEY) or {}
    next_name = state.get('next', '')
    if state.get('target') != target.pk or time.time() - float(state.get('at', 0)) > MAX_AGE:
        request.session.pop(SESSION_KEY, None)
        messages.error(request, 'Please start the account deletion again.')
        return _back(next_name)
    blocker = svc.platform_deletion_blocker(request.user, target)
    if blocker:
        messages.error(request, blocker)
        return _back(next_name)

    error = ''
    label = target.get_full_name() or target.username
    if request.method == 'POST':
        if request.POST.get('action') == 'resend':
            try:
                svc.issue_code(request.user, ip=_client_ip(request), target=target)
                messages.success(request, 'A new code was sent to your phone.')
            except svc.DeletionError as exc:
                messages.error(request, str(exc))
            return redirect('hq:account_delete_verify', pk=target.pk)
        verified = state.get('verified_at')
        retry_ok = (request.POST.get('action') == 'retry' and verified
                    and time.time() - float(verified) <= 600)
        try:
            if not retry_ok:
                svc.verify_code(request.user, request.POST.get('code'), target=target)
                state['verified_at'] = time.time()      # a failed purge can be retried without a new SMS
                request.session[SESSION_KEY] = state
        except svc.DeletionError as exc:
            error = str(exc)
        else:
            try:
                if target.role == User.Role.ADMIN:
                    result = svc.purge_owner_account(target, by_platform=True)
                    done = (f'Owner "{label}" and everything belonging to them was permanently deleted '
                            f'({result["branches"]} branch(es), {result["staff"]} staff account(s)).')
                else:
                    svc.purge_staff_account(target)
                    done = f'Account "{label}" was permanently deleted.'
            except (svc.DeletionError, ProtectedError) as exc:
                logger.exception('HQ deletion blocked for user %s', pk)
                error = (str(exc) if isinstance(exc, svc.DeletionError) else
                         'Some records could not be removed, so nothing was deleted (blocked by: '
                         + ', '.join(svc.protected_model_names(exc)) + ').')
            except Exception:
                logger.exception('HQ deletion failed for user %s', pk)
                error = 'Something went wrong, so nothing was deleted. Please try again or check the logs.'
            else:
                logger.warning('HQ account deletion: admin_id=%s deleted user_id=%s (%s)', request.user.pk, pk, target.role)
                request.session.pop(SESSION_KEY, None)
                messages.success(request, done)
                return _back(next_name)

    return render(request, 'hq/account_delete_verify.html', {
        'target': target, 'target_label': label,
        'masked_phone': svc.mask_phone(request.user.phone_number),
        'error': error, 'code_length': svc.CODE_LENGTH, 'valid_minutes': svc._otp_minutes(),
        'is_owner': target.role == User.Role.ADMIN,
        'cancel_url': 'hq:customer_list' if next_name == 'customers' else 'hq:staff_list',
        'can_retry': bool(state.get('verified_at') and time.time() - float(state['verified_at']) <= 600),
    })
