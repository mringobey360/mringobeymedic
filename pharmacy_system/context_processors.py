def site_settings(request):
    """Make basic site info and compact Tanzania-localized clock context available."""
    from django.conf import settings
    from django.utils import timezone
    now = timezone.localtime(timezone.now())
    hour = now.hour
    greeting = 'Good morning' if hour < 12 else ('Good afternoon' if hour < 18 else 'Good evening')
    context = {
        'SMARTPOS_NOW': now,
        'SMARTPOS_GREETING': greeting,
        'SITE_NAME': 'SmartPOS',
        'SMARTPOS_VERSION': getattr(settings, 'SMARTPOS_VERSION', 'V54.64'),
        # Customer-facing name of the online payment option (not the provider's name).
        'PAYMENT_BRAND_NAME': getattr(settings, 'PAYMENT_BRAND_NAME', 'SmartPOS Pay'),
        # Floating help widget: works for free out of the box using the offline
        # FAQ engine (assistant/offline.py, no API key needed). It only needs
        # ASSISTANT_ENABLED — see assistant/views.py for the AI vs offline choice.
        'ASSISTANT_ACTIVE': bool(getattr(settings, 'ASSISTANT_ENABLED', False)),
        # Customer care: Technical Support numbers shown in the widget + idle check-in delay.
        'ASSISTANT_SUPPORT_PHONES': list(getattr(settings, 'SUPPORT_PHONES', []) or []),
        'ASSISTANT_IDLE_SECONDS': int(getattr(settings, 'ASSISTANT_IDLE_SECONDS', 60) or 60),
        'SUPPORT_AGENT_NAME': getattr(settings, 'SUPPORT_AGENT_NAME', 'Mutech'),
        'SUPPORT_WHATSAPP_PHONE': getattr(settings, 'SUPPORT_WHATSAPP_PHONE', '+255610117824'),
        # Public support identity used by the WhatsApp support widget.
        # Configure SUPPORT_ADMIN_USERNAME when a specific administrator should
        # be shown; otherwise use the first active Shop Owner/Supervisor.
        'SUPPORT_ADMIN_USER': None,
    }
    # In-app status banners (improvements / maintenance / incidents) set by the platform owner.
    try:
        from hq.system_notices import notices_for_user
        context['system_notices'] = notices_for_user(getattr(request, 'user', None))
    except Exception:
        context['system_notices'] = []
    try:
        from accounts.models import User
        support_username = getattr(settings, 'SUPPORT_ADMIN_USERNAME', '') or ''
        qs = User.objects.filter(is_active=True, role=User.Role.ADMIN).order_by('id')
        if support_username:
            admin_user = qs.filter(username=support_username).first()
        else:
            admin_user = qs.first()
        if admin_user:
            context['SUPPORT_ADMIN_USER'] = admin_user
    except Exception:
        context['SUPPORT_ADMIN_USER'] = None
    try:
        _user = getattr(request, 'user', None)
        if _user is not None and _user.is_authenticated and _user.is_employee_role():
            _branch = getattr(request, 'pharmacy', None)
            if _branch:
                from pharmacies.branch_billing import branch_billing_state as _bbs
                _est = getattr(request, 'branch_billing', None) or _bbs(_branch)
                if _est and _est['alert']:
                    context['branch_billing_alert'] = _est
                from expenses.models import Expense
                context['pending_expenses_count'] = Expense.objects.filter(
                    pharmacy=_branch, submitted_by=_user, status=Expense.Status.PENDING
                ).count()
        if getattr(request, 'user', None) and request.user.is_authenticated and request.user.is_management_user():
            if request.user.is_assistant_role():
                profile = getattr(request.user, 'assistant_profile', None)
                context['managed_pharmacies'] = list(profile.accessible_pharmacies()) if profile and profile.active else []
                if profile and profile.active:
                    # UI is a projection of the same server-side operational
                    # policy.  Do not let a legacy permission snapshot hide a
                    # module that the Assistant can actually use.
                    context['assistant_permissions'] = {'__ALL__'} | {key for key, _label in profile.permission_labels().items()}
                else:
                    context['assistant_permissions'] = set()
                context['assistant_profile'] = profile
            else:
                context['managed_pharmacies'] = list(request.user.accessible_management_pharmacies().all().select_related('addon').order_by('name'))
            # Per-branch subscription awareness: each branch is judged only by its own
            # billing. The banner is about the ACTIVE branch; the switcher shows a small
            # marker on any branch that needs renewal.
            from pharmacies.branch_billing import branch_billing_state
            for _ph in context.get('managed_pharmacies') or []:
                try:
                    _ph.billing_state = branch_billing_state(_ph)
                except Exception:
                    _ph.billing_state = None
            _active = getattr(request, 'pharmacy', None)
            if _active is not None:
                _st = getattr(request, 'branch_billing', None) or branch_billing_state(_active)
                if _st and _st['alert']:
                    context['branch_billing_alert'] = _st
            pharmacy = getattr(request, 'pharmacy', None)
            if pharmacy:
                from sales.models import Sale
                context['needs_review_count'] = Sale.objects.filter(pharmacy=pharmacy, needs_review=True).count()
                from expenses.models import Expense
                context['pending_expenses_count'] = Expense.objects.filter(pharmacy=pharmacy, status=Expense.Status.PENDING).count()
                from sales.models import SaleReturnRequest
                context['pending_returns_count'] = SaleReturnRequest.objects.filter(
                    pharmacy=pharmacy, status=SaleReturnRequest.Status.PENDING
                ).count()
    except Exception:
        # Never let a broken DB/session take down error-page rendering itself.
        context['managed_pharmacies'] = []
        context['needs_review_count'] = 0
        context['pending_returns_count'] = 0
        context['pending_expenses_count'] = 0
    return context
