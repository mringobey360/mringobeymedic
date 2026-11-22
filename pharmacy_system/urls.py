from django.views.generic import TemplateView
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from core.landing_views import landing_view
from core.views import (
    service_worker_view, offline_view, server_unavailable_view, maintenance_view, health_view, readiness_view, service_status_view, data_protection_view, trigger_backup_view, download_user_backup_view, delete_user_backup_view, trigger_full_backup_view, favicon_view, robots_txt_view, sitemap_xml_view,
    custom_400, custom_404, custom_403, custom_500,
)

urlpatterns = [
    path('smartpos-preview/', TemplateView.as_view(template_name='public/smartpos_preview.html'), name='smartpos_preview'),
    path('', include('feedback.urls')),
    path('', include('testimonials.urls')),
    path('admin/', admin.site.urls),
    path('marketplace/', include('marketplace.urls')),
    path('favicon.ico', favicon_view, name='favicon'),
    path('robots.txt', robots_txt_view, name='robots_txt'),
    path('sitemap.xml', sitemap_xml_view, name='sitemap_xml'),
    path('sw.js', service_worker_view, name='service_worker'),
    path('offline/', offline_view, name='offline'),
    path('server-unavailable/', server_unavailable_view, name='server_unavailable'),
    path('health/', health_view, name='health'),
    path('ready/', readiness_view, name='readiness'),
    path('service-status/', service_status_view, name='service_status'),
    path('maintenance/', maintenance_view, name='maintenance'),
    path('data-protection/', data_protection_view, name='data_protection'),
    path('data-protection/backup/', trigger_backup_view, name='trigger_backup'),
    path('data-protection/backup/<int:pk>/download/', download_user_backup_view, name='download_user_backup'),
    path('data-protection/backup/<int:pk>/delete/', delete_user_backup_view, name='delete_user_backup'),
    path('data-protection/full-backup/', trigger_full_backup_view, name='trigger_full_backup'),
    # Public landing page for visitors; signed-in users still get the dashboard at '/'.
    path('', landing_view, name='landing'),
    path('', include('dashboard.urls')),
    path('', include('hq.urls')),
    path('', include('notifications.urls')),
    path('', include('customer_registry.urls')),
    path('', include('chat.urls')),
    path('', include('assistant.urls')),
    path('accounts/', include('accounts.urls')),
    path('inventory/', include('inventory.urls')),
    path('sales/', include('sales.urls')),
    path('layaway/', include('layaway.urls')),
    path('performance/', include('rewards.urls')),
    path('pharmacies/', include('pharmacies.urls')),
    path('sms/', include('sms_integration.urls')),
    path('payments/', include('payments.urls')),
    path('expenses/', include('expenses.urls')),
    path('daily-reports/', include('daily_reports.urls')),

]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / 'static')

admin.site.site_header = "SmartPOS Administration"
admin.site.site_title = "SmartPOS Admin"
admin.site.index_title = "System Administration"

# IMPORTANT: Django only looks up handler403/404/500 on the ROOT_URLCONF
# module (this file) — NOT on settings.py. Defining them in settings.py has
# no effect and silently falls back to Django's bare "<h1>403 Forbidden</h1>"
# text response. They must live here to actually render our branded
# templates/errors/403.html, 404.html and 500.html pages.
handler400 = custom_400
handler403 = custom_403
handler404 = custom_404
handler500 = custom_500
