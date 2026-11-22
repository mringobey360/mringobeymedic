"""System-owner SMS announcements (platform -> customers). Superadmin only."""
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from notifications.models import Notification
from sms_integration import announcements as svc
from sms_integration.models import SMSAnnouncement, SMSAnnouncementRecipient

from .announcement_forms import SMSAnnouncementForm
from .views import superadmin_required

# One-click starting points (Swahili, plain GSM characters => 1 SMS segment where possible).
# 'update', 'billing' and 'notice' include {link} since those messages almost always point
# somewhere (release notes, the payment page, a shared resource) — the admin still has to
# fill in "Link destination" for the message to make sense; 'maintenance' has no natural
# destination so it stays link-free.
QUICK_TEMPLATES = [
    {
        'key': 'update', 'label': 'System update', 'type': 'SYSTEM_UPDATE',
        'title': 'System update',
        'text': 'Habari {name}, SmartPOS imesasishwa na maboresho mapya. Bofya hapa kuona yaliyobadilika: {link}. Asante kwa kutuamini.',
    },
    {
        'key': 'maintenance', 'label': 'Maintenance', 'type': 'MAINTENANCE',
        'title': 'Scheduled maintenance',
        'text': 'Habari {name}, mfumo wa SmartPOS utafanyiwa matengenezo tarehe __ kuanzia saa __ hadi saa __. Huduma inaweza kusitishwa kwa muda mfupi. Samahani kwa usumbufu.',
    },
    {
        'key': 'billing', 'label': 'Subscription reminder', 'type': 'BILLING',
        'title': 'Subscription reminder',
        'text': 'Habari {name}, usajili wako wa SmartPOS unakaribia kuisha. Bofya hapa kulipia haraka: {link}. Asante.',
    },
    {
        'key': 'notice', 'label': 'General notice', 'type': 'ANNOUNCEMENT',
        'title': 'General announcement',
        'text': 'Habari {name}, tuna taarifa muhimu kwako kuhusu SmartPOS: {link}. Kwa msaada wasiliana nasi. Asante.',
    },
]


@superadmin_required
def announcement_list_view(request):
    qs = SMSAnnouncement.objects.select_related('created_by')
    status = request.GET.get('status', '').strip()
    if status in SMSAnnouncement.Status.values:
        qs = qs.filter(status=status)
    page_obj = Paginator(qs, 15).get_page(request.GET.get('page'))
    for item in page_obj:
        svc.release_if_stale(item)
    return render(request, 'hq/announcement_list.html', {
        'page_obj': page_obj, 'current_status': status,
        'status_choices': SMSAnnouncement.Status.choices,
    })


@superadmin_required
@require_http_methods(['GET', 'POST'])
def announcement_compose_view(request):
    ready, reason = svc.provider_ready()
    form = SMSAnnouncementForm(request.POST or None, initial={
        'audience': SMSAnnouncement.Audience.ACTIVE, 'message_type': SMSAnnouncement.MessageType.ANNOUNCEMENT,
        'include_assistants': True, 'also_in_app': True,
    })

    if request.method == 'POST' and form.is_valid():
        cd = form.cleaned_data
        if not ready:
            messages.error(request, reason)
        elif SMSAnnouncement.objects.filter(
            created_by=request.user, title=cd['title'], message=cd['message'],
            created_at__gte=timezone.now() - timedelta(minutes=2),
        ).exists():
            messages.warning(request, 'This exact message was just sent. Open the campaign below to check its progress.')
            return redirect('hq:announcement_list')
        else:
            selected_ids = [c.pk for c in cd['customer_ids']] if cd['audience'] == SMSAnnouncement.Audience.SELECTED else None
            include_assistants = cd['include_assistants'] or cd['audience'] == SMSAnnouncement.Audience.ASSISTANTS
            # Platform announcements reach the shop owners AND (optionally) their active assistant
            # managers: each assistant gets their own SMS on their own phone number plus a bell notification.
            customers, assistant_users = svc.resolve_delivery_users(
                cd['audience'], selected_ids, include_assistants=include_assistants)
            delivery_users = customers + assistant_users
            if not delivery_users:
                messages.error(request, 'No recipients match that audience, so nothing was sent.')
            else:
                base_url = request.build_absolute_uri('/')
                with transaction.atomic():
                    announcement = SMSAnnouncement.objects.create(
                        title=cd['title'], message_type=cd['message_type'], message=cd['message'],
                        audience=cd['audience'], also_in_app=cd['also_in_app'], include_assistants=include_assistants,
                        created_by=request.user,
                        link_url=cd.get('link_url', ''),
                    )
                    svc.build_recipients(announcement, delivery_users, base_url=base_url)
                    transaction.on_commit(lambda pk=announcement.pk: svc.start_sending(pk))

                if cd['also_in_app']:
                    # In-app bell: the customer is already inside the system, so {link} falls back to the
                    # plain destination (announcement.link_url) (same as the SMS, which also carries the link exactly as typed) —
                    # no need to track opens on a notification the customer only sees once logged in.
                    Notification.objects.bulk_create([
                        Notification(
                            recipient=customer, title=announcement.title,
                            message=svc.render_customer_message(announcement, customer, announcement.link_url, include_administrator_footer=True),
                            category=Notification.Category.ANNOUNCEMENT, level=Notification.Level.INFO,
                            link_url=announcement.link_url or reverse('notifications:list'),
                        ) for customer in delivery_users
                    ])
                messages.success(request, f'Announcement queued for {len(customers)} shop owner(s) and {len(assistant_users)} assistant manager(s). Delivery is running now.')
                return redirect('hq:announcement_detail', pk=announcement.pk)

    customers_list = list(svc.customers_queryset().select_related('subscription').order_by('first_name', 'last_name', 'username'))
    assistant_phones = {}
    for owner_id in svc.assistants_for_owners([c.pk for c in customers_list]).exclude(phone_number__isnull=True).exclude(
            phone_number='').values_list('assistant_profile__manager_id', flat=True):
        assistant_phones[owner_id] = assistant_phones.get(owner_id, 0) + 1
    for c in customers_list:
        c.assistant_sms_count = assistant_phones.get(c.pk, 0)
    return render(request, 'hq/announcement_compose.html', {
        'form': form, 'provider_ready': ready, 'provider_reason': reason,
        'audience_counts': svc.audience_counts(),
        'customers': customers_list,
        'selected_ids': set(request.POST.getlist('customer_ids')),
        'quick_templates': QUICK_TEMPLATES,
        'max_chars': svc.MAX_TEMPLATE_CHARS,
    })


@superadmin_required
def announcement_detail_view(request, pk):
    announcement = get_object_or_404(SMSAnnouncement.objects.select_related('created_by'), pk=pk)
    svc.release_if_stale(announcement)

    recipients = announcement.recipients.all()
    status = request.GET.get('status', '').strip()
    q = request.GET.get('q', '').strip()
    if status in SMSAnnouncementRecipient.DeliveryStatus.values:
        recipients = recipients.filter(status=status)
    if q:
        recipients = recipients.filter(Q(display_name__icontains=q) | Q(phone_number__icontains=q))
    page_obj = Paginator(recipients, 25).get_page(request.GET.get('page'))
    return render(request, 'hq/announcement_detail.html', {
        'announcement': announcement, 'page_obj': page_obj,
        'current_status': status, 'q': q,
        'delivery_choices': SMSAnnouncementRecipient.DeliveryStatus.choices,
        'can_retry': (not announcement.is_running) and (announcement.failed_count > 0 or announcement.pending_count > 0),
        # Only older campaigns sent with the tracked redirect have opens; new SMS carry the link directly (no tracking).
        'opened_count': announcement.recipients.filter(opened_at__isnull=False).count() or None,
    })


@superadmin_required
@require_http_methods(['POST'])
def announcement_retry_view(request, pk):
    announcement = get_object_or_404(SMSAnnouncement, pk=pk)
    svc.release_if_stale(announcement)
    ready, reason = svc.provider_ready()
    if not ready:
        messages.error(request, reason)
    elif svc.retry_unsent(announcement):
        messages.success(request, 'Retrying the customers that were not delivered.')
    else:
        messages.info(request, 'Nothing to retry for this campaign.')
    return redirect('hq:announcement_detail', pk=pk)
