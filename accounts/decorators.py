from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


# Central permission map for views that are shared with Manager Assistants.
# Managers always bypass these checks; assistants must have the matching
# permission on their ManagerAssistant profile.
VIEW_PERMISSION_OVERRIDES = {
    'dashboard_home_view': 'dashboard',
    'pos_view': 'sales',
    'sale_list_view': 'sales',
    'customer_debt_lookup_api': 'debts',
    'product_list_view': 'inventory',
    'expense_list': 'expenses',
    'expense_create': 'expenses',
    'inbox_view': 'chat',
    'open_direct_thread_view': 'chat',
    'broadcast_view': 'chat',
    'customer_list': 'customers',
    'customer_create': 'customers',
    'customer_edit': 'customers',
    'customer_detail': 'customers',
    'employee_list_view': 'employees',
    'employee_create_view': 'employees',
    'employee_edit_view': 'employees',
    'employee_toggle_active_view': 'employees',
    'employee_reset_pin_view': 'employees',
    'employee_compensation_view': 'employees',
    'meal_adjustment_create_view': 'employees',
    'meal_adjustment_delete_view': 'employees',
    'meal_timing_settings_view': 'employees',
    'attendance_presence_status_view': 'presence',
    'attendance_review_view': 'attendance',
    'attendance_exception_decide_view': 'attendance',
    'meal_issues_view': 'attendance',
    'meal_issue_decide_view': 'attendance',
    'meal_manual_grant_view': 'attendance',
    'meal_bulk_grant_view': 'attendance',
    'payroll_report_view': 'reports',
    'reports_view': 'reports',
    'custom_reports_view': 'reports',
    'export_custom_sales_report_pdf': 'reports',
    'export_custom_sales_report_excel': 'reports',
    'export_profit_comparison_pdf': 'reports',
    'export_sales_report_pdf': 'reports',
    'export_products_report_pdf': 'reports',
    'export_profit_report_pdf': 'reports',
    'sales_needing_review_view': 'sales',
    'mark_sale_reviewed_view': 'sales',
    'sale_return_decide_view': 'returns',
    'customer_toggle': 'customers',
    'pharmacy_list_view': 'branches',
    'pharmacy_edit_view': 'branches',
    'receipt_design_view': 'branches',
    'receipt_design_reset_view': 'branches',
    'receipt_design_sample_view': 'branches',
}

# These endpoints change ownership, billing, branches, backups or other
# manager-account controls and therefore remain Manager-only.
MANAGER_ONLY_NAMES = set()

def assistant_permission_for(view_func):
    name = getattr(view_func, '__name__', '')
    if name in VIEW_PERMISSION_OVERRIDES:
        return VIEW_PERMISSION_OVERRIDES[name]
    module = getattr(view_func, '__module__', '')
    if module.startswith('inventory.'):
        return 'inventory'
    if module.startswith('expenses.'):
        return 'expenses'
    if module.startswith('customer_registry.'):
        return 'customers'
    if module.startswith('chat.'):
        return 'chat'
    if module.startswith('notifications.'):
        return 'announcements'
    if module.startswith('dashboard.'):
        return 'dashboard' if name == 'dashboard_home_view' else 'reports'
    if module.startswith('sales.'):
        return 'sales'
    if module.startswith('accounts.'):
        if 'attendance' in name or 'presence' in name:
            return 'attendance' if 'presence' not in name else 'presence'
        return 'employees'
    return None

def _assistant_allowed(user, view_func):
    """Central server-side authorization for Manager Assistants.

    Active Assistants inherit the Manager's operational authority.  Platform
    superadmin/HQ-only views remain protected by their own decorators.
    """
    if user.is_superuser or user.is_manager_role():
        return True
    if not user.is_assistant_role() or not user.is_active:
        return False
    profile = getattr(user, 'assistant_profile', None)
    if not profile or not profile.active:
        return False
    return getattr(view_func, '__name__', '') not in MANAGER_ONLY_NAMES


def admin_required(view_func):
    """Manager-only or permission-granted Manager Assistant access."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not _assistant_allowed(request.user, view_func):
            raise PermissionDenied('Your Manager Assistant permissions do not allow this action.')
        return view_func(request, *args, **kwargs)
    _wrapped.access_policy = 'management'
    return _wrapped


def employee_or_admin_required(view_func):
    """Authenticated employees, managers, or permission-granted assistants."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if request.user.is_employee_role() or _assistant_allowed(request.user, view_func):
            return view_func(request, *args, **kwargs)
        raise PermissionDenied('Your account does not have access to this area.')
    _wrapped.access_policy = 'employee_or_management'
    return _wrapped


def manager_required(view_func):
    """Strict Manager/Supervisor access; assistants are deliberately excluded."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not (request.user.is_manager_role() or request.user.is_assistant_role() or request.user.is_superuser):
            raise PermissionDenied('Only the branch manager can perform this action.')
        return view_func(request, *args, **kwargs)
    _wrapped.access_policy = 'manager'
    return _wrapped


def branch_display_required(view_func):
    'Allow a branch-scoped attendance display account or a manager with normal branch access.'
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        user = request.user
        allowed = user.is_branch_display_role or user.is_manager_role() or user.is_assistant_role() or user.is_superuser
        if user.is_assistant_role() and user.has_assistant_permission('attendance'):
            allowed = True
        if allowed and getattr(request, 'pharmacy', None) is not None:
            return view_func(request, *args, **kwargs)
        raise PermissionDenied('This page is only available to an authorised branch attendance display.')
    _wrapped.access_policy = 'display_or_management'
    return _wrapped


def branch_display_only_required(view_func):
    """Strictly allow only a dedicated BRANCH_DISPLAY account on the kiosk."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        user = request.user
        if user.is_branch_display_role and getattr(request, 'pharmacy', None) is not None:
            return view_func(request, *args, **kwargs)
        raise PermissionDenied('This screen is reserved for the authorised branch attendance display device.')
    _wrapped.access_policy = 'display_only'
    return _wrapped


# ---------------------------------------------------------------------------
# Role-aware destination checks (used by notification routing)
# ---------------------------------------------------------------------------
# Views that a branch display account may reach (mirrors AssistantPermissionMiddleware).
DISPLAY_SAFE_VIEW_NAMES = {
    'attendance_qr_kiosk_view', 'attendance_qr_token_view', 'logout_view', 'change_password_view',
}


def user_can_access_view(user, view_func):
    """Return True when ``user`` passes the access decorator on ``view_func``.

    This mirrors the decorators above WITHOUT running the view, so a notification
    link can be checked before it is shown. Views without a policy tag (plain
    ``@login_required`` or custom checks) are treated as accessible; the real
    decorator/middleware remains the authority when the page is opened.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    name = getattr(view_func, '__name__', '')
    policy = getattr(view_func, 'access_policy', None)
    if user.is_branch_display_role:
        return policy in {'display_only', 'display_or_management'} or name in DISPLAY_SAFE_VIEW_NAMES
    if policy is None:
        return True
    if user.is_superuser:
        return policy != 'display_only'
    if policy == 'display_only':
        return False
    if policy == 'manager':
        return user.is_manager_role() or user.is_assistant_role()
    if policy == 'employee_or_management':
        return user.is_employee_role() or _assistant_allowed(user, view_func)
    # 'management' and 'display_or_management'
    return _assistant_allowed(user, view_func)
