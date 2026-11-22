from datetime import timedelta
from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Sum, F, DecimalField
from django.db.models.functions import Coalesce
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods
from accounts.decorators import admin_required, manager_required
from sales.models import Sale, SaleItem
from .models import RewardDecision, VIPPolicy
from .services import get_vip_policy

@login_required
def overview(request):
    branch = getattr(request, 'pharmacy', None)
    if branch is None:
        messages.warning(request, 'Choose an active branch to view customer loyalty and performance.')
        return redirect('dashboard')
    today = timezone.localdate(); start = today.replace(day=1); period = start.strftime('%Y-%m')
    completed = Sale.objects.filter(pharmacy=branch, status__in=[Sale.Status.COMPLETED, Sale.Status.PARTIALLY_RETURNED], business_date__gte=start, business_date__lte=today)
    staff = list(completed.values('served_by__id','served_by__username','served_by__first_name','served_by__last_name').annotate(sales=Count('id')).order_by('-sales')[:10])
    for row in staff:
        row['revenue'] = SaleItem.objects.filter(sale__in=completed.filter(served_by_id=row['served_by__id'])).aggregate(v=Coalesce(Sum(F('unit_price')*F('quantity'), output_field=DecimalField(max_digits=14,decimal_places=2)),Decimal('0')))['v']
    staff.sort(key=lambda r: (r['revenue'], r['sales']), reverse=True)
    products = SaleItem.objects.filter(sale__in=completed).values('product__name').annotate(quantity=Coalesce(Sum('quantity'),0), revenue=Coalesce(Sum(F('unit_price')*F('quantity'), output_field=DecimalField(max_digits=14,decimal_places=2)),Decimal('0'))).order_by('-quantity')[:10]
    policy = get_vip_policy(branch)
    window_start = today - timedelta(days=max(1, int(policy.window_days or 90)) - 1)
    customer_sales = Sale.objects.filter(pharmacy=branch, status__in=[Sale.Status.COMPLETED,Sale.Status.PARTIALLY_RETURNED], business_date__gte=window_start, business_date__lte=today, customer_contact__isnull=False).values('customer_contact_id','customer_contact__name','customer_contact__phone').annotate(visits=Count('id')).order_by('-visits')
    customer_rows = list(customer_sales)
    for c in customer_rows:
        c['spend'] = SaleItem.objects.filter(sale__pharmacy=branch,sale__customer_contact_id=c['customer_contact_id'],sale__business_date__gte=window_start,sale__business_date__lte=today,sale__status__in=[Sale.Status.COMPLETED, Sale.Status.PARTIALLY_RETURNED]).aggregate(v=Coalesce(Sum(F('unit_price')*F('quantity'),output_field=DecimalField(max_digits=14,decimal_places=2)),Decimal('0')))['v']
    min_spend = policy.minimum_spend or Decimal('0')
    vip = [c for c in customer_rows if policy.enabled and c['visits'] >= policy.qualifying_visits and (min_spend <= 0 or c['spend'] >= min_spend)]
    almost_vip = [c for c in customer_rows if 2 <= c['visits'] < policy.qualifying_visits or (policy.enabled and c['visits'] >= policy.qualifying_visits and min_spend > 0 and c['spend'] < min_spend)][:10]
    decisions = RewardDecision.objects.filter(pharmacy=branch, period=period).select_related('staff','approved_by','paid_by')
    return render(request,'rewards/overview.html',{'branch':branch,'staff':staff,'products':products,'vip':vip,'almost_vip':almost_vip,'decisions':decisions,'period':period,'window_start':window_start,'vip_discount':policy.discount_percent, 'vip_discount_label': (f'{policy.discount_percent}%' if policy.discount_type == 'PERCENT' else f'TZS {policy.discount_amount:,.0f} per unit'), 'vip_visit_threshold':policy.qualifying_visits, 'loyalty_window_days':policy.window_days, 'vip_policy':policy})

@manager_required
@require_http_methods(["GET", "POST"])
def vip_settings(request):
    """Manager-only branch-specific VIP policy editor with strict validation."""
    branch = getattr(request, 'pharmacy', None)
    if branch is None:
        messages.warning(request, 'Choose an active branch before configuring VIP rules.')
        return redirect('dashboard')
    policy = get_vip_policy(branch)
    if request.method == 'POST':
        try:
            visits = int(request.POST.get('qualifying_visits', '4'))
            days = int(request.POST.get('window_days', '90'))
            discount_type = request.POST.get('discount_type', 'PERCENT')
            rate = Decimal(request.POST.get('discount_percent', '5') or '0')
            amount = Decimal(request.POST.get('discount_amount', '0') or '0')
            spend = Decimal(request.POST.get('minimum_spend', '0') or '0')
            if not 1 <= visits <= 100: raise ValueError('VIP visits must be between 1 and 100.')
            if not 1 <= days <= 3650: raise ValueError('The loyalty window must be between 1 and 3650 days.')
            if discount_type not in ('PERCENT', 'AMOUNT'): raise ValueError('Choose percentage or fixed amount for the VIP discount.')
            if not Decimal('0') <= rate <= Decimal('100'): raise ValueError('VIP percentage must be between 0% and 100%.')
            if amount < 0 or amount > Decimal('100000000'): raise ValueError('Fixed VIP discount must be a valid non-negative TZS amount per unit.')
            if spend < 0 or spend > Decimal('100000000000'): raise ValueError('Minimum spend must be a valid non-negative amount.')
        except (ValueError, InvalidOperation):
            messages.error(request, 'Check visits (1–100), period (1–3650 days), discount type/value, and minimum spend (0 or more).')
            return render(request, 'rewards/vip_settings.html', {'branch': branch, 'policy': policy})
        policy.enabled = request.POST.get('enabled') == 'on'
        policy.qualifying_visits = visits
        policy.window_days = days
        policy.discount_type = discount_type
        policy.discount_percent = rate.quantize(Decimal('0.01'))
        policy.discount_amount = amount.quantize(Decimal('0.01'))
        policy.minimum_spend = spend.quantize(Decimal('0.01'))
        policy.updated_by = request.user
        policy.save()
        messages.success(request, 'VIP rules saved for this branch. POS and Customer Loyalty now use these settings.')
        return redirect('rewards:vip-settings')
    return render(request, 'rewards/vip_settings.html', {'branch': branch, 'policy': policy})


@admin_required
@require_POST
def propose_rewards(request):
    branch = getattr(request, 'pharmacy', None)
    if branch is None: return redirect('dashboard')
    period = timezone.localdate().strftime('%Y-%m')
    try:
        amounts = [Decimal(request.POST.get(f'rank_{rank}', default)) for rank, default in ((1,'20000'),(2,'10000'),(3,'5000'))]
        if any(a < 0 or a > Decimal('100000000') for a in amounts): raise InvalidOperation
    except (InvalidOperation, ValueError):
        messages.error(request, 'Enter valid non-negative reward amounts.')
        return redirect('rewards:overview')
    start = timezone.localdate().replace(day=1); end = timezone.localdate()
    sales = Sale.objects.filter(pharmacy=branch, business_date__gte=start, business_date__lte=end, status__in=[Sale.Status.COMPLETED,Sale.Status.PARTIALLY_RETURNED], served_by__isnull=False)
    rows = list(sales.values('served_by_id').annotate(revenue=Coalesce(Sum(F('items__unit_price')*F('items__quantity'), output_field=DecimalField(max_digits=14,decimal_places=2)),Decimal('0'),output_field=DecimalField(max_digits=14,decimal_places=2))).order_by('-revenue')[:3])
    if not rows:
        messages.warning(request, 'No completed sales found for this period.')
        return redirect('rewards:overview')
    with transaction.atomic():
        for rank, row in enumerate(rows, 1):
            RewardDecision.objects.update_or_create(pharmacy=branch,staff_id=row['served_by_id'],period=period,defaults={'rank':rank,'metric':'SALES','metric_value':row['revenue'] or 0,'proposed_amount':amounts[rank-1],'approved_amount':None,'approved_by':None,'approved_at':None,'status':'PROPOSED','reviewed_note':'','paid_at':None,'paid_by':None})
    messages.success(request, 'Reward proposals saved for manager review. No reward is paid automatically.')
    return redirect('rewards:overview')

@admin_required
@require_POST
def approve_reward(request, pk):
    branch = getattr(request,'pharmacy',None)
    with transaction.atomic():
        decision = get_object_or_404(RewardDecision.objects.select_for_update(),pk=pk,pharmacy=branch)
        if decision.status != 'PROPOSED': messages.info(request,'Only proposed rewards can be approved.')
        else:
            decision.approved_amount = decision.proposed_amount; decision.approved_by = request.user
            decision.approved_at = timezone.now(); decision.status = 'APPROVED'
            decision.reviewed_note = request.POST.get('note','')[:255]
            decision.save(update_fields=['approved_amount','approved_by','approved_at','status','reviewed_note'])
            messages.success(request,'Reward approved and recorded. Complete the real payment through payroll/cash control, then mark it paid.')
    return redirect('rewards:overview')

@admin_required
@require_POST
def reject_reward(request, pk):
    branch = getattr(request, 'pharmacy', None)
    with transaction.atomic():
        decision = get_object_or_404(RewardDecision.objects.select_for_update(), pk=pk, pharmacy=branch)
        if decision.status == 'PROPOSED':
            decision.status = 'REJECTED'
            decision.approved_by = request.user
            decision.approved_at = timezone.now()
            decision.reviewed_note = (request.POST.get('note') or 'Rejected by manager')[:255]
            decision.save(update_fields=['status', 'approved_by', 'approved_at', 'reviewed_note'])
            messages.success(request, 'Reward proposal rejected and audit note saved.')
        else:
            messages.info(request, 'Only proposed rewards can be rejected.')
    return redirect('rewards:overview')

@admin_required
@require_POST
def mark_reward_paid(request, pk):
    branch = getattr(request, 'pharmacy', None)
    with transaction.atomic():
        decision = get_object_or_404(RewardDecision.objects.select_for_update(), pk=pk, pharmacy=branch)
        if decision.status == 'APPROVED':
            decision.status = 'PAID'
            decision.paid_at = timezone.now()
            decision.paid_by = request.user
            note = (request.POST.get('note') or '').strip()
            if note:
                decision.reviewed_note = (decision.reviewed_note + ' | Payout: ' + note)[:255]
            decision.save(update_fields=['status', 'paid_at', 'paid_by', 'reviewed_note'])
            messages.success(request, 'Reward marked paid and audit trail updated. Verify the corresponding cash-control/payroll record exists.')
        else:
            messages.error(request, 'Only approved rewards can be marked paid.')
    return redirect('rewards:overview')
