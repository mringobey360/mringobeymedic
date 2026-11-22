import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.decorators import admin_required
from .forms import DailyReportSettingsForm
from .models import DailyReportSettings, DailySalesReport
from .pdf import generate_daily_sales_pdf
from .services import premium_daily_reports_enabled

logger = logging.getLogger(__name__)

@login_required
@admin_required
def settings_view(request):
    if not premium_daily_reports_enabled(request.user):
        return render(request, 'daily_reports/premium_locked.html', {'title': 'Premium Daily Sales Intelligence'})
    pref, _ = DailyReportSettings.objects.get_or_create(owner=request.user)
    if request.method == 'POST':
        form = DailyReportSettingsForm(request.POST, instance=pref)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.owner = request.user
            obj.save()
            messages.success(request, 'Daily Sales Intelligence settings saved. Delivery time uses Africa/Dar_es_Salaam.')
            return redirect('daily_reports:settings')
    else:
        form = DailyReportSettingsForm(instance=pref)
    return render(request, 'daily_reports/settings.html', {'form': form, 'pref': pref})

@login_required
def report_list(request):
    if not premium_daily_reports_enabled(request.user):
        raise Http404
    reports = DailySalesReport.objects.filter(owner=request.user).order_by('-report_date', '-id')
    page_obj = Paginator(reports, 15).get_page(request.GET.get('page'))
    return render(request, 'daily_reports/list.html', {'page_obj': page_obj})

def public_report(request, token):
    report = get_object_or_404(DailySalesReport.objects.select_related('owner'), access_token=token)
    if report.is_expired:
        return render(request, 'daily_reports/expired.html', {'report': report}, status=410)
    return render(request, 'daily_reports/report.html', {
        'report': report, 'snapshot': report.snapshot,
        'pdf_error': request.GET.get('pdf_error') == '1',
    })

def public_report_pdf(request, token):
    report = get_object_or_404(DailySalesReport.objects.select_related('owner'), access_token=token)
    if report.is_expired:
        raise Http404
    try:
        return generate_daily_sales_pdf(report)
    except Exception:
        # Never answer a download click with a 5xx: the service worker would swap
        # it for the "reconnecting" screen and the page would look like it just
        # refreshed. Log the real error and go back to the report with a clear notice.
        logger.exception('Daily Sales Intelligence PDF failed for report %s', report.pk)
        return redirect(f"{reverse('daily_reports:public_report', args=[report.access_token])}?pdf_error=1")
