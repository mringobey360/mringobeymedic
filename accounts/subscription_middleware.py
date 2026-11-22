from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.urls import reverse
from django.utils import timezone

from hq.models import Subscription


class SubscriptionLockMiddleware:
    """
    Enforce subscription access at the SERVER level.

    - Platform superadmins are never locked.
    - Public pages remain public.
    - A pharmacy owner needs an ACTIVE subscription whose end_date is today
      or later.
    - An employee inherits access from the subscription of the owner(s) who
      manage the employee's assigned pharmacy branch.
    - Expired/cancelled/missing subscriptions are redirected to one clear,
      dedicated renewal page.

    STAGED PER-BRANCH POLICY: when the active branch has its own billing state
    (see pharmacies.branch_billing) access is staged instead of abrupt:
    GRACE (works, warning) -> READ_ONLY (view only) -> LOCKED. Only the lapsed
    branch is affected; other branches of the same owner keep working. The owner
    can always reach billing and backup/export pages, even when locked.

    This is deliberately middleware-level protection rather than merely
    hiding navigation buttons, so direct URLs are protected as well.
    """

    EXEMPT_PATHS = None
    EXEMPT_PREFIXES = (
        '/static/',
        '/media/',
        '/favicon.ico',
        '/robots.txt',
        '/sitemap.xml',
        '/jiunge/',
        '/assistant/',  # help chatbot stays available (e.g. to ask how to renew)
        '/payments/',   # Pesapal payment flow must stay reachable even when the
                         # subscription itself is what's expired/pending (renewal
                         # payment, IPN callbacks, Pesapal's own return redirect).
    )

    def __init__(self, get_response):
        self.get_response = get_response

    @classmethod
    def _get_exempt_paths(cls):
        if cls.EXEMPT_PATHS is None:
            cls.EXEMPT_PATHS = {
                reverse('accounts:login'),
                reverse('accounts:logout'),
                reverse('accounts:subscription_expired'),
                reverse('accounts:request_renewal'),
                reverse('accounts:branch_subscription_expired'),
                # An owner may always erase their own account, even with an expired subscription.
                reverse('accounts:delete_account'),
                reverse('accounts:delete_account_verify'),
                reverse('hq:signup'),
            }
        return cls.EXEMPT_PATHS

    def _get_subscription(self, user, pharmacy):
        """Return the subscription that grants this user access, if any."""
        if user.is_superuser:
            return None

        if user.is_admin_role():
            try:
                return user.subscription
            except Subscription.DoesNotExist:
                return None

        if user.is_assistant_role():
            profile = getattr(user, 'assistant_profile', None)
            if not profile or not profile.active:
                return None
            try:
                manager_subscription = profile.manager.subscription
            except Subscription.DoesNotExist:
                return None
            return manager_subscription

        # Employees are tied to one branch. The branch is managed by the
        # pharmacy owner(s); an active subscription from a manager grants
        # access to staff working in that branch.
        if pharmacy is None:
            return None

        manager_ids = pharmacy.managers.values_list('pk', flat=True)
        return (
            Subscription.objects
            .filter(owner_id__in=manager_ids, status=Subscription.Status.ACTIVE)
            .select_related('owner')
            .order_by('-end_date')
            .first()
        )

    @staticmethod
    def _billing_exempt(path):
        """Pages that must stay reachable while a branch is read-only/locked, so it can always be renewed."""
        list_path = reverse('pharmacies:list')
        if path == list_path:
            return True
        if path.startswith(list_path):
            tail = path[len(list_path):].strip('/').split('/')
            if len(tail) == 2 and tail[1] in {'switch', 'payment'}:
                return True
        return path.startswith(reverse('hq:packages_center')) or path.startswith('/notifications/')

    @staticmethod
    def _owner_export_ok(request):
        """Owner may always GET backup/export pages, even when the branch is locked."""
        return (
            request.user.is_admin_role()
            and request.method in ('GET', 'HEAD')
            and request.path.startswith(reverse('data_protection'))
        )

    def _always_exempt(self, path):
        return path in self._get_exempt_paths() or any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES)

    def process_view(self, request, view_func, view_args, view_kwargs):
        """READ_ONLY stage: allow viewing, block anything that changes data."""
        state = getattr(request, 'branch_billing', None)
        if not state or not (state['read_only'] or state.get('view_only')) or request.method in ('GET', 'HEAD', 'OPTIONS'):
            return None
        if self._always_exempt(request.path) or self._billing_exempt(request.path):
            return None
        text = (f"{state['name']} is in view-only mode because its subscription expired. "
                f"You can still view data and reports; renew to record sales or make changes."
                if state.get('view_only') else
                f"{state['name']} is in read-only mode because its subscription ended. "
                f"You can still view data and reports; renew to record sales or make changes.")
        wants_json = (
            request.headers.get('x-requested-with') == 'XMLHttpRequest'
            or 'application/json' in request.headers.get('accept', '')
            or 'application/json' in (request.content_type or '')
        )
        if wants_json:
            return JsonResponse({'ok': False, 'code': 'branch_read_only', 'error': text}, status=403)
        messages.error(request, text)
        back = request.META.get('HTTP_REFERER', '')
        if not back or not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
            back = reverse('dashboard:home')
        return redirect(back)

    def __call__(self, request):
        if (not request.user.is_authenticated or request.user.is_superuser
                or getattr(request, 'impersonating', False)):   # platform owner inspecting a customer
            return self.get_response(request)

        if self._always_exempt(request.path):
            return self.get_response(request)

        pharmacy = getattr(request, 'pharmacy', None)

        # ---- Staged per-branch policy (only the active branch is judged) ----
        from pharmacies.branch_billing import branch_billing_state
        state = branch_billing_state(pharmacy) if pharmacy is not None else None
        if state is not None:
            request.branch_billing = state
            request.subscription = None  # the branch banner replaces the account banner
            if state['locked'] and not (self._billing_exempt(request.path) or self._owner_export_ok(request)):
                if state['kind'] == 'PRIMARY':
                    Subscription.objects.filter(
                        owner_id__in=pharmacy.managers.values_list('pk', flat=True),
                        status=Subscription.Status.ACTIVE, end_date__lt=timezone.localdate(),
                    ).update(status=Subscription.Status.EXPIRED)
                # A branch whose paid period simply lapsed stays OPEN IN VIEW-ONLY MODE for its management
                # (owner / manager assistant): they can still see data and reports and renew, and process_view
                # blocks every change. Deliberate locks (cancelled, expired by the platform admin, finished
                # free trial) and staff accounts keep the full lock.
                if state['forced'] or not request.user.is_management_user():
                    if state['kind'] == 'PRIMARY':
                        return redirect(reverse('accounts:subscription_expired'))
                    return redirect(reverse('accounts:branch_subscription_expired'))
                state['view_only'] = True
            return self.get_response(request)

        # ---- Legacy account-level check (no branch context / no subscription record) ----
        subscription = self._get_subscription(request.user, pharmacy)
        request.subscription = subscription

        if subscription is None:
            return redirect(reverse('accounts:subscription_expired'))

        # Both status and date are enforced. The end date is inclusive: a
        # subscription ending today remains usable until the next local day.
        if subscription.status != Subscription.Status.ACTIVE or subscription.end_date < timezone.localdate():
            if subscription.status == Subscription.Status.ACTIVE and subscription.end_date < timezone.localdate():
                Subscription.objects.filter(pk=subscription.pk).update(
                    status=Subscription.Status.EXPIRED
                )
            return redirect(reverse('accounts:subscription_expired'))

        return self.get_response(request)

