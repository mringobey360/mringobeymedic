import os
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.files.storage import default_storage
from django.db import connection, transaction
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import get_template
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.decorators import admin_required
from core.backup_utils import USER_EXPORT_RETENTION, build_backup_zip, build_user_data_backup_zip, create_user_backup
from core.models import UserDataBackup


def _critical_page(template_name, status, context=None, headers=None):
    """Render a critical page without allowing the error page itself to fail.

    Critical responses intentionally avoid request context processors so a database,
    session, subscription, or tenant outage cannot cascade into another exception.
    If the template is missing or broken in a production image, a dependency-free
    inline fallback is returned instead.
    """
    from html import escape

    context = context or {}
    used_fallback = False

    try:
        html = get_template(template_name).render(context)
    except Exception:
        # Never raise from an error handler. A missing/broken template must not hide
        # the original exception or trigger recursive 500 responses/log flooding.
        used_fallback = True
        request_id = escape(str(context.get('request_id') or 'Unavailable'))
        safe_return_url = escape(str(context.get('safe_return_url') or '/accounts/login/'), quote=True)
        safe_return_label = escape(str(context.get('safe_return_label') or 'Sign in to SmartPOS'))
        is_maintenance = status == 503 and template_name == 'maintenance.html'
        title = 'Scheduled Maintenance' if is_maintenance else 'Temporary Service Issue'
        heading = (
            'SmartPOS is undergoing maintenance.'
            if is_maintenance
            else 'We could not complete that request.'
        )
        message = (
            'The service is temporarily unavailable while planned maintenance is in progress.'
            if is_maintenance
            else 'SmartPOS encountered an unexpected problem. Your saved business records are not deleted by this error.'
        )
        html = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<title>{title} — SmartPOS</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:#f8fafc;color:#0f172a;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
.card{{width:min(560px,100%);background:#fff;border:1px solid #e2e8f0;border-radius:24px;padding:36px;box-shadow:0 24px 70px rgba(15,23,42,.10);text-align:center}}
.mark{{width:64px;height:64px;margin:0 auto 18px;border-radius:20px;display:grid;place-items:center;background:#ecfdf5;color:#0f766e;font-size:30px;font-weight:900}}
h1{{margin:0 0 12px;font-size:28px;line-height:1.15;letter-spacing:-.03em}}p{{margin:0 auto;color:#64748b;line-height:1.7;max-width:450px}}
.actions{{display:flex;gap:12px;justify-content:center;margin:24px auto 0;max-width:460px}}a,button{{flex:1 1 0;max-width:220px;min-height:48px;display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:14px;padding:12px 20px;font:inherit;font-size:15px;font-weight:800;text-decoration:none;cursor:pointer;white-space:nowrap}}a{{background:#0f766e;color:#fff}}button{{background:#fff;color:#334155;border:1px solid #cbd5e1}}@media(max-width:560px){{.actions{{flex-direction:column;max-width:none}}a,button{{max-width:none;width:100%;min-height:52px;font-size:16px}}}}
.ref{{margin-top:22px;font-size:11px;color:#94a3b8}}.brand{{margin-top:16px;font-size:11px;color:#94a3b8;text-align:center}}
</style>
</head>
<body>
<main>
<section class="card">
<div class="mark">!</div>
<h1>{heading}</h1>
<p>{message}</p>
<div class="actions"><a href="{safe_return_url}">{safe_return_label}</a><button type="button" onclick="location.reload()">Try Again</button></div>
<div class="ref">Reference: {request_id}</div>
</section>
<div class="brand">SmartPOS · Built by Mutech Apex Innovations</div>
</main>
</body>
</html>'''

    response = HttpResponse(html, status=status, content_type='text/html; charset=utf-8')
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['X-Content-Type-Options'] = 'nosniff'
    response['X-Pharmacy-Branded-Error'] = '1'
    if used_fallback:
        response['X-Pharmacy-Error-Fallback'] = '1'
    for key, value in (headers or {}).items():
        response[key] = value
    return response

def custom_400(request, exception=None):
    return _critical_page('errors/400.html', 400)


_NO_REDIRECT_404_PREFIXES = ('/static/', '/media/', '/api/', '/.well-known/')


def custom_404(request, exception=None):
    """Unknown URL -> send the visitor somewhere useful instead of a dead 404 page.

    Only a URL that matches NO route at all (Resolver404) is redirected:
      * signed-out visitors  -> the login page
      * signed-in users      -> their dashboard
    A 404 raised by a real view (get_object_or_404, missing record) still shows
    the branded 404 page, so genuine "not found" cases are never hidden.
    Assets, APIs and non-GET requests keep a real 404 status.
    """
    from django.urls import Resolver404

    path = getattr(request, 'path', '') or ''
    last_segment = path.rstrip('/').rsplit('/', 1)[-1]
    if (
        isinstance(exception, Resolver404)
        and request.method in ('GET', 'HEAD')
        and not path.startswith(_NO_REDIRECT_404_PREFIXES)
        and '.' not in last_segment  # sw.js, *.png, *.map ... stay real 404s
    ):
        try:
            is_authenticated = bool(getattr(getattr(request, 'user', None), 'is_authenticated', False))
            return redirect('dashboard:home' if is_authenticated else 'accounts:login')
        except Exception:
            pass  # never let the error handler itself fail
    return _critical_page('errors/404.html', 404)


def custom_403(request, exception=None):
    # A notification may point to a route whose permissions changed after the
    # notification was created. Fail safely to the user's role-aware workspace
    # rather than leaving them on a 403 screen. The target view's authorization
    # is never bypassed.
    if request.GET.get('notification_click') == '1':
        from django.contrib import messages
        from django.shortcuts import redirect
        from django.urls import reverse
        is_authenticated = bool(getattr(getattr(request, 'user', None), 'is_authenticated', False))
        from notifications.routing import role_home_url
        target = role_home_url(request.user) if is_authenticated else reverse('accounts:login')
        if is_authenticated:
            messages.warning(request, 'That notification destination is not available to your current role. You have been returned to your workspace.')
        return redirect(target)
    # Never send users back to the forbidden URL: browser history would reopen
    # the same 403 page. Route anonymous visitors to login and authenticated
    # users to the role-aware dashboard, which chooses their permitted landing
    # page. Keep destinations server-generated (never trust a query-string URL).
    from django.urls import reverse

    is_authenticated = bool(getattr(getattr(request, 'user', None), 'is_authenticated', False))
    try:
        from notifications.routing import role_home_url
        safe_url = role_home_url(request.user) if is_authenticated else reverse('accounts:login')
        if safe_url == request.path:
            # Never offer a "safe" link that is the very page that was refused.
            safe_url = reverse('accounts:logout')
    except Exception:
        safe_url = '/' if is_authenticated else '/accounts/login/'

    context = {
        'safe_return_url': safe_url,
        'safe_return_label': 'Go to your workspace' if is_authenticated else 'Sign in to SmartPOS',
        'is_authenticated_user': is_authenticated,
    }
    # Do not expose raw permission exception details to customers.
    return _critical_page('errors/403.html', 403, context)


def custom_500(request):
    request_id = getattr(request, 'request_id', '')
    return _critical_page('errors/500.html', 500, {'request_id': request_id})

def service_worker_view(request):
    sw_path = os.path.join(settings.BASE_DIR, 'static', 'sw.js')
    try:
        with open(sw_path, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        content = ''
    response = HttpResponse(content, content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache'
    return response


def offline_view(request):
    return render(request, 'offline.html')


def server_unavailable_view(request):
    return _critical_page(
        'server_unavailable.html', 503,
        {'request_id': getattr(request, 'request_id', '')},
        {'Retry-After': '30'},
    )


def maintenance_view(request):
    return _critical_page(
        'maintenance.html', 503,
        {
            'message': getattr(settings, 'MAINTENANCE_MESSAGE', ''),
            'retry_seconds': getattr(settings, 'MAINTENANCE_RETRY_SECONDS', 60),
            'request_id': getattr(request, 'request_id', ''),
        },
        {'Retry-After': str(getattr(settings, 'MAINTENANCE_RETRY_SECONDS', 60)), 'X-Pharmacy-Maintenance': '1'},
    )


@require_http_methods(['GET'])
def health_view(request):
    """Fast liveness probe: confirms the Django web process can answer HTTP."""
    response = JsonResponse({'status': 'ok', 'service': 'pharmacy-manager', 'check': 'liveness'})
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


@require_http_methods(['GET'])
def readiness_view(request):
    """Readiness probe: confirms the application can reach its primary database."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        payload, status_code = {'status': 'ready', 'service': 'pharmacy-manager', 'database': 'ok'}, 200
    except Exception:
        payload, status_code = {'status': 'degraded', 'service': 'pharmacy-manager', 'database': 'unavailable'}, 503
    response = JsonResponse(payload, status=status_code)
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


@require_http_methods(['GET'])
def service_status_view(request):
    """Small browser-safe status endpoint used by recovery pages; never exposes exception details."""
    if getattr(settings, 'MAINTENANCE_MODE', False):
        state, code = 'maintenance', 503
    else:
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
            state, code = 'operational', 200
        except Exception:
            state, code = 'recovering', 503
    response = JsonResponse({'service': 'pharmacy-manager', 'status': state}, status=code)
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


@admin_required
def data_protection_view(request):
    backups = UserDataBackup.objects.filter(owner=request.user.business_owner())[:USER_EXPORT_RETENTION]
    total_records = sum(sum((v or 0) for v in (b.record_counts or {}).values()) for b in backups)
    return render(request, 'core/data_protection.html', {
        'backups': backups,
        'backup_retention': USER_EXPORT_RETENTION,
        'backup_total_records': total_records,
    })


@admin_required
@require_http_methods(['POST'])
def trigger_backup_view(request):
    """Create and persist a tenant-isolated, human-readable owner backup."""
    if request.user.is_superuser:
        messages.error(request, 'Use Full System Backup for the platform database.')
        return redirect('data_protection')
    if not request.user.accessible_management_pharmacies().exists():
        messages.warning(request, 'You do not have a branch to back up yet.')
        return redirect('data_protection')

    try:
        backup = create_user_backup(request.user)
        messages.success(request, f'Backup created successfully: {backup.filename}')
        return redirect('data_protection')
    except Exception as exc:
        messages.error(request, f'Backup could not be created. Please try again or contact support. ({exc})')
        return redirect('data_protection')


def io_bytes(data):
    import io
    return io.BytesIO(data)


@admin_required
def download_user_backup_view(request, pk):
    backup = get_object_or_404(UserDataBackup, pk=pk, owner=request.user.business_owner())
    try:
        file_obj = default_storage.open(backup.storage_path, 'rb')
    except (FileNotFoundError, OSError):
        raise Http404('This backup file is no longer available.')
    response = FileResponse(file_obj, as_attachment=True, filename=backup.filename, content_type='application/zip')
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@admin_required
@require_http_methods(['POST'])
def delete_user_backup_view(request, pk):
    backup = get_object_or_404(UserDataBackup, pk=pk, owner=request.user.business_owner())
    try:
        default_storage.delete(backup.storage_path)
    finally:
        backup.delete()
    messages.success(request, 'Backup deleted.')
    return redirect('data_protection')


def _superadmin_required(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied('Superadmin access required.')


@require_http_methods(['GET'])
def trigger_full_backup_view(request):
    _superadmin_required(request)
    filename, zip_bytes = build_backup_zip()
    response = HttpResponse(zip_bytes, content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def favicon_view(request):
    """Serve /favicon.ico (Google and browsers request this path by default)."""
    from django.http import FileResponse, Http404
    path = os.path.join(settings.BASE_DIR, 'static', 'favicon.ico')
    if not os.path.exists(path):
        raise Http404()
    resp = FileResponse(open(path, 'rb'), content_type='image/x-icon')
    resp['Cache-Control'] = 'public, max-age=86400'
    return resp


def robots_txt_view(request):
    """Crawl rules: public pages open, the application itself private, plus the sitemap address."""
    from .seo import robots_lines
    response = HttpResponse('\n'.join(robots_lines()) + '\n', content_type='text/plain')
    response['Cache-Control'] = 'public, max-age=3600'
    return response


def sitemap_xml_view(request):
    """/sitemap.xml - public pages plus published property listings (never fails the request)."""
    from .seo import sitemap_xml
    extra = []
    try:
        from marketplace.models import Listing
        from django.urls import reverse
        for slug, updated in Listing.objects.filter(status=Listing.Status.PUBLISHED).order_by('-updated_at').values_list('slug', 'updated_at')[:5000]:
            extra.append((reverse('marketplace:listing_detail', args=[slug]), updated.date().isoformat() if updated else None, '0.4', 'weekly'))
    except Exception:
        extra = []
    response = HttpResponse(sitemap_xml(extra), content_type='application/xml; charset=utf-8')
    response['Cache-Control'] = 'public, max-age=3600'
    return response
