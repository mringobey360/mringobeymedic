"""System notices (in-app status banners). Platform superadmin only.

Workflow: write a DRAFT -> review the exact customer-facing preview -> publish.
Customers never see a draft. Editing a published notice sends it back to draft so the
new wording is reviewed before customers see it.
"""
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import User

from .models import SystemNotice
from .notice_forms import SystemNoticeForm
from .system_notices import notice_to_row
from .views import superadmin_required

RESOLVED_BANNER_HOURS = 6


def _reach(notice):
    """Rough number of active accounts that can see the notice once it is live."""
    try:
        qs = User.objects.filter(is_active=True)
        if notice.audience == SystemNotice.Audience.MANAGERS:
            qs = qs.filter(role__in=[User.Role.ADMIN, User.Role.ASSISTANT])
        return qs.count()
    except Exception:
        return None


@superadmin_required
def notice_list_view(request):
    qs = SystemNotice.objects.select_related('created_by')
    page_obj = Paginator(qs, 15).get_page(request.GET.get('page'))
    live_count = sum(1 for n in SystemNotice.objects.filter(is_active=True, publish_state=SystemNotice.Workflow.PUBLISHED) if n.status == 'LIVE')
    draft_count = SystemNotice.objects.filter(publish_state=SystemNotice.Workflow.DRAFT).count()
    return render(request, 'hq/notice_list.html', {'page_obj': page_obj, 'live_count': live_count, 'draft_count': draft_count})


def _after_save_redirect(request, notice):
    """"Save draft" returns to the list; "Review & publish" opens the review page."""
    if request.POST.get('action') == 'draft':
        messages.success(request, 'Draft saved. Customers cannot see it until you publish it.')
        return redirect('hq:notice_list')
    return redirect('hq:notice_review', pk=notice.pk)


@superadmin_required
@require_http_methods(['GET', 'POST'])
def notice_create_view(request):
    form = SystemNoticeForm(request.POST or None, initial={
        'kind': SystemNotice.Kind.INFO, 'audience': SystemNotice.Audience.ALL,
        'dismissible': True,
    })
    if request.method == 'POST' and form.is_valid():
        notice = form.save(commit=False)
        notice.created_by = request.user
        notice.publish_state = SystemNotice.Workflow.DRAFT
        notice.save()
        return _after_save_redirect(request, notice)
    return render(request, 'hq/notice_form.html', {'form': form, 'notice': None})


@superadmin_required
@require_http_methods(['GET', 'POST'])
def notice_edit_view(request, pk):
    notice = get_object_or_404(SystemNotice, pk=pk)
    was_published = notice.is_published
    form = SystemNoticeForm(request.POST or None, instance=notice)
    if request.method == 'POST' and form.is_valid():
        notice = form.save(commit=False)
        if was_published:
            # Changed wording must be reviewed again, so it leaves the customer screens until republished.
            notice.publish_state = SystemNotice.Workflow.DRAFT
        notice.save()
        if was_published:
            messages.warning(request, 'This notice was taken off customer screens while you edit it. Review it and publish it again.')
        return _after_save_redirect(request, notice)
    return render(request, 'hq/notice_form.html', {'form': form, 'notice': notice, 'was_published': was_published})


@superadmin_required
def notice_review_view(request, pk):
    """Shows exactly what customers will see, plus the checks, before anything goes live."""
    notice = get_object_or_404(SystemNotice, pk=pk)
    now = timezone.now()
    blocking, warnings = notice.review_checks(now)
    row = notice_to_row(notice)
    if not row['published_at']:
        row['published_at'] = now
    return render(request, 'hq/notice_review.html', {
        'notice': notice, 'row': row, 'blocking': blocking, 'warnings': warnings,
        'reach': _reach(notice), 'can_publish': not blocking and not notice.is_published,
    })


@superadmin_required
@require_POST
def notice_publish_view(request, pk):
    notice = get_object_or_404(SystemNotice, pk=pk)
    blocking, _warnings = notice.review_checks()
    if blocking:
        messages.error(request, 'Not published: ' + blocking[0])
        return redirect('hq:notice_review', pk=notice.pk)
    if notice.is_published:
        messages.info(request, 'This notice is already published.')
        return redirect('hq:notice_list')
    notice.publish(request.user)
    from .notice_delivery import send_notice_notifications
    sent = send_notice_notifications(notice)  # no-op if scheduled for later; the scheduler sends it at start time
    extra = f' Also sent to {sent} notification bell{"s" if sent != 1 else ""}.' if sent else ''
    messages.success(request, 'Published. Customers will see the banner within a few seconds.' + extra)
    return redirect('hq:notice_list')


@superadmin_required
@require_POST
def notice_unpublish_view(request, pk):
    notice = get_object_or_404(SystemNotice, pk=pk)
    notice.unpublish()
    messages.success(request, 'Notice moved back to drafts. Customers no longer see it.')
    return redirect('hq:notice_list')


@superadmin_required
@require_POST
def notice_toggle_view(request, pk):
    notice = get_object_or_404(SystemNotice, pk=pk)
    if not notice.is_published:
        messages.error(request, 'Only published notices can be switched on or off. Review and publish this draft first.')
        return redirect('hq:notice_review', pk=notice.pk)
    notice.is_active = not notice.is_active
    notice.save(update_fields=['is_active', 'updated_at'])
    messages.success(request, 'Notice is now ON.' if notice.is_active else 'Notice switched OFF. Customers no longer see it.')
    return redirect('hq:notice_list')


@superadmin_required
@require_POST
def notice_resolve_view(request, pk):
    """One click after an incident: turn it into a green "resolved" notice that hides itself."""
    notice = get_object_or_404(SystemNotice, pk=pk)
    if not notice.is_published:
        messages.error(request, 'Only a published notice can be marked as resolved.')
        return redirect('hq:notice_list')
    now = timezone.now()
    notice.kind = SystemNotice.Kind.RESOLVED
    notice.title = 'Tatizo limetatuliwa'
    notice.message = 'Huduma zimerejea kawaida. Asante kwa uvumilivu wako.'
    notice.is_active = True
    notice.dismissible = True
    notice.starts_at = now
    notice.ends_at = now + timedelta(hours=RESOLVED_BANNER_HOURS)
    notice.published_at = now
    notice.published_by = request.user
    notice.save()
    messages.success(request, f'Marked as resolved. It will hide itself after {RESOLVED_BANNER_HOURS} hours. Edit it if you want to add details.')
    return redirect('hq:notice_list')


@superadmin_required
@require_POST
def notice_delete_view(request, pk):
    notice = get_object_or_404(SystemNotice, pk=pk)
    notice.delete()
    messages.success(request, 'Notice deleted.')
    return redirect('hq:notice_list')


@superadmin_required
@require_POST
def notice_show_now_view(request, pk):
    """A published notice waiting for its start time: start showing it to customers right now."""
    notice = get_object_or_404(SystemNotice, pk=pk)
    if not notice.is_published or notice.status != 'SCHEDULED':
        messages.info(request, 'This notice is not waiting for a start time.')
        return redirect('hq:notice_list')
    notice.starts_at = timezone.now()
    notice.save(update_fields=['starts_at', 'updated_at'])
    from .notice_delivery import send_notice_notifications
    send_notice_notifications(notice)
    messages.success(request, 'Customers can see the banner now.')
    return redirect('hq:notice_list')
