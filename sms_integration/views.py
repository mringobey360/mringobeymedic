from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import os
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from accounts.models import User
from .models import SMSWallet, CustomerSMSCredentials, SMSLog, SMSAnnouncementRecipient
from .forms import CustomerSMSCredentialsForm

@login_required
def sms_usage_view(request):
    """The standalone SMS Centre was retired; balances and packages live in Packages & Services."""
    from django.urls import reverse
    return redirect(reverse('hq:packages_center'))


def _open_recipient_link(recipient):
    """Record the first open (idempotent) and send the visitor on to the campaign's link_url."""
    if not recipient.opened_at:
        # A second click (or a link-preview bot) must not overwrite the first open time.
        SMSAnnouncementRecipient.objects.filter(pk=recipient.pk, opened_at__isnull=True).update(
            opened_at=timezone.now())
    destination = recipient.announcement.link_url
    if not destination:
        # Defensive fallback — should not happen since {link} requires link_url at compose time.
        return redirect('dashboard:home')
    return redirect(destination)


def announcement_open_view(request, token):
    """Legacy tracked link /sms/o/<uuid>/ — kept so SMS already delivered keep working."""
    recipient = get_object_or_404(
        SMSAnnouncementRecipient.objects.select_related('announcement'), access_token=token)
    return _open_recipient_link(recipient)


@csrf_exempt
def africa_talking_delivery_report_view(request):
    """Receive Africa's Talking asynchronous SMS delivery reports.

    The provider sends these reports as POST requests after the initial API
    acceptance. This endpoint is deliberately public because it is called by
    Africa's Talking, but an optional AFRICASTALKING_DLR_TOKEN can be set in
    Railway to require a shared query-string token.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    expected_token = os.environ.get('AFRICASTALKING_DLR_TOKEN', '').strip()
    if expected_token and request.GET.get('token', '') != expected_token:
        return JsonResponse({'success': False, 'error': 'Unauthorized.'}, status=403)

    payload = request.POST
    provider_message_id = (payload.get('id') or payload.get('messageId') or payload.get('message_id') or '').strip()
    status = (payload.get('status') or payload.get('deliveryStatus') or '').strip()
    phone = (payload.get('phoneNumber') or payload.get('number') or payload.get('phone') or '').strip()
    reason = (payload.get('failureReason') or payload.get('errorMessage') or payload.get('description') or '').strip()

    if not provider_message_id and not phone:
        return JsonResponse({'success': False, 'error': 'Missing message id and phone number.'}, status=400)

    logs = SMSLog.objects.all()
    if provider_message_id:
        log = logs.filter(provider_message_id=provider_message_id).order_by('-created_at').first()
    else:
        from accounts.phone_utils import normalize_phone_number
        try:
            normalized = normalize_phone_number(phone)
        except Exception:
            normalized = phone
        log = logs.filter(recipient=normalized, was_sent=True).order_by('-created_at').first()

    if not log:
        return JsonResponse({'success': True, 'matched': False})

    normalized_status = status.upper() or 'UNKNOWN'
    delivered = normalized_status in {'SUCCESS', 'DELIVERED'}
    failed = normalized_status in {'FAILED', 'REJECTED', 'EXPIRED'}
    log.delivery_status = normalized_status
    log.delivery_error = reason if failed else ''
    log.delivery_updated_at = timezone.now()
    log.save(update_fields=['delivery_status', 'delivery_error', 'delivery_updated_at'])
    return JsonResponse({'success': True, 'matched': True, 'delivered': delivered})
