import logging
import uuid

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import get_template


logger = logging.getLogger(__name__)


def is_maintenance_bypass_host(request):
    """True when the request arrived on one of the owner-only MAINTENANCE_BYPASS_HOSTS."""
    hosts = getattr(settings, 'MAINTENANCE_BYPASS_HOSTS', None)
    if not hosts:
        return False
    try:
        host = request.get_host().split(':')[0].lower()
    except Exception:          # DisallowedHost etc. -> not a bypass host
        return False
    return host in hosts


class MaintenanceOwnerOnlyMiddleware:
    """
    Runs after authentication. On the bypass domain during maintenance, anonymous
    visitors may reach the login page, but a logged-in user who is not a superuser
    gets the maintenance page — so the bypass domain is for the platform owner only.
    """
    ALWAYS_OK = ('/login', '/logout', '/accounts/login', '/accounts/logout', '/static/', '/media/', '/health/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (getattr(settings, 'MAINTENANCE_MODE', False) and is_maintenance_bypass_host(request)
                and not any(request.path.startswith(p) for p in self.ALWAYS_OK)
                and not request.path.startswith('/payments/ipn/')):
            user = getattr(request, 'user', None)
            if user is not None and user.is_authenticated and not user.is_superuser:
                return ServiceRecoveryMiddleware(self.get_response)._maintenance_response(request)
        return self.get_response(request)


class ServiceRecoveryMiddleware:
    """Infrastructure-safe recovery layer.

    Runs before sessions/authentication so maintenance mode can still render when
    the database or session store is unavailable. It also attaches a request ID
    to every response for support/log correlation without exposing internals.
    """

    EXEMPT_PREFIXES = (
        '/static/', '/media/', '/health/', '/ready/', '/service-status/',
        '/sw.js', '/offline/', '/server-unavailable/', '/maintenance/',
        # Pesapal's server-to-server notification must keep working so no payment is
        # missed while we are in maintenance (it only re-checks status, never shows a page).
        '/payments/ipn/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = (request.headers.get('X-Request-ID') or uuid.uuid4().hex[:12])[:64]

        if (getattr(settings, 'MAINTENANCE_MODE', False) and not self._is_exempt(request.path)
                and not is_maintenance_bypass_host(request)):
            response = self._maintenance_response(request)
        else:
            try:
                response = self.get_response(request)
            except Exception:
                logger.exception("Unhandled exception intercepted by ServiceRecoveryMiddleware", extra={"request_id": request.request_id})
                # Last-resort protection: if an exception escapes Django's normal
                # exception handler, return the same branded recovery experience
                # instead of leaking a traceback or infrastructure details.
                response = self._server_error_response(request)

        response['X-Request-ID'] = request.request_id
        if response.status_code >= 500:
            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return response

    def _server_error_response(self, request):
        context = {'request_id': request.request_id}
        html = get_template('errors/500.html').render(context)
        response = HttpResponse(html, status=500, content_type='text/html; charset=utf-8')
        response['X-Pharmacy-Branded-Error'] = '1'
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return response

    def _is_exempt(self, path):
        return any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES)

    def _maintenance_response(self, request):
        context = {
            'message': getattr(settings, 'MAINTENANCE_MESSAGE', ''),
            'retry_seconds': getattr(settings, 'MAINTENANCE_RETRY_SECONDS', 60),
            'request_id': request.request_id,
        }
        html = get_template('maintenance.html').render(context)
        response = HttpResponse(html, status=503, content_type='text/html; charset=utf-8')
        response['Retry-After'] = str(context['retry_seconds'])
        response['X-Pharmacy-Maintenance'] = '1'
        return response
