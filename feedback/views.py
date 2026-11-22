import json
import time
from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Avg, Q as models_Q
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from accounts.models import User
from .models import Feedback

def _branch_for(user, request=None):
    active = getattr(request, "pharmacy", None) if request else None
    if active is not None:
        return active
    if getattr(user, 'pharmacy_id', None):
        return user.pharmacy
    try:
        return user.managed_pharmacies.order_by('name').first()
    except Exception:
        return None

def _can_review(user):
    return bool(user.is_superuser or getattr(user,'role','') in ('ADMIN','ASSISTANT'))

@login_required
def feedback_center(request):
    """One Feedback page for everybody: send feedback + see your own submissions.
    Managers get a tab that leads to the team inbox."""
    mine=Feedback.objects.filter(user=request.user).select_related('branch').prefetch_related('replies__author')
    context={'my_items':mine[:20],'my_total':mine.count(),'can_review':_can_review(request.user),
             'categories':Feedback.Category.choices,'ratings':Feedback.RATING_LABELS,'active_tab':'send'}
    return render(request,'feedback/center.html',context)

@login_required
@require_POST
def submit_feedback(request):
    recent = Feedback.objects.filter(user=request.user, created_at__gte=timezone.now()-timedelta(hours=1)).count()
    if recent >= 5:
        return JsonResponse({'ok':False,'message':'You have reached the feedback limit. Please try again later.'}, status=429)
    try:
        data=json.loads(request.body.decode('utf-8'))
        rating=int(data.get('rating',0))
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({'ok':False,'message':'Please select a rating.'},status=400)
    if rating not in range(1,6):
        return JsonResponse({'ok':False,'message':'Please select a rating.'},status=400)
    comment=str(data.get('comment','')).strip()[:500]
    category=str(data.get('category','USABILITY')).upper()
    allowed={x[0] for x in Feedback.Category.choices}
    if category not in allowed: category='USABILITY'
    # Capture technical context on the server where possible; never trust submitted role.
    branch=_branch_for(request.user, request)
    role=getattr(request.user,'get_role_display',lambda:'')()
    fb=Feedback.objects.create(user=request.user,branch=branch,rating=rating,category=category,comment=comment,
        page_url=str(data.get('page_url','')).split('?')[0][:500],page_section=str(data.get('page_section',''))[:80],
        app_version='V53',user_role=role,user_agent=request.META.get('HTTP_USER_AGENT','')[:500])

    # In-app notification for the platform System Administrator(s). The feedback's
    # created_at timestamp is intentionally not modified after creation.
    try:
        from notifications.models import Notification
        admins = User.objects.filter(is_superuser=True).exclude(pk=request.user.pk)
        branch_label = getattr(branch, 'name', '') or 'No branch selected'
        rating_label = fb.get_rating_display()
        snippet = (' — ' + fb.comment[:140]) if fb.comment else ''
        for admin in admins.iterator():
            Notification.objects.create(
                recipient=admin,
                pharmacy=branch,
                category='SYSTEM',
                level='INFO',
                title='New SmartPOS feedback',
                message=f'{request.user} submitted {rating_label.lower()} feedback ({fb.get_category_display()}) from {branch_label}{snippet}',
                link_url='/feedback/inbox/',
                dedupe_key=f'smartpos-feedback-{fb.pk}-admin-{admin.pk}',
            )
    except Exception:
        # Notification delivery must never undo a successfully saved feedback item.
        pass

    request.session['smartpos_feedback_last_prompt']=timezone.now().isoformat()
    request.session['spfb_due']=[time.time(), False]
    return JsonResponse({'ok':True,'id':fb.pk,'message':'Thank you! Your feedback has been received.',
        'item':{'id':fb.pk,'rating':fb.rating,'rating_label':fb.get_rating_display(),'category':fb.get_category_display(),
                'comment':fb.comment,'status':fb.get_status_display(),'created':timezone.localtime(fb.created_at).strftime('%d %b %Y, %H:%M')}})

@login_required
def feedback_inbox(request):
    """Feedback inbox: tenant-scoped, searchable, filterable, paginated. Only HQ can change status / reply."""
    user=request.user
    is_hq=bool(user.is_superuser)
    if not is_hq and getattr(user,'role','') not in ('ADMIN','ASSISTANT'):
        raise PermissionDenied
    base=Feedback.objects.select_related('user','branch').prefetch_related('replies__author')
    if not is_hq:
        branches=user.managed_pharmacies.all() if getattr(user,'role','') == 'ADMIN' else []
        if getattr(user,'role','') == 'ASSISTANT':
            try:
                branches=user.assistant_profile.accessible_pharmacies()
            except Exception:
                branches=[]
        if getattr(user,'pharmacy_id',None): branches=list(branches)+[user.pharmacy]
        base=base.filter(branch__in=branches)

    if request.method=='POST':
        if not is_hq: raise PermissionDenied
        fb=get_object_or_404(Feedback,pk=request.POST.get('feedback_id'))
        new_status=request.POST.get('status','')
        if new_status in dict(Feedback.Status.choices):
            was_resolved = fb.status == 'RESOLVED'
            fb.status=new_status; fb.save(update_fields=['status','updated_at'])
            messages.success(request, f'Feedback #{fb.pk} marked as {fb.get_status_display().lower()}.')
            if new_status=='RESOLVED' and not was_resolved:
                try:
                    from notifications.models import Notification
                    Notification.objects.create(recipient=fb.user, pharmacy=fb.branch, category='SYSTEM', level='SUCCESS', title='Feedback update', message='Your SmartPOS feedback has been marked as resolved.', link_url='/feedback/')
                except Exception:
                    pass
        return redirect(request.get_full_path())

    # Overall numbers for the summary strip (not affected by the filters below).
    overall=base.aggregate(total=Count('id'),average=Avg('rating'))
    overall_by_status={r['status']:r['n'] for r in base.values('status').annotate(n=Count('id'))}
    stats={'total':overall['total'],'average':overall['average'],
           'new':overall_by_status.get('NEW',0),'resolved':overall_by_status.get('RESOLVED',0)}

    # Filters (all validated). Status is applied last so the status chips can show counts.
    f={'q':request.GET.get('q','').strip()[:120],'rating':request.GET.get('rating',''),
       'category':request.GET.get('category',''),'status':request.GET.get('status',''),
       'branch':request.GET.get('branch',''),'date_from':request.GET.get('date_from',''),
       'date_to':request.GET.get('date_to','')}
    qs=base
    if f['rating'] in ('1','2','3','4','5'): qs=qs.filter(rating=int(f['rating']))
    else: f['rating']=''
    if f['category'] in dict(Feedback.Category.choices): qs=qs.filter(category=f['category'])
    else: f['category']=''
    if is_hq and f['branch'].isdigit(): qs=qs.filter(branch_id=int(f['branch']))
    else: f['branch']=''
    from datetime import datetime
    for key,lookup in (('date_from','created_at__date__gte'),('date_to','created_at__date__lte')):
        try:
            qs=qs.filter(**{lookup:datetime.strptime(f[key],'%Y-%m-%d').date()})
        except ValueError:
            f[key]=''
    if f['q']:
        qs=qs.filter(models_Q(comment__icontains=f['q'])|models_Q(user__username__icontains=f['q'])|
                     models_Q(user__first_name__icontains=f['q'])|models_Q(user__last_name__icontains=f['q'])|
                     models_Q(branch__name__icontains=f['q'])|models_Q(page_section__icontains=f['q'])).distinct()
    chip_counts={r['status']:r['n'] for r in qs.values('status').annotate(n=Count('id'))}
    status_chips=[{'value':v,'label':l,'count':chip_counts.get(v,0)} for v,l in Feedback.Status.choices]
    chips_total=sum(chip_counts.values())
    if f['status'] in dict(Feedback.Status.choices): qs=qs.filter(status=f['status'])
    else: f['status']=''
    page_obj=Paginator(qs,20).get_page(request.GET.get('page'))
    has_filters=any(f.values())
    context={'page_obj':page_obj,'feedback_items':page_obj.object_list,'stats':stats,'filters':f,'has_filters':has_filters,
        'status_chips':status_chips,'chips_total':chips_total,'can_review':True,'active_tab':'inbox','is_hq':is_hq,
        'statuses':Feedback.Status.choices,'categories':Feedback.Category.choices,'ratings':Feedback.RATING_LABELS,
        'branches':(Feedback.objects.exclude(branch__isnull=True).values('branch_id','branch__name').distinct().order_by('branch__name') if is_hq else [])}
    return render(request,'feedback/inbox.html',context)

# ---- Professional support desk and customer-visible feedback replies ----
def _notify_user(recipient, title, message, link, branch=None, level='INFO'):
    try:
        from notifications.models import Notification
        Notification.objects.create(recipient=recipient, pharmacy=branch, category='SYSTEM', level=level,
                                    title=title[:150], message=message[:1000], link_url=link[:300])
    except Exception:
        # Notifications are best-effort; never lose a saved support message because
        # an optional notification provider/table is temporarily unavailable.
        pass

def _support_staff(user):
    return bool(user.is_superuser or getattr(user, 'role', '') in ('ADMIN', 'ASSISTANT'))

def _support_queryset(user):
    from .models import SupportTicket
    qs = SupportTicket.objects.select_related('user', 'branch', 'assigned_to').prefetch_related('messages__author')
    if user.is_superuser:
        return qs
    if getattr(user, 'role', '') not in ('ADMIN', 'ASSISTANT'):
        return qs.filter(user=user)
    branches = []
    if getattr(user, 'role', '') == 'ADMIN':
        try: branches = list(user.managed_pharmacies.all())
        except Exception: branches = []
    else:
        try: branches = list(user.assistant_profile.accessible_pharmacies())
        except Exception: branches = []
    if getattr(user, 'pharmacy_id', None): branches.append(user.pharmacy)
    return qs.filter(models_Q(branch__in=branches) | models_Q(user=user)).distinct()

@login_required
def support_center(request):
    from .models import SupportTicket
    mine = SupportTicket.objects.filter(user=request.user).select_related('branch').prefetch_related('messages')
    context = {'tickets': mine[:50], 'can_manage_support': _support_staff(request.user), 'active_tab': 'support'}
    return render(request, 'feedback/support_center.html', context)

@login_required
@require_POST
def support_create(request):
    from .models import SupportTicket, SupportMessage
    subject = str(request.POST.get('subject', '')).strip()[:160]
    description = str(request.POST.get('description', '')).strip()[:5000]
    category = str(request.POST.get('category', 'TECHNICAL')).upper()
    allowed = {v for v, _ in SupportTicket.Category.choices}
    if not subject or len(subject) < 4 or not description or len(description) < 10 or category not in allowed:
        messages.error(request, 'Jaza kichwa na maelezo ya tatizo kwa usahihi (maelezo angalau herufi 10).')
        return redirect('feedback:support')
    branch = _branch_for(request.user, request)
    ticket = SupportTicket.objects.create(user=request.user, branch=branch, subject=subject, description=description,
        category=category, page_url=str(request.POST.get('page_url', '')).split('?')[0][:500], app_version='V52')
    SupportMessage.objects.create(ticket=ticket, author=request.user, body=description)
    # Notify HQ/support staff without making ticket creation depend on notifications.
    try:
        from accounts.models import User
        staff = User.objects.filter(is_superuser=True, is_active=True)
        if branch is not None:
            staff = (staff | User.objects.filter(role='ADMIN', managed_pharmacies=branch, is_active=True)).distinct()
        for recipient in staff[:20]:
            _notify_user(recipient, f'New support ticket {ticket.ticket_number}', subject, f'/feedback/support/{ticket.pk}/', branch, 'WARNING')
    except Exception:
        pass
    messages.success(request, f'Ticket {ticket.ticket_number} imefunguliwa. Timu ya support itakujibu hapa.')
    return redirect('feedback:support_detail', pk=ticket.pk)

@login_required
def support_detail(request, pk):
    from .models import SupportTicket
    ticket = get_object_or_404(_support_queryset(request.user), pk=pk)
    staff = _support_staff(request.user)
    # Internal notes are never sent to or shown to the customer.
    ticket_messages = ticket.messages.select_related('author')
    if not staff:
        ticket_messages = ticket_messages.filter(is_internal=False)
    return render(request, 'feedback/support_detail.html', {'ticket': ticket, 'ticket_messages': ticket_messages,
        'can_manage_support': staff, 'statuses': SupportTicket.Status.choices, 'priorities': SupportTicket.Priority.choices})

@login_required
@require_POST
def support_message(request, pk):
    from .models import SupportTicket, SupportMessage
    ticket = get_object_or_404(_support_queryset(request.user), pk=pk)
    staff = _support_staff(request.user)
    body = str(request.POST.get('body', '')).strip()[:5000]
    internal = staff and request.POST.get('is_internal') == '1'
    if len(body) < 2:
        messages.error(request, 'Andika ujumbe wenye maelezo kabla ya kutuma.')
        return redirect('feedback:support_detail', pk=pk)
    SupportMessage.objects.create(ticket=ticket, author=request.user, body=body, is_internal=internal)
    if not internal:
        if staff:
            ticket.status = 'WAITING_CUSTOMER'
            _notify_user(ticket.user, f'Jibu jipya: {ticket.ticket_number}', body[:220], f'/feedback/support/{ticket.pk}/', ticket.branch, 'INFO')
        else:
            ticket.status = 'OPEN'
            # Alert the owner and assigned support person.
            recipients = [u for u in (ticket.assigned_to,) if u]
            try:
                from accounts.models import User
                recipients += list(User.objects.filter(is_superuser=True, is_active=True)[:10])
            except Exception:
                pass
            for recipient in set(recipients):
                _notify_user(recipient, f'Customer replied: {ticket.ticket_number}', body[:220], f'/feedback/support/{ticket.pk}/', ticket.branch, 'INFO')
    ticket.save(update_fields=['status', 'updated_at'])
    messages.success(request, 'Ujumbe umetumwa.' if not internal else 'Internal note imehifadhiwa.')
    return redirect('feedback:support_detail', pk=pk)

@login_required
@require_POST
def support_update(request, pk):
    from .models import SupportTicket
    if not _support_staff(request.user):
        raise PermissionDenied
    ticket = get_object_or_404(_support_queryset(request.user), pk=pk)
    status = request.POST.get('status', '')
    priority = request.POST.get('priority', '')
    if status in dict(SupportTicket.Status.choices):
        ticket.status = status
        if _support_staff(request.user) and ticket.assigned_to_id is None:
            ticket.assigned_to = request.user
        if status in ('RESOLVED', 'CLOSED'):
            ticket.resolved_at = timezone.now()
            _notify_user(ticket.user, f'Ticket {ticket.ticket_number} updated', f'Status: {ticket.get_status_display()}. You can reply if the issue persists.', f'/feedback/support/{ticket.pk}/', ticket.branch, 'SUCCESS')
        else:
            ticket.resolved_at = None
    if priority in dict(SupportTicket.Priority.choices):
        ticket.priority = priority
    ticket.save(update_fields=['status', 'priority', 'resolved_at', 'assigned_to', 'updated_at'])
    messages.success(request, 'Ticket imeboreshwa.')
    return redirect('feedback:support_detail', pk=pk)

@login_required
@require_POST
def feedback_reply(request, pk):
    from .models import FeedbackReply
    if not request.user.is_superuser:
        raise PermissionDenied
    fb = get_object_or_404(Feedback.objects.select_related('user', 'branch'), pk=pk)
    body = str(request.POST.get('message', '')).strip()[:3000]
    internal = request.POST.get('is_internal') == '1'
    if len(body) < 2:
        messages.error(request, 'Andika jibu lenye maelezo.')
    else:
        FeedbackReply.objects.create(feedback=fb, author=request.user, message=body, is_internal=internal)
        if not internal:
            _notify_user(fb.user, f'Jibu la feedback #{fb.pk}', body[:220], '/feedback/', fb.branch, 'INFO')
            messages.success(request, 'Jibu limetumwa kwa mteja na notification imeandaliwa.')
        else:
            messages.success(request, 'Internal note imehifadhiwa; mteja hataiona.')
    return redirect(request.POST.get('next') or 'feedback:inbox')

@login_required
def support_inbox(request):
    from .models import SupportTicket
    if not _support_staff(request.user):
        raise PermissionDenied
    qs = _support_queryset(request.user)
    status = request.GET.get('status', '')
    priority = request.GET.get('priority', '')
    category = request.GET.get('category', '')
    query = request.GET.get('q', '').strip()[:120]
    if status in dict(SupportTicket.Status.choices): qs = qs.filter(status=status)
    if priority in dict(SupportTicket.Priority.choices): qs = qs.filter(priority=priority)
    if category in dict(SupportTicket.Category.choices): qs = qs.filter(category=category)
    # Search is applied only after _support_queryset has enforced the user's access scope.
    if query:
        qs = qs.filter(models_Q(ticket_number__icontains=query) | models_Q(subject__icontains=query) |
                       models_Q(user__username__icontains=query) | models_Q(user__first_name__icontains=query) |
                       models_Q(user__last_name__icontains=query)).distinct()
    stats = {'open': qs.exclude(status__in=['RESOLVED', 'CLOSED']).count(), 'urgent': qs.filter(priority='URGENT').exclude(status__in=['RESOLVED','CLOSED']).count(), 'resolved': qs.filter(status='RESOLVED').count(), 'total': qs.count()}
    return render(request, 'feedback/support_inbox.html', {'tickets': qs[:300], 'stats': stats,
        'statuses': SupportTicket.Status.choices, 'priorities': SupportTicket.Priority.choices,
        'categories': SupportTicket.Category.choices, 'query': query, 'can_review': True, 'active_tab': 'support_inbox'})
