import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils.html import escape
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from .models import LayawayPlan, LayawayPayment, LayawayAudit, LayawayRefund
from .forms import LayawayCreateForm, LayawayPaymentForm
from . import services
from accounts.workday_guard import workday_sales_block_message

def _branch(request):
    pharmacy=getattr(request,'pharmacy',None)
    if not pharmacy: raise PermissionDenied('Select an active branch before using Layaway.')
    return pharmacy

def _sms_ctx(pharmacy):
    from .sms_service import _shop_line, _footer
    return {'sms_shop':_shop_line(pharmacy),'sms_footer':_footer(pharmacy)}

def _allowed(request):
    user=request.user
    return user.is_superuser or (hasattr(user,'is_management_user') and user.is_management_user()) or (hasattr(user,'is_employee_role') and user.is_employee_role()) or (hasattr(user,'is_manager_role') and user.is_manager_role())

def _is_manager(user):
    """Shop owner / manager / manager assistant (or platform admin). Cancelling and re-opening plans is theirs."""
    return user.is_superuser or (callable(getattr(user, 'is_management_user', None)) and user.is_management_user())

@login_required
def plan_list(request):
    if not _allowed(request): raise PermissionDenied
    return _render_list(request,_branch(request))

def _render_list(request,pharmacy,form=None,open_create=False):
    """Layaway list. The "New plan" form lives in a popup on this page; it is re-opened with
    errors when a submission is invalid, and when someone visits the old /create/ address."""
    base=LayawayPlan.objects.filter(pharmacy=pharmacy)
    qs=base.select_related('product','created_by').annotate(paid_sum=Sum('payments__amount',filter=Q(payments__status=LayawayPayment.Status.RECEIVED)))
    term=request.GET.get('q','').strip()
    if term: qs=qs.filter(Q(reference__icontains=term)|Q(customer_name__icontains=term)|Q(customer_phone__icontains=term)|Q(product__name__icontains=term))
    status=request.GET.get('status','')
    if status in LayawayPlan.Status.values: qs=qs.filter(status=status)
    start_raw=request.GET.get('start','').strip()
    end_raw=request.GET.get('end','').strip()
    start_date=_report_date(start_raw, None) if start_raw else None
    end_date=_report_date(end_raw, None) if end_raw else None
    if start_date and end_date and start_date > end_date: start_date, end_date = end_date, start_date
    if start_date:
        qs=qs.filter(created_at__date__gte=start_date)
        base=base.filter(created_at__date__gte=start_date)
    if end_date:
        qs=qs.filter(created_at__date__lte=end_date)
        base=base.filter(created_at__date__lte=end_date)
    plans=list(qs[:200])
    for p in plans:
        p.paid=p.paid_sum or Decimal('0'); p.bal=max(Decimal('0'),p.total_amount-p.paid)
        p.pct=int(min(100,p.paid/p.total_amount*100)) if p.total_amount else 0
    counts=dict(base.order_by().values_list('status').annotate(c=Count('id')))
    active=base.filter(status__in=(LayawayPlan.Status.ACTIVE, LayawayPlan.Status.PARTIALLY_HANDED_OVER))
    value=active.aggregate(v=Sum(ExpressionWrapper(F('unit_price')*F('quantity'),output_field=DecimalField(max_digits=16,decimal_places=2))))['v'] or Decimal('0')
    received=LayawayPayment.objects.filter(plan__in=active,status=LayawayPayment.Status.RECEIVED).aggregate(v=Sum('amount'))['v'] or Decimal('0')
    summary={'active':counts.get('ACTIVE',0)+counts.get('PARTIALLY_HANDED_OVER',0),'pending':counts.get('PAID_PENDING_HANDOVER',0),'resolution':counts.get('NEEDS_RESOLUTION',0),
             'overdue':active.filter(due_date__lt=timezone.localdate()).count(),'outstanding':max(Decimal('0'),value-received)}
    tabs=[{'key':'','label':'All','count':sum(counts.values())}]+[{'key':v,'label':l,'count':counts.get(v,0)} for v,l in LayawayPlan.Status.choices]
    form=form or LayawayCreateForm(pharmacy=pharmacy)
    return render(request,'layaway/list.html',{'plans':plans,'q':term,'status':status,'tabs':tabs,'pharmacy':pharmacy,'summary':summary,
        'form':form,'open_create':open_create,'is_manager':_is_manager(request.user),'search_url':reverse('sales:product_search_api'),'start':start_date,'end':end_date,**_sms_ctx(pharmacy)})

def _report_date(value, default):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date() if value else default
    except (TypeError, ValueError):
        return default


@login_required
def plan_report_pdf(request):
    """Branch-scoped Layaway analysis PDF using the shared SmartPOS report design."""
    if not _allowed(request):
        raise PermissionDenied
    pharmacy = _branch(request)
    today = timezone.localdate()
    first_plan_date = LayawayPlan.objects.filter(pharmacy=pharmacy).order_by('created_at').values_list('created_at', flat=True).first()
    default_start = timezone.localtime(first_plan_date).date() if first_plan_date else today.replace(day=1)
    start = _report_date(request.GET.get('start', ''), default_start)
    end = _report_date(request.GET.get('end', ''), today)
    if start > end:
        start, end = end, start

    plans_qs = LayawayPlan.objects.filter(
        pharmacy=pharmacy, created_at__date__range=(start, end)
    ).select_related('product', 'created_by').prefetch_related('payments', 'refunds', 'handovers')
    term = request.GET.get('q', '').strip()
    if term:
        plans_qs = plans_qs.filter(
            Q(reference__icontains=term) | Q(customer_name__icontains=term) |
            Q(customer_phone__icontains=term) | Q(product__name__icontains=term)
        )
    selected_status = request.GET.get('status', '').strip()
    if selected_status in LayawayPlan.Status.values:
        plans_qs = plans_qs.filter(status=selected_status)

    plans = list(plans_qs.order_by('-created_at'))
    status_counts = {label: 0 for _, label in LayawayPlan.Status.choices}
    total_value = Decimal('0')
    paid_total = Decimal('0')
    refund_total = Decimal('0')
    outstanding_total = Decimal('0')
    handed_units = 0
    pending_units = 0
    overdue_count = 0
    rows = []
    for plan in plans:
        paid = sum((p.amount for p in plan.payments.all() if p.status == LayawayPayment.Status.RECEIVED), Decimal('0'))
        refunded = sum((r.amount for r in plan.refunds.all()), Decimal('0'))
        handed = sum((h.quantity for h in plan.handovers.all()), 0)
        remaining = max(0, int(plan.quantity or 0) - handed)
        balance = max(Decimal('0'), plan.total_amount - paid)
        status_counts[plan.get_status_display()] = status_counts.get(plan.get_status_display(), 0) + 1
        total_value += plan.total_amount
        paid_total += paid
        refund_total += refunded
        outstanding_total += balance
        handed_units += handed
        pending_units += remaining
        if plan.status == LayawayPlan.Status.ACTIVE and plan.due_date and plan.due_date < today:
            overdue_count += 1
        rows.append([
            escape(plan.reference), plan.created_at.strftime('%d %b %Y'), escape(plan.customer_name),
            escape(plan.product.name), f'{plan.quantity}', f'{handed} / {plan.quantity}',
            f'TZS {plan.total_amount:,.0f}', f'TZS {paid:,.0f}', f'TZS {balance:,.0f}',
            escape(plan.get_status_display()),
        ])

    collection_rate = (paid_total / total_value * Decimal('100')) if total_value else Decimal('0')
    summary = [
        ('Branch', escape(pharmacy.name)),
        ('Report period', f'{start:%d %B %Y} — {end:%d %B %Y}'),
        ('Plans included', f'{len(plans):,}'),
        ('Total planned value', f'TZS {total_value:,.0f}'),
        ('Payments received', f'TZS {paid_total:,.0f}'),
        ('Refunds recorded', f'TZS {refund_total:,.0f}'),
        ('Outstanding customer balance', f'TZS {outstanding_total:,.0f}'),
        ('Collection rate (payments / plan value)', f'{collection_rate:.1f}%'),
        ('Units handed over', f'{handed_units:,}'),
        ('Units pending handover', f'{pending_units:,}'),
        ('Overdue active plans', f'{overdue_count:,}'),
    ]
    summary.extend((f'Plans — {label}', f'{count:,}') for label, count in status_counts.items() if count)
    from core.pdf_utils import generate_pdf_report
    from reportlab.lib.pagesizes import A4, landscape
    return generate_pdf_report(
        title='Layaway Analysis Report',
        subtitle=f'{escape(pharmacy.name)} — {start:%d %B %Y} to {end:%d %B %Y}',
        headers=['Reference', 'Created', 'Customer', 'Product', 'Qty', 'Handed', 'Plan value', 'Paid', 'Balance', 'Status'],
        rows=rows, summary=summary,
        filename=f'layaway_analysis_{start}_{end}.pdf',
        column_widths=[25, 20, 30, 30, 10, 15, 23, 22, 22, 25],
        pagesize=landscape(A4),
    )


@login_required
def plan_create(request):
    if not _allowed(request): raise PermissionDenied
    pharmacy=_branch(request)
    if request.method=='POST':
        locked=workday_sales_block_message(request.user)  # v97.18: closed workday locks new plans/deposits
        if locked:
            messages.error(request,locked); return redirect('layaway:list')
    form=LayawayCreateForm(request.POST or None,pharmacy=pharmacy)
    if request.method=='POST' and form.is_valid():
        plan=form.save(commit=False); plan.pharmacy=pharmacy; plan.created_by=request.user
        if plan.product.pharmacy_id != pharmacy.id: raise PermissionDenied
        if plan.quantity < 1 or plan.unit_price <= 0 or plan.deposit < 0 or plan.deposit > plan.unit_price*plan.quantity:
            form.add_error('deposit' if plan.deposit > plan.unit_price*plan.quantity else None,'Deposit cannot exceed the total amount, and price and quantity must be greater than zero.')
        else:
            dep_method=form.cleaned_data.get('deposit_method') or LayawayPayment.Method.CASH
            with transaction.atomic():
                plan.save(); LayawayAudit.objects.create(plan=plan,actor=request.user,action='CREATED',detail=f'Plan created. Initial deposit recorded: {plan.deposit} ({dep_method.title()}).')
                if plan.deposit > 0: LayawayPayment.objects.create(plan=plan,amount=plan.deposit,method=dep_method,transaction_reference=form.cleaned_data.get('deposit_reference') or '',received_by=request.user,note='Initial deposit recorded at plan creation')
                if plan.deposit == plan.total_amount:
                    # Paid in full on day one: it goes straight to the handover queue.
                    plan.status=LayawayPlan.Status.PAID_PENDING_HANDOVER; plan.completed_at=timezone.now(); plan.save(update_fields=['status','completed_at'])
                    LayawayAudit.objects.create(plan=plan,actor=request.user,action='FULLY_PAID',detail='Paid in full at opening. Ready for handover.')
            # Notify the customer of the starting outstanding balance using the existing SMS gateway.
            try:
                from .sms_service import send_plan_event, build_created_sms
                owner = request.user.business_owner() if callable(getattr(request.user, 'business_owner', None)) else request.user
                sms_text = build_created_sms(plan)
                ok, sms_state = send_plan_event(plan, 'CREATED', sms_text, owner=owner, event_key=f'plan:{plan.pk}:created')
                if not ok and sms_state == 'NO_CREDITS':
                    messages.warning(request, 'Plan created, but no Layaway SMS credits remain. Choose a Premium Messaging package to resume customer notifications.')
            except Exception:
                # SMS failure must never undo the plan or its recorded deposit.
                pass
            messages.success(request,f'Layaway {plan.reference} created. Its stock is reserved from normal POS sales; manager-approved overrides are available only for unpaid plans.')
            return redirect('layaway:detail',pk=plan.pk)
    return _render_list(request,pharmacy,form=form,open_create=True)

@login_required
def plan_detail(request,pk):
    if not _allowed(request): raise PermissionDenied
    plan=get_object_or_404(LayawayPlan.objects.select_related('product','pharmacy'),pk=pk,pharmacy=_branch(request))
    if request.method=='POST':
        locked=workday_sales_block_message(request.user)  # v97.18: closed workday locks payments/status changes
        if locked:
            messages.error(request,locked); return redirect('layaway:detail',pk=pk)
    form=LayawayPaymentForm(request.POST or None)
    # One-time token rendered with the form: a double tap or a browser retry re-sends the same token and is ignored.
    try: pay_key=uuid.UUID(request.POST.get('idem','')) if request.method=='POST' else uuid.uuid4()
    except ValueError: pay_key=uuid.uuid4()
    if request.method=='POST' and 'record_payment' in request.POST:
        if plan.status not in (LayawayPlan.Status.ACTIVE, LayawayPlan.Status.PARTIALLY_HANDED_OVER): messages.error(request,'Payments can only be recorded while a Layaway plan has an outstanding balance.')
        elif form.is_valid():
            try: amount=Decimal(form.cleaned_data['amount'])
            except (InvalidOperation,TypeError): amount=Decimal('0')
            with transaction.atomic():
                locked=LayawayPlan.objects.select_for_update().get(pk=plan.pk,pharmacy=_branch(request))
                if LayawayPayment.objects.filter(idempotency_key=pay_key).exists():
                    messages.info(request,'That payment was already recorded. Nothing was added twice.')
                    return redirect('layaway:detail',pk=plan.pk)
                if locked.status not in (LayawayPlan.Status.ACTIVE, LayawayPlan.Status.PARTIALLY_HANDED_OVER):
                    messages.error(request,'Payments can only be recorded while a Layaway plan has an outstanding balance.')
                    return redirect('layaway:detail',pk=plan.pk)
                if amount <= 0 or amount > locked.balance:
                    form.add_error('amount','Amount must be greater than zero and cannot exceed the outstanding balance.')
                else:
                    pay=form.save(commit=False); pay.plan=locked; pay.received_by=request.user; pay.idempotency_key=pay_key; pay.save()
                    LayawayAudit.objects.create(plan=locked,actor=request.user,action='PAYMENT_RECEIVED',detail=f'{amount} received via {pay.get_method_display()}.')
                    if locked.balance == 0:
                        locked.status=LayawayPlan.Status.PAID_PENDING_HANDOVER; locked.completed_at=timezone.now(); locked.save(update_fields=['status','completed_at'])
                        LayawayAudit.objects.create(plan=locked,actor=request.user,action='FULLY_PAID',detail='Balance reached zero. Official sale, cash ledger, receipt and product handover must be verified before marking Completed.')
                    # Send only after the payment transaction commits; provider latency
                    # must not hold the plan row lock or undo a valid payment.
                    try:
                        from .sms_service import send_plan_event, build_payment_sms, build_paid_sms
                        owner = request.user.business_owner() if callable(getattr(request.user, 'business_owner', None)) else request.user
                        event_type = 'PAID' if locked.balance == 0 else 'PAYMENT'
                        sms_text = build_paid_sms(locked, amount) if locked.balance == 0 else build_payment_sms(locked, amount)
                        event_key = f'plan:{locked.pk}:payment:{pay.pk}'
                        if locked.balance == 0:
                            event_key = f'plan:{locked.pk}:paid'
                        transaction.on_commit(lambda current_plan=locked, typ=event_type, text=sms_text, key=event_key, sms_owner=owner: send_plan_event(current_plan, typ, text, owner=sms_owner, event_key=key))
                    except Exception:
                        pass
                    messages.success(request,'Payment recorded successfully.' if locked.status==LayawayPlan.Status.ACTIVE else 'Layaway is fully paid. Use "Hand over product" when the customer collects it.')
                    return redirect('layaway:detail',pk=plan.pk)
    paid=plan.amount_paid
    pct=int(min(100,paid/plan.total_amount*100)) if plan.total_amount else 0
    days_left=(plan.due_date-timezone.localdate()).days if plan.due_date and plan.status in (LayawayPlan.Status.ACTIVE, LayawayPlan.Status.PARTIALLY_HANDED_OVER) else None
    stock=None
    if plan.status in services.RESERVING_STATUSES:
        res=services.reservation_for_product(plan.product)
        sellable=plan.product.sellable_quantity
        paid_net=max(Decimal('0'), plan.amount_paid-plan.amount_refunded)
        paid_units=int(paid_net // plan.unit_price) if plan.unit_price else 0
        eligible=max(0, paid_units-plan.quantity_handed_over)
        next_unit_payment=max(Decimal('0'), (Decimal(plan.quantity_handed_over + 1) * plan.unit_price) - paid_net)
        remaining=plan.quantity_remaining
        # The current plan may use its own reservation, but it must not consume
        # units promised to other customers. Show the quantity actually safe for
        # this plan, not merely the branch-wide stock total.
        other_reserved=max(0, int(res['qty']) - int(remaining))
        available_for_plan=max(0, int(sellable) - other_reserved)
        handover_max=min(remaining, eligible, available_for_plan)
        stock={'sellable':sellable,'reserved':res['qty'],'plans':res['plans'],
               'short':max(0,res['qty']-sellable),'enough':sellable>=remaining,
               'remaining':remaining,'eligible':eligible,'next_unit_payment':next_unit_payment,'handover_max':handover_max,
               'other_reserved':other_reserved,'available_for_plan':available_for_plan}
    cash_paid,other_paid=services.payment_split(plan)
    return render(request,'layaway/detail.html',{'is_manager':_is_manager(request.user),'stock':stock,'cash_paid':cash_paid,'other_paid':other_paid,'pay_key':pay_key,'plan':plan,'payment_form':form,'payments':plan.payments.select_related('received_by'),'handovers':plan.handovers.select_related('sale','handed_over_by'),'audit':plan.audit_events.select_related('actor'),'pct':pct,'days_left':days_left,'open_modal':'pay' if form.errors else ''})

@login_required
def resolve_plan(request,pk):
    """Flag a plan for review (any staff), or cancel / re-open it (managers only). A written reason is always required."""
    if not _allowed(request): raise PermissionDenied
    plan=get_object_or_404(LayawayPlan,pk=pk,pharmacy=_branch(request))
    if request.method!='POST': return redirect('layaway:detail',pk=pk)
    action=request.POST.get('action'); note=request.POST.get('note','').strip()
    if action not in ('needs_resolution','cancel','reopen'): raise PermissionDenied
    if action in ('cancel','reopen') and not _is_manager(request.user):
        messages.error(request,'Only a shop manager or owner can cancel or re-open a layaway plan. You can flag it for resolution instead.')
        return redirect('layaway:detail',pk=pk)
    if not note:
        messages.error(request,'Write the reason first. It is kept in the audit trail.')
        return redirect('layaway:detail',pk=pk)
    with transaction.atomic():
        plan=LayawayPlan.objects.select_for_update().get(pk=pk,pharmacy=_branch(request))
        if plan.status==LayawayPlan.Status.COMPLETED: messages.error(request,'This plan was already handed over. Corrections go through a sale return, not here.')
        elif plan.status==LayawayPlan.Status.CANCELLED: messages.error(request,'This plan is already cancelled.')
        elif action=='cancel' and plan.quantity_handed_over > 0:
            messages.error(request,'This plan has already had product handed over. Do not cancel/refund it as a whole; process any product return through the official Sale Return workflow, then resolve the remaining units with a manager.')
        elif action=='reopen':
            if plan.status!=LayawayPlan.Status.NEEDS_RESOLUTION: messages.error(request,'Only plans flagged for resolution can be re-opened.')
            else:
                if plan.reservation_released:
                    other_reserved = services.reserved_quantities(plan.pharmacy, [plan.product_id]).get(plan.product_id, {}).get('qty', 0)
                    available = max(0, plan.product.sellable_quantity - other_reserved)
                    if available < plan.quantity_remaining:
                        messages.error(request, f'Cannot re-open this reservation yet. Only {available} unreserved sellable unit(s) are available; this plan needs {plan.quantity_remaining} remaining unit(s). Restock or adjust the plan first.')
                        return redirect('layaway:detail',pk=pk)
                plan.status=(LayawayPlan.Status.COMPLETED if plan.quantity_remaining == 0 else LayawayPlan.Status.PAID_PENDING_HANDOVER if plan.balance==0 else LayawayPlan.Status.PARTIALLY_HANDED_OVER if plan.quantity_handed_over else LayawayPlan.Status.ACTIVE)
                plan.reservation_released=False
                plan.resolved_at=None; plan.resolved_by=None
                plan.save(update_fields=['status','reservation_released','resolved_at','resolved_by'])
                LayawayAudit.objects.create(plan=plan,actor=request.user,action='REOPENED',detail=note)
                messages.success(request,'Plan re-opened and its stock reservation restored.')
        else:
            paid=plan.amount_paid
            refund=Decimal('0'); refund_method=request.POST.get('refund_method') or LayawayPayment.Method.CASH
            if action=='cancel' and paid>0:
                if refund_method not in LayawayPayment.Method.values: refund_method=LayawayPayment.Method.CASH
                try: refund=Decimal(request.POST.get('refund_amount') or '0')
                except InvalidOperation: refund=Decimal('-1')
                if refund<0 or refund>paid-plan.amount_refunded:
                    messages.error(request,f'Refund must be between 0 and TZS {paid-plan.amount_refunded:,.0f} (what the customer has paid). Nothing was changed.')
                    return redirect('layaway:detail',pk=pk)
            plan.status=LayawayPlan.Status.NEEDS_RESOLUTION if action=='needs_resolution' else LayawayPlan.Status.CANCELLED
            plan.resolved_at=timezone.now(); plan.resolved_by=request.user; plan.resolution_note=note
            plan.save(update_fields=['status','resolved_at','resolved_by','resolution_note'])
            extra=''
            if action=='cancel' and paid>0:
                if refund>0:
                    LayawayRefund.objects.create(plan=plan,amount=refund,method=refund_method,note=note[:255],refunded_by=request.user)
                    extra=f' Refunded TZS {refund:,.0f} ({dict(LayawayPayment.Method.choices)[refund_method]}).'
                    if refund_method==LayawayPayment.Method.CASH: extra+=' The cash drawer has been reduced by this amount.'
                kept=paid-refund
                if kept>0: extra+=f' TZS {kept:,.0f} was kept by the shop (not refunded).'
            LayawayAudit.objects.create(plan=plan,actor=request.user,action=plan.status,detail=note+extra)
            messages.success(request,'Plan status updated.'+extra)
    return redirect('layaway:detail',pk=pk)

@login_required
def handover_plan(request,pk):
    """Record a full or payment-covered partial handover; the service rechecks stock and payments."""
    if not _allowed(request): raise PermissionDenied
    pharmacy=_branch(request)
    if request.method!='POST': return redirect('layaway:detail',pk=pk)
    locked=workday_sales_block_message(request.user)  # v97.18
    if locked:
        messages.error(request,locked); return redirect('layaway:detail',pk=pk)
    get_object_or_404(LayawayPlan,pk=pk,pharmacy=pharmacy)
    try:
        qty=request.POST.get('quantity') or None
        sale=services.hand_over(pk,request.user,pharmacy,quantity=qty,note=request.POST.get('note',''))
    except services.HandoverError as exc:
        messages.error(request,str(exc))
    else:
        try:
            from .sms_service import send_plan_event, build_handover_sms
            plan = LayawayPlan.objects.select_related('product', 'pharmacy').get(pk=pk, pharmacy=pharmacy)
            handover = plan.handovers.order_by('-handed_over_at', '-pk').first()
            owner = request.user.business_owner() if callable(getattr(request.user, 'business_owner', None)) else request.user
            complete = plan.status == LayawayPlan.Status.COMPLETED
            sms_text = build_handover_sms(plan, handover.quantity if handover else 0, complete)
            if handover:
                send_plan_event(plan, 'HANDOVER_COMPLETE' if complete else 'HANDOVER_PARTIAL', sms_text, owner=owner, event_key=f'plan:{plan.pk}:handover:{handover.pk}')
        except Exception:
            pass
        messages.success(request,f'Handover recorded. Sale #{sale.sale_number or sale.pk} created, stock reduced, and no payment was collected again. Remaining units stay on the Layaway plan.')
    return redirect('layaway:detail',pk=pk)

@login_required
def sms_packages(request):
    """Premium Layaway SMS packages are a manager/owner-only billing function."""
    if not _is_manager(request.user):
        raise PermissionDenied('Only a shop manager or owner can manage Layaway SMS packages.')
    pharmacy = _branch(request)
    from .models import LayawaySMSWallet, LayawaySMSPackage, LayawaySMSPurchase
    from django.utils import timezone
    wallet, _ = LayawaySMSWallet.objects.get_or_create(pharmacy=pharmacy)
    packages = LayawaySMSPackage.objects.filter(is_active=True)
    if request.method == 'POST':
        is_manager = request.user.is_superuser or (callable(getattr(request.user, 'is_management_user', None)) and request.user.is_management_user())
        if not is_manager:
            raise PermissionDenied('Only a shop manager/owner can request a Premium Messaging package.')
        package_id = request.POST.get('package_id')
        package = get_object_or_404(packages, pk=package_id)
        reference = request.POST.get('payment_reference', '').strip()[:150]
        note = request.POST.get('customer_note', '').strip()
        if not reference:
            messages.error(request, 'Enter your mobile-money payment reference before submitting the request.')
        else:
            LayawaySMSPurchase.objects.create(
                pharmacy=pharmacy, package=package,
                requested_by=getattr(request.user, 'business_owner', lambda: request.user)(),
                amount=package.price, payment_reference=reference, customer_note=note,
            )
            messages.success(request, 'Your package request has been submitted for payment verification. SMS credits are added only after approval.')
            return redirect('layaway:sms_packages')
    from payments.services import pesapal_usable
    from payments import abliner_client
    from hq.models import PaymentSettings
    payment_settings = PaymentSettings.load()
    online_ready = bool(pesapal_usable() or abliner_client.is_usable())
    package_list = list(packages)
    for pk in package_list:
        pk.per_sms = int(round(pk.price / pk.sms_credits)) if pk.sms_credits else 0
    purchases = LayawaySMSPurchase.objects.filter(pharmacy=pharmacy)[:10]
    return render(request, 'layaway/sms_packages.html', {
        'pharmacy': pharmacy, 'wallet': wallet, 'packages': package_list, 'online_ready': online_ready, 'payment_settings': payment_settings,
        'purchases': purchases, **_sms_ctx(pharmacy),
    })
