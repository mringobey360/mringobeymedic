from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from decimal import Decimal
from django.db.models import F, Count, Sum, DecimalField, Value
from django.db.models.functions import Coalesce
from datetime import timedelta
from django.utils import timezone
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required
from accounts.phone_utils import normalize_phone_number
from .forms import CustomerContactForm, CustomerBulkUploadForm, parse_customer_file, row_value
from .models import CustomerCampaign, CustomerCampaignRecipient, CustomerContact, CustomerDataAccessLog
from core.ratelimit import client_ip
from sales.models import Sale, SaleItem
from sms_integration.models import SMSMarketingSubscription, SMSMarketingPurchase

def _branch(request):
    if request.pharmacy is None:
        raise PermissionDenied('Select an active branch first.')
    return request.pharmacy

def _marketing_subscription(request):
    if not request.user.is_authenticated:
        return None
    # Marketing SMS never expire: the package stays usable until its credits are finished.
    return (SMSMarketingSubscription.objects
            .filter(owner=request.user.business_owner(), status=SMSMarketingSubscription.Status.ACTIVE,
                    sms_used__lt=F('sms_credits'))
            .order_by('id').first())

def _premium_marketing(request):
    return bool(_marketing_subscription(request))

def _require_premium_manager(request):
    if not request.user.is_management_user():
        raise PermissionDenied('Only the business manager can send customer campaigns.')
    if not _marketing_subscription(request):
        messages.info(request, 'Customer Marketing SMS is a paid add-on. Activate a Marketing SMS package to continue.')
        return redirect('hq:marketing_sms')
    return _branch(request)

def _log_data_access(request, action, count=0, pharmacy=None):
    """Audit trail for customer-number handling. Never allowed to break the request."""
    try:
        CustomerDataAccessLog.objects.create(
            user=request.user if request.user.is_authenticated else None,
            username=getattr(request.user, 'username', '')[:150],
            pharmacy=pharmacy or getattr(request, 'pharmacy', None),
            action=action, record_count=count, ip_address=client_ip(request),
        )
    except Exception:  # noqa: BLE001
        pass


def _customer_queryset(pharmacy, q, status, include_phone=True):
    qs = CustomerContact.objects.filter(pharmacy=pharmacy)
    if q:
        # Only managers may search BY phone: searching by number would let any employee
        # confirm whether a given person's number is in the registry.
        cond = Q(name__icontains=q)
        if include_phone:
            cond |= Q(phone__icontains=q)
        qs = qs.filter(cond)
    if status in ('active', 'inactive'):
        qs = qs.filter(is_active=(status == 'active'))
    return qs.order_by('name', 'pk')


@login_required
def customer_list(request):
    pharmacy = _branch(request)
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', 'active')
    qs = _customer_queryset(pharmacy, q, status, include_phone=request.user.is_management_user())
    total_count = qs.count()
    # Loyalty is derived from real sales and uses the exact same branch policy as POS.
    from rewards.services import get_vip_policy
    vip_policy = get_vip_policy(pharmacy)
    today = timezone.localdate()
    window_days = max(1, int(vip_policy.window_days or 90))
    window_start = today - timedelta(days=window_days - 1)
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    page_customers = list(page.object_list)
    customer_ids = [c.pk for c in page_customers]
    visits_by_customer = {}
    spend_by_customer = {}
    if customer_ids:
        visits_by_customer = dict(
            Sale.objects.filter(
                pharmacy=pharmacy, customer_contact_id__in=customer_ids,
                business_date__gte=window_start, business_date__lte=today,
                status__in=[Sale.Status.COMPLETED, Sale.Status.PARTIALLY_RETURNED],
            ).values('customer_contact_id').annotate(visits=Count('id')).values_list('customer_contact_id', 'visits')
        )
        spend_rows = SaleItem.objects.filter(
            sale__pharmacy=pharmacy, sale__customer_contact_id__in=customer_ids,
            sale__business_date__gte=window_start, sale__business_date__lte=today,
            sale__status__in=[Sale.Status.COMPLETED, Sale.Status.PARTIALLY_RETURNED],
        ).values('sale__customer_contact_id').annotate(
            spend=Coalesce(
                Sum(F('unit_price') * F('quantity'), output_field=DecimalField(max_digits=14, decimal_places=2)),
                Value(Decimal('0.00'), output_field=DecimalField(max_digits=14, decimal_places=2)),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        ).values_list('sale__customer_contact_id', 'spend')
        spend_by_customer = dict(spend_rows)
    for customer in page_customers:
        customer.visits_90d = visits_by_customer.get(customer.pk, 0)
        customer.spend_90d = spend_by_customer.get(customer.pk, 0)
        meets_spend = (vip_policy.minimum_spend or 0) <= 0 or customer.spend_90d >= vip_policy.minimum_spend
        qualifies = vip_policy.enabled and customer.visits_90d >= vip_policy.qualifying_visits and meets_spend
        customer.loyalty_status = (
            'VIP' if qualifies else
            'ALMOST VIP' if customer.visits_90d >= max(1, vip_policy.qualifying_visits - 1) else
            'LOYAL' if customer.visits_90d >= 2 else 'REGULAR'
        )
        customer.visits_to_vip = max(0, vip_policy.qualifying_visits - customer.visits_90d)
    context = {
        'page_obj': page, 'q': q, 'status': status, 'total_count': total_count,
        'vip_visit_threshold': vip_policy.qualifying_visits, 'loyalty_window_days': window_days, 'vip_policy': vip_policy,
        'premium_marketing': _premium_marketing(request),
        'marketing_subscription': _marketing_subscription(request),
    }
    # Live search: the browser asks only for the results block.
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return render(request, 'customer_registry/_results.html', context)
    return render(request, 'customer_registry/list.html', context)


@login_required
def customer_create(request):
    pharmacy=_branch(request)
    if request.method=='POST':
        form=CustomerContactForm(request.POST)
        if form.is_valid():
            obj=form.save(commit=False); obj.pharmacy=pharmacy; obj.created_by=request.user
            try:
                obj.save()
                messages.success(request,f'Customer {obj.name} registered successfully.')
                return redirect('customer_registry:list')
            except Exception as exc:
                if 'uniq_customer_phone_per_branch' in str(exc):
                    form.add_error('phone','This phone number is already registered in this branch.')
                else: raise
    else: form=CustomerContactForm()
    return render(request,'customer_registry/form.html',{'form':form,'mode':'Register customer'})


@login_required
def customer_edit(request, pk):
    pharmacy = _branch(request)
    if not request.user.is_management_user():
        raise PermissionDenied('Only a manager or authorized assistant can edit customer records.')
    customer = get_object_or_404(CustomerContact, pk=pk, pharmacy=pharmacy)
    if request.method == 'POST':
        form = CustomerContactForm(request.POST, instance=customer)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, f'{customer.name} has been updated successfully.')
                return redirect('customer_registry:list')
            except Exception as exc:
                if 'uniq_customer_phone_per_branch' in str(exc):
                    form.add_error('phone', 'This phone number is already registered in this branch.')
                else:
                    raise
    else:
        form = CustomerContactForm(instance=customer)
    return render(request, 'customer_registry/form.html', {'form': form, 'mode': 'Edit customer', 'customer': customer})


@login_required
@require_POST
def customer_delete(request, pk):
    pharmacy = _branch(request)
    if not request.user.is_management_user():
        raise PermissionDenied('Only the business manager can delete customer records.')
    customer = get_object_or_404(CustomerContact, pk=pk, pharmacy=pharmacy)
    name = customer.name
    # Campaign recipients keep a historical snapshot, so deleting a customer does
    # not erase delivery/audit history. The customer itself is safely removable.
    customer.delete()
    messages.success(request, f'{name} has been removed from the customer registry. Existing campaign history is preserved.')
    return redirect('customer_registry:list')

@login_required
@require_POST
def customer_bulk_delete(request):
    pharmacy = _branch(request)
    if not request.user.is_management_user():
        raise PermissionDenied('Only the business manager can delete customer records.')
    if request.POST.get('all_matching') == '1':
        # "Select all N customers" across every page of the current search/filter.
        qs = _customer_queryset(pharmacy, request.POST.get('q', '').strip(), request.POST.get('status', 'active'))   # manager-only view
    else:
        ids = [int(i) for i in request.POST.getlist('ids') if str(i).isdigit()]
        if not ids:
            messages.warning(request, 'No customers were selected.')
            return redirect('customer_registry:list')
        qs = CustomerContact.objects.filter(pharmacy=pharmacy, pk__in=ids)
    count = qs.count()
    if not count:
        messages.warning(request, 'No matching customers were found.')
        return redirect('customer_registry:list')
    # Campaign recipients keep a historical snapshot (customer FK is SET_NULL).
    with transaction.atomic():
        CustomerContact.objects.filter(pk__in=list(qs.values_list('pk', flat=True))).delete()
    _log_data_access(request, CustomerDataAccessLog.Action.BULK_DELETE, count, pharmacy)
    messages.success(request, f'{count:,} customer(s) removed from the registry. Campaign history is preserved.')
    return redirect('customer_registry:list')


@login_required
def customer_bulk_template(request):
    """Download the clean customer import template; status is managed automatically."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = 'Customers'
    headers = ['Customer Name', 'Phone Number', 'Notes']
    ws.append(headers)
    ws.append(['Asha Mussa', '0712345678', 'SAMPLE — DELETE BEFORE IMPORT'])
    ws.append(['John Michael', '0754567890', 'SAMPLE — DELETE BEFORE IMPORT'])
    ws.append(['Example Customer', '0768000000', 'SAMPLE — DELETE BEFORE IMPORT'])
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='0F766E')
        cell.alignment = Alignment(horizontal='center')
    ws.freeze_panes = 'A2'
    # Make sample rows visually distinct so staff immediately know they are examples.
    sample_fill = PatternFill('solid', fgColor='FFF7D6')
    sample_font = Font(color='7A5B00')
    for row in ws.iter_rows(min_row=2, max_row=4):
        for cell in row:
            cell.fill = sample_fill
            cell.font = sample_font
    # Helpful comments on the input headers.
    ws['A1'].comment = __import__('openpyxl').comments.Comment('Example: Asha Mussa. Replace the sample rows with your real customer names.', 'SmartPOS')
    ws['B1'].comment = __import__('openpyxl').comments.Comment('Example: 0712345678 or +255712345678. The system normalizes Tanzanian numbers automatically.', 'SmartPOS')
    ws['C1'].comment = __import__('openpyxl').comments.Comment('Optional. Example: VIP customer. Do not enter Status.', 'SmartPOS')
    widths = [28, 22, 36]
    for i, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    info = wb.create_sheet('Instructions')
    info_rows = [
        ['CUSTOMER BULK REGISTRATION'],
        ['1', 'Enter the customer full name.'],
        ['2', 'Enter a valid Tanzanian mobile number. Example: 0712345678 or +255712345678.'],
        ['3', 'Notes are optional.'],
        ['4', 'Do not enter Status. New customers are automatically registered as Active.'],
        ['5', 'If a phone number already exists, the system can update that customer when the update option is enabled.'],
        ['6', 'The yellow rows are sample data for guidance only. Delete them before importing your real customers.'],
        ['7', 'Tip: You can keep the column headings exactly as they are and replace only the sample data.'],
    ]
    for row in info_rows: info.append(row)
    info.column_dimensions['A'].width = 8
    info.column_dimensions['B'].width = 105
    info['A1'].font = Font(bold=True, size=14, color='FFFFFF')
    info['A1'].fill = PatternFill('solid', fgColor='0F766E')
    info.merge_cells('A1:B1')
    for row in info.iter_rows(min_row=2):
        row[0].font = Font(bold=True)
        row[1].alignment = Alignment(wrap_text=True, vertical='top')
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="customer_bulk_template.xlsx"'
    wb.save(response)
    return response


@login_required
def customer_bulk(request):
    pharmacy=_branch(request)
    if request.method=='POST':
        form=CustomerBulkUploadForm(request.POST,request.FILES)
        if form.is_valid():
            try: rows=parse_customer_file(form.cleaned_data['file'])
            except Exception as exc:
                form.add_error('file',f'Could not read file: {exc}')
            else:
                if not rows:
                    form.add_error('file','The file contains no data rows.')
                else:
                    created=updated=skipped=0; errors=[]
                    for idx,row in enumerate(rows, start=2):
                        name=row_value(row,'name','customer name','full name')
                        raw_phone=row_value(row,'phone','phone number','mobile','mobile number')
                        notes=row_value(row,'notes','note')
                        if not name or not raw_phone:
                            skipped+=1; errors.append(f'Row {idx}: name and phone are required.'); continue
                        try: phone=normalize_phone_number(raw_phone)
                        except Exception as exc:
                            skipped+=1; errors.append(f'Row {idx}: invalid phone ({exc}).'); continue
                        obj=CustomerContact.objects.filter(pharmacy=pharmacy,phone=phone).first()
                        if obj:
                            if form.cleaned_data['update_existing']:
                                obj.name=name; obj.notes=notes; obj.is_active=True; obj.save(update_fields=['name','notes','is_active','updated_at']); updated+=1
                            else:
                                skipped+=1; errors.append(f'Row {idx}: phone already exists.'); 
                        else:
                            CustomerContact.objects.create(pharmacy=pharmacy,name=name,phone=phone,notes=notes,created_by=request.user); created+=1
                    _log_data_access(request, CustomerDataAccessLog.Action.BULK_IMPORT, created+updated, pharmacy)
                    msg=f'Bulk registration complete: {created} added, {updated} updated, {skipped} skipped.'
                    if errors: messages.warning(request,msg+' Review skipped rows below.')
                    else: messages.success(request,msg)
                    return render(request,'customer_registry/bulk_result.html',{'created':created,'updated':updated,'skipped':skipped,'errors':errors})
    else: form=CustomerBulkUploadForm()
    return render(request,'customer_registry/bulk.html',{'form':form})

@admin_required
@require_POST
def customer_toggle(request,pk):
    pharmacy=_branch(request)
    obj=get_object_or_404(CustomerContact,pk=pk,pharmacy=pharmacy)
    obj.is_active=not obj.is_active; obj.save(update_fields=['is_active','updated_at'])
    messages.success(request, f'{obj.name} is now {"active" if obj.is_active else "inactive"}.')
    return redirect(request.META.get('HTTP_REFERER') or 'customer_registry:list')

@login_required
def customer_export(request):
    """Manager-only CSV of customer names + phone numbers (always audited)."""
    pharmacy=_branch(request)
    if not request.user.is_management_user():
        _log_data_access(request, CustomerDataAccessLog.Action.EXPORT_DENIED, 0, pharmacy)
        raise PermissionDenied('Only the business manager can export the customer list.')
    qs=CustomerContact.objects.filter(pharmacy=pharmacy).order_by('name')
    _log_data_access(request, CustomerDataAccessLog.Action.EXPORT, qs.count(), pharmacy)
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']='attachment; filename="customers.csv"'
    response['Cache-Control']='no-store'
    w=__import__('csv').writer(response)
    w.writerow(['Customer Name','Phone Number','Customer Status','Notes','Registered On'])
    for c in qs: w.writerow([c.name,c.phone,'Active' if c.is_active else 'Inactive',c.notes,c.created_at.strftime('%Y-%m-%d')])
    return response



@login_required
def campaign_list(request):
    pharmacy=_branch(request)
    if not request.user.is_management_user():
        raise PermissionDenied('Only the business manager can access campaign history.')
    campaigns=CustomerCampaign.objects.filter(pharmacy=pharmacy).select_related('created_by')
    paginator=Paginator(campaigns,15)
    page=paginator.get_page(request.GET.get('page'))
    return render(request,'customer_registry/campaign_list.html',{'page_obj':page,'premium_marketing':_premium_marketing(request), 'marketing_subscription': _marketing_subscription(request)})

MAX_EXTRA_RECIPIENTS = 500


def _first_name(name):
    value = ' '.join((name or '').strip().split())
    return value.split()[0] if value else 'Customer'


def _campaign_sms_message(title, message, customer_name, business_phone):
    """Build the final customer-facing SMS once, before segment billing.

    Title, greeting and branch contact are system-controlled so every campaign
    is recognizable and gives the customer a direct service contact.
    """
    title = ' '.join((title or '').split())
    message = (message or '').strip()
    name = _first_name(customer_name)
    parts = [title, f'Habari {name}! {message}']
    if business_phone:
        parts.append(f'Kwa huduma/maelezo zaidi: {business_phone}')
    parts.append('Asante kwa kutuamini.')
    from sms_integration.utils import tidy_sms
    return tidy_sms('\n'.join(p for p in parts if p))  # same text that is billed and sent


def _normalize_extra_phone(raw):
    """Normalise a phone typed in the campaign form. Returns '+255...' or raises ValidationError."""
    import re
    from django import forms
    value = re.sub(r'[\s\-().]', '', (raw or '').strip())
    # Tanzanian mobile typed without the leading 0 / country code, e.g. 712345678.
    if value.isdigit() and len(value) == 9 and value[0] in '67':
        value = '0' + value
    return normalize_phone_number(value)


def _parse_extra_phones(raw_items):
    """Split, normalise and de-duplicate extra numbers. Returns (valid_list, invalid_list)."""
    import re
    tokens = []
    for item in raw_items:
        tokens.extend(t for t in re.split(r'[,;\n]+', item or '') if t)
    valid, invalid, seen = [], [], set()
    from django import forms
    for token in tokens:
        try:
            phone = _normalize_extra_phone(token)
        except forms.ValidationError:
            invalid.append(token)
            continue
        if phone not in seen:
            seen.add(phone)
            valid.append(phone)
    return valid, invalid


@login_required
def campaign_create(request):
    if not request.user.is_management_user():
        raise PermissionDenied('Only the business manager can send customer campaigns.')
    pharmacy = _marketing_subscription(request) and _branch(request)
    subscription = _marketing_subscription(request)
    if pharmacy is None or subscription is None:
        messages.info(request, 'Customer Marketing SMS is a paid add-on. Activate a Marketing SMS package to continue.')
        return redirect('hq:marketing_sms')

    customers = CustomerContact.objects.filter(pharmacy=pharmacy, is_active=True).order_by('name')

    def form_page(form_data=None):
        return render(request, 'customer_registry/campaign_form.html', {
            'customers': customers, 'premium_marketing': True, 'marketing_subscription': subscription,
            'business_phone': pharmacy.phone_number or '',
            'branch_name': pharmacy.name,
            'form_data': form_data or {},
        })

    if request.method != 'POST':
        return form_page()

    title = request.POST.get('title', '').strip()
    message = request.POST.get('message', '').strip()
    audience = request.POST.get('audience', 'ALL_ACTIVE')
    selected_ids = request.POST.getlist('customer_ids')
    raw_extras = request.POST.getlist('extra_phones')
    form_data = {'title': title, 'message': message, 'audience': audience, 'selected_ids': selected_ids, 'extra_phones': raw_extras}

    extra_phones, invalid_extras = _parse_extra_phones(raw_extras)
    if invalid_extras:
        shown = ', '.join(invalid_extras[:5]) + (' ...' if len(invalid_extras) > 5 else '')
        messages.error(request, f'{len(invalid_extras)} invalid phone number(s): {shown}. Fix or remove them and try again.')
        return form_page(form_data)
    if len(extra_phones) > MAX_EXTRA_RECIPIENTS:
        messages.error(request, f'You can add at most {MAX_EXTRA_RECIPIENTS} extra numbers per campaign. Use Bulk Registration for larger lists.')
        return form_page(form_data)
    if not title or not message:
        messages.error(request, 'Campaign title and message are required.')
        return form_page(form_data)
    if not pharmacy.phone_number:
        messages.error(request, 'Set the branch office/customer-service phone number before sending a Marketing SMS. Customers need a direct contact number.')
        return form_page(form_data)
    if len(message) > 1000:
        messages.error(request, 'Message is too long. Maximum 1000 characters.')
        return form_page(form_data)

    if audience == 'EXTRA_ONLY':
        target = []
    elif audience == 'SELECTED':
        target = list(customers.filter(pk__in=selected_ids))
    else:
        target = list(customers)

    # Extra numbers that already belong to a targeted customer are skipped (no double SMS).
    registry_phones = {c.phone for c in target}
    duplicates = [ph for ph in extra_phones if ph in registry_phones]
    extra_phones = [ph for ph in extra_phones if ph not in registry_phones]

    if not target and not extra_phones:
        messages.error(request, 'Select at least one customer or add at least one phone number.')
        return form_page(form_data)

    from sms_integration.utils import sms_segment_count
    prepared = []  # (customer_or_None, name, phone, final_message, segments)
    total_segments = 0
    for customer in target:
        final_message = _campaign_sms_message(title, message, customer.name, pharmacy.phone_number)
        final_message = final_message.replace('{name}', _first_name(customer.name))
        segments = sms_segment_count(final_message)
        prepared.append((customer, customer.name, customer.phone, final_message, segments))
        total_segments += segments
    for phone in extra_phones:
        final_message = _campaign_sms_message(title, message, 'Customer', pharmacy.phone_number)
        final_message = final_message.replace('{name}', 'Customer')
        segments = sms_segment_count(final_message)
        prepared.append((None, 'Customer', phone, final_message, segments))
        total_segments += segments

    if total_segments > subscription.credits_remaining:
        messages.error(
            request,
            f'This campaign needs {total_segments:,} SMS credits for {len(prepared):,} recipient(s), but only {subscription.credits_remaining:,} credits are available. '
            'Short messages use 1 credit; longer messages use additional credits per SMS segment.'
        )
        return form_page(form_data)

    with transaction.atomic():
        locked = SMSMarketingSubscription.objects.select_for_update().get(pk=subscription.pk)
        if locked.credits_remaining < total_segments:
            messages.error(request, 'Your Marketing SMS balance changed while preparing this campaign. Please review the balance and try again.')
            return form_page(form_data)
        locked.sms_used = F('sms_used') + total_segments
        locked.save(update_fields=['sms_used', 'updated_at'])
        campaign = CustomerCampaign.objects.create(
            pharmacy=pharmacy, created_by=request.user, title=title, message=message,
            total_recipients=len(prepared), total_segments=total_segments,
        )
        CustomerCampaignRecipient.objects.bulk_create([
            CustomerCampaignRecipient(
                campaign=campaign, customer=c, name=name, phone=phone,
                message=final_message, segments=segments, credits_reserved=segments,
            ) for c, name, phone, final_message, segments in prepared
        ])

    from .tasks import queue_customer_campaign
    try:
        queue_customer_campaign.delay(campaign.pk)
    except Exception:
        # Return all reserved credits if the durable queue cannot be reached.
        with transaction.atomic():
            sub = SMSMarketingSubscription.objects.select_for_update().get(pk=subscription.pk)
            sub.sms_used = max(sub.sms_used - total_segments, 0)
            sub.save(update_fields=['sms_used', 'updated_at'])
        CustomerCampaign.objects.filter(pk=campaign.pk).update(status=CustomerCampaign.Status.FAILED, sent_count=0, failed_count=len(prepared), completed_at=timezone.now())
        CustomerCampaignRecipient.objects.filter(campaign=campaign).update(status=CustomerCampaignRecipient.Status.FAILED, credits_reserved=0, error='Background SMS queue unavailable.')
        messages.error(request, 'The campaign was saved, but the background SMS queue is unavailable. Start Redis/Celery and retry the campaign.')
        return redirect('customer_registry:campaign_detail', pk=campaign.pk)

    note = f' ({len(extra_phones):,} extra number(s) included)' if extra_phones else ''
    if duplicates:
        note += f' {len(duplicates):,} extra number(s) skipped because they are already selected customers.'
    messages.success(request, f'Campaign queued for {len(prepared):,} recipient(s){note}. {total_segments:,} marketing SMS credits have been reserved based on actual message length.')
    return redirect('customer_registry:campaign_detail', pk=campaign.pk)


@login_required
def campaign_detail(request,pk):
    pharmacy=_branch(request)
    if not request.user.is_management_user(): raise PermissionDenied
    campaign=get_object_or_404(CustomerCampaign.objects.select_related('created_by'),pk=pk,pharmacy=pharmacy)
    recips=campaign.recipients.select_related('customer').order_by('status','name')
    paginator=Paginator(recips,30)
    page=paginator.get_page(request.GET.get('page'))
    return render(request,'customer_registry/campaign_detail.html',{'campaign':campaign,'page_obj':page})


@login_required
@require_POST
def campaign_retry(request, pk):
    pharmacy = _branch(request)
    if not request.user.is_management_user():
        raise PermissionDenied
    subscription = _marketing_subscription(request)
    if not subscription:
        messages.error(request, 'Your Customer Marketing SMS package is not active or has no SMS remaining.')
        return redirect('hq:marketing_sms')
    campaign = get_object_or_404(CustomerCampaign, pk=pk, pharmacy=pharmacy)
    if campaign.status == CustomerCampaign.Status.SENDING:
        messages.info(request, 'This campaign is already being processed.')
        return redirect('customer_registry:campaign_detail', pk=pk)
    failed = list(campaign.recipients.filter(status=CustomerCampaignRecipient.Status.FAILED))
    retry_credits = sum(r.segments for r in failed)
    if retry_credits > subscription.credits_remaining:
        messages.error(request, f'Retrying these recipients needs {retry_credits:,} SMS credits, but only {subscription.credits_remaining:,} remain.')
        return redirect('customer_registry:campaign_detail', pk=pk)
    with transaction.atomic():
        sub = SMSMarketingSubscription.objects.select_for_update().get(pk=subscription.pk)
        if sub.credits_remaining < retry_credits:
            messages.error(request, 'Your Marketing SMS balance changed. Please refresh and try again.')
            return redirect('customer_registry:campaign_detail', pk=pk)
        sub.sms_used = F('sms_used') + retry_credits
        sub.save(update_fields=['sms_used', 'updated_at'])
        CustomerCampaignRecipient.objects.filter(campaign=campaign, status=CustomerCampaignRecipient.Status.FAILED).update(status=CustomerCampaignRecipient.Status.PENDING, error='', credits_reserved=F('segments'))
        CustomerCampaign.objects.filter(pk=campaign.pk).update(status=CustomerCampaign.Status.QUEUED, completed_at=None, total_segments=F('total_segments') + retry_credits)
    campaign.refresh_from_db()
    campaign.status = CustomerCampaign.Status.QUEUED
    campaign.completed_at = None
    campaign.save(update_fields=['status', 'completed_at'])
    from .tasks import queue_customer_campaign
    try:
        queue_customer_campaign.delay(campaign.pk)
    except Exception:
        with transaction.atomic():
            sub = SMSMarketingSubscription.objects.select_for_update().get(pk=subscription.pk)
            sub.sms_used = max(sub.sms_used - retry_credits, 0)
            sub.save(update_fields=['sms_used', 'updated_at'])
            CustomerCampaignRecipient.objects.filter(campaign=campaign, status=CustomerCampaignRecipient.Status.PENDING).update(status=CustomerCampaignRecipient.Status.FAILED, credits_reserved=0, error='Background SMS queue unavailable.')
        failed_now = CustomerCampaignRecipient.objects.filter(campaign=campaign, status=CustomerCampaignRecipient.Status.FAILED).count()
        campaign.status = CustomerCampaign.Status.FAILED
        campaign.failed_count = failed_now
        campaign.completed_at = timezone.now()
        campaign.save(update_fields=['status', 'failed_count', 'completed_at'])
        messages.error(request, 'The background SMS queue is unavailable. Reserved credits were returned. Start Redis/Celery and try again.')
        return redirect('customer_registry:campaign_detail', pk=pk)
    messages.success(request, 'Failed recipients have been queued again.')
    return redirect('customer_registry:campaign_detail', pk=pk)
