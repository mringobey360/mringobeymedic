"""Prospects (business owners not yet on SmartPOS): collect business phone numbers and send them official SmartPOS invitations. Superadmin only."""
import csv
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from sms_integration import announcements as ann
from sms_integration import prospects as svc
from sms_integration.models import SMSAnnouncement, SMSProspect

from .views import superadmin_required

Status = SMSProspect.Status


def _type_or_default(value):
    return value if value in SMSProspect.BusinessType.values else SMSProspect.BusinessType.OTHER


@superadmin_required
def prospect_list_view(request):
    svc.mark_existing_customers_as_joined()
    qs = SMSProspect.objects.all()
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    btype = request.GET.get('type', '').strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(business_name__icontains=q) | Q(phone_number__icontains=q) | Q(region__icontains=q))
    if status in Status.values:
        qs = qs.filter(status=status)
    if btype in SMSProspect.BusinessType.values:
        qs = qs.filter(business_type=btype)

    totals = dict(SMSProspect.objects.values_list('status').annotate(c=Count('id')).order_by())
    page_obj = Paginator(qs, 25).get_page(request.GET.get('page'))
    return render(request, 'hq/prospect_list.html', {
        'page_obj': page_obj, 'q': q, 'current_status': status, 'current_type': btype,
        'status_choices': Status.choices, 'type_choices': SMSProspect.BusinessType.choices,
        'total': sum(totals.values()), 'totals': totals,
        'join_rate': round(totals.get(Status.JOINED, 0) * 100 / sum(totals.values())) if totals else 0,
        'bar': [(label, totals.get(value, 0), value) for value, label in Status.choices],
        'eligible': svc.eligible_queryset().count(),
        'never_messaged': svc.eligible_queryset().filter(sms_count=0).count(),
    })


@superadmin_required
@require_POST
def prospect_add_view(request):
    phone, err = svc.clean_phone(request.POST.get('phone_number', ''))
    if err:
        messages.error(request, f'Number not saved: {err}')
        return redirect('hq:prospect_list')
    prospect, created = SMSProspect.objects.get_or_create(
        phone_number=phone,
        defaults={
            'name': request.POST.get('name', '').strip()[:150],
            'business_name': request.POST.get('business_name', '').strip()[:150],
            'business_type': _type_or_default(request.POST.get('business_type')),
            'region': request.POST.get('region', '').strip()[:60],
            'notes': request.POST.get('notes', '').strip()[:300],
            'created_by': request.user,
        })
    if created:
        messages.success(request, f'{prospect.display_name} ({phone}) added.')
    else:
        messages.info(request, f'{phone} is already registered ({prospect.display_name}).')
    return redirect('hq:prospect_list')


@superadmin_required
@require_POST
def prospect_import_view(request):
    entries, bad = svc.parse_contacts(request.POST.get('contacts', ''))
    if not entries and not bad:
        messages.error(request, 'Paste at least one number (one per line).')
        return redirect('hq:prospect_list')
    result = svc.register_prospects(
        entries, created_by=request.user,
        business_type=_type_or_default(request.POST.get('business_type')),
        region=request.POST.get('region', '').strip())
    invalid = result['invalid'] + bad
    messages.success(request, f"Added {result['added']} new number(s). Already registered: {result['duplicates']}. Invalid: {len(invalid)}.")
    if invalid:
        shown = '; '.join(f'{raw} ({why})' for raw, why in invalid[:5])
        messages.warning(request, f"Rejected numbers: {shown}{' ...' if len(invalid) > 5 else ''}")
    return redirect('hq:prospect_list')


@superadmin_required
@require_POST
def prospect_action_view(request, pk):
    prospect = get_object_or_404(SMSProspect, pk=pk)
    action = request.POST.get('action')
    if action == 'optout':
        prospect.status = Status.OPTED_OUT
    elif action == 'reactivate':
        prospect.status = Status.CONTACTED if prospect.sms_count else Status.NEW
    elif action == 'joined':
        prospect.status = Status.JOINED
    elif action == 'delete':
        prospect.delete()
        messages.success(request, 'Number deleted.')
        return redirect('hq:prospect_list')
    else:
        messages.error(request, 'Unknown action.')
        return redirect('hq:prospect_list')
    prospect.save(update_fields=['status', 'updated_at'])
    messages.success(request, f'{prospect.display_name}: {prospect.get_status_display()}.')
    return redirect('hq:prospect_list')


@superadmin_required
@require_POST
def prospect_update_view(request, pk):
    """Edit a prospect's details (opened from the toast-style Edit panel on the list)."""
    prospect = get_object_or_404(SMSProspect, pk=pk)
    nxt = request.POST.get('next', '')
    back = nxt if nxt.startswith('/hq/prospects/') else reverse('hq:prospect_list')

    phone, err = svc.clean_phone(request.POST.get('phone_number', ''))
    if err:
        messages.error(request, f'Changes not saved: {err}')
        return redirect(back)
    if SMSProspect.objects.filter(phone_number=phone).exclude(pk=prospect.pk).exists():
        messages.error(request, f'Changes not saved: {phone} is already registered for another prospect.')
        return redirect(back)

    prospect.phone_number = phone
    prospect.name = request.POST.get('name', '').strip()[:150]
    prospect.business_name = request.POST.get('business_name', '').strip()[:150]
    prospect.business_type = _type_or_default(request.POST.get('business_type'))
    prospect.region = request.POST.get('region', '').strip()[:60]
    prospect.notes = request.POST.get('notes', '').strip()[:300]
    prospect.save(update_fields=['phone_number', 'name', 'business_name', 'business_type', 'region', 'notes', 'updated_at'])
    messages.success(request, f'{prospect.display_name} ({phone}) updated.')
    return redirect(back)


@superadmin_required
def prospect_export_view(request):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="smartpos-prospects.csv"'
    writer = csv.writer(response)
    writer.writerow(['Phone', 'Name', 'Business', 'Type', 'Region', 'Status', 'SMS sent', 'Last SMS', 'Added'])
    for p in SMSProspect.objects.all().iterator():
        writer.writerow([p.phone_number, p.name, p.business_name, p.get_business_type_display(), p.region,
                         p.get_status_display(), p.sms_count,
                         p.last_sms_at.strftime('%Y-%m-%d %H:%M') if p.last_sms_at else '',
                         p.created_at.strftime('%Y-%m-%d')])
    return response


@superadmin_required
@require_http_methods(['GET', 'POST'])
def prospect_campaign_view(request):
    svc.mark_existing_customers_as_joined()
    ready, reason = ann.provider_ready()
    default_link = request.build_absolute_uri('/')
    signup_link = request.build_absolute_uri('/jiunge/')
    initial = {'title': svc.PROSPECT_TEMPLATES[0]['title'], 'message': svc.PROSPECT_TEMPLATES[0]['text'],
               'link_url': default_link, 'mode': 'new', 'optout': False, 'business_type': ''}
    data = initial
    errors = {}

    if request.method == 'POST':
        data = {
            'title': request.POST.get('title', '').strip(),
            'message': request.POST.get('message', '').strip(),
            'link_url': request.POST.get('link_url', '').strip(),
            'mode': request.POST.get('mode', 'new'),
            'optout': False,
            'business_type': request.POST.get('business_type', ''),
        }
        selected_ids = [int(i) for i in request.POST.getlist('prospect_ids') if i.isdigit()]
        if not data['title']:
            errors['title'] = 'Enter a message title.'
        if not data['message']:
            errors['message'] = 'Write the message.'
        elif len(data['message']) > ann.MAX_TEMPLATE_CHARS:
            errors['message'] = f'The message must not exceed {ann.MAX_TEMPLATE_CHARS} characters.'
        else:
            bad = ann.find_unsupported_placeholders(data['message'])
            if bad:
                errors['message'] = 'Only {name} and {link} are available.'
        if '{link}' in data['message'] and not data['link_url'].startswith(('http://', 'https://')):
            errors['link_url'] = 'Enter a full link starting with https://'
        if data['mode'] not in ('new', 'all', 'selected'):
            data['mode'] = 'new'
        if data['mode'] == 'selected' and not selected_ids:
            errors['mode'] = 'Select at least one number.'
        if not ready:
            errors['provider'] = reason

        if not errors:
            recipients = list(svc.resolve_prospects(data['mode'], selected_ids, data['business_type']))
            if not recipients:
                errors['mode'] = 'No numbers qualify for this message (contacts who joined or opted out are never messaged).'
            elif SMSAnnouncement.objects.filter(
                    created_by=request.user, title=data['title'], message=data['message'],
                    audience=SMSAnnouncement.Audience.PROSPECTS,
                    created_at__gte=timezone.now() - timedelta(minutes=2)).exists():
                messages.warning(request, 'This exact message was just sent. Open the campaign below to check its progress.')
                return redirect('hq:announcement_list')
            else:
                with transaction.atomic():
                    announcement = SMSAnnouncement.objects.create(
                        title=data['title'], message_type=SMSAnnouncement.MessageType.ANNOUNCEMENT,
                        message=data['message'], link_url=data['link_url'],
                        audience=SMSAnnouncement.Audience.PROSPECTS, also_in_app=False,
                        include_assistants=False, created_by=request.user)
                    svc.build_prospect_recipients(announcement, recipients, include_optout=data['optout'])
                    transaction.on_commit(lambda pk=announcement.pk: ann.start_sending(pk))
                messages.success(request, f'Invitation queued for {len(recipients)} business owner(s). Delivery is running now.')
                return redirect('hq:announcement_detail', pk=announcement.pk)
    else:
        selected_ids = []

    eligible = svc.eligible_queryset()
    type_counts = {r['business_type']: {'all': r['a'], 'new': r['n']} for r in eligible.values('business_type').annotate(
        a=Count('id'), n=Count('id', filter=Q(sms_count=0))).order_by()}
    return render(request, 'hq/prospect_campaign.html', {
        'type_counts': type_counts,
        'data': data, 'errors': errors, 'provider_ready': ready, 'provider_reason': reason,
        'templates': svc.PROSPECT_TEMPLATES, 'optout_line': svc.OPTOUT_LINE,
        'default_link': default_link, 'signup_link': signup_link,
        'type_choices': SMSProspect.BusinessType.choices,
        'prospects': list(eligible.order_by('-created_at')[:500]),
        'selected_ids': {str(i) for i in selected_ids},
        'counts': {'all': eligible.count(), 'new': eligible.filter(sms_count=0).count()},
        'max_chars': ann.MAX_TEMPLATE_CHARS,
        'footer': ann.ADMINISTRATOR_FOOTER,
    })
