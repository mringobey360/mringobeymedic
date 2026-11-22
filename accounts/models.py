import datetime as _dt
import random
import string
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

phone_format_validator = RegexValidator(
    regex=r'^\+[0-9]{9,14}$',
    message="Phone number must be in international format, e.g. +255712345678."
)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Shop Owner'
        ASSISTANT = 'ASSISTANT', 'Manager Assistant'
        EMPLOYEE = 'EMPLOYEE', 'Shop Employee'
        BRANCH_DISPLAY = 'BRANCH_DISPLAY', 'Branch Attendance Display'

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE)
    phone_number = models.CharField(
        max_length=20, unique=True, blank=True, null=True, validators=[phone_format_validator],
        help_text="International format, e.g. +255712345678 (the app normalizes local 07... numbers automatically)."
    )
    must_change_password = models.BooleanField(
        default=False,
        help_text="If true, user is forced to set a new password on next login."
    )
    google_sub = models.CharField(
        max_length=64, unique=True, null=True, blank=True, db_index=True,
        help_text="Google account id (the 'sub' claim) linked for 'Continue with Google' sign-in. Shop Owners only.",
    )
    profile_photo = models.ImageField(upload_to='profile_photos/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Which attendance-verification methods this employee is shown and may use.
    # Chosen by the manager per employee (comma list of GPS, QR, MANUAL).
    attendance_methods = models.CharField(max_length=24, default='GPS,MANUAL')

    # Single-device sign-in (accounts/single_device.py): by default an account can be
    # signed in on ONE device at a time. The platform owner can exempt an account
    # (e.g. an owner who legitimately uses a phone and a PC) from Django admin.
    allow_multiple_devices = models.BooleanField(
        default=False,
        help_text="Exempt this account from the one-device-at-a-time sign-in rule.",
    )

    @property
    def role_title(self):
        """Exact job title shown in the UI: a Shop Owner is a Shop Owner, an Employee an Employee, etc."""
        if self.is_superuser:
            return 'Platform Administrator'
        return {
            self.Role.ADMIN: 'Shop Owner',
            self.Role.ASSISTANT: 'Manager Assistant',
            self.Role.EMPLOYEE: 'Shop Employee',
            self.Role.BRANCH_DISPLAY: 'Branch Display',
        }.get(self.role, self.role)

    def get_role_display(self):
        return self.role_title

    @property
    def is_branch_display_role(self):
        return self.role == self.Role.BRANCH_DISPLAY

    @property
    def allowed_attendance_methods(self):
        valid = {'GPS', 'QR', 'MANUAL'}
        return {m for m in (self.attendance_methods or '').split(',') if m in valid}

    # An Employee is assigned to exactly one pharmacy branch and only ever
    # operates within it. A Shop Owner/Supervisor can manage several branches
    # (see managed_pharmacies) and switches between them from the UI.
    pharmacy = models.ForeignKey(
        'pharmacies.Pharmacy', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='employees',
        help_text="The single branch this employee works at. Only applies to the Employee role."
    )
    managed_pharmacies = models.ManyToManyField(
        'pharmacies.Pharmacy', blank=True, related_name='managers',
        help_text="Branches this Shop Owner/Supervisor can view and manage. Only applies to the Admin role."
    )

    # Compensation — set by the Shop Owner for their own branch's employees.
    # Salary is fixed and completely independent from meals/sales. Meal amounts
    # are tracked by the separate MealLedger and only EARNED meals are counted
    # as operating cost; unpaid eligible meals become Meal Debt.
    monthly_salary = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Fixed monthly salary (TZS). Reference only — never auto-deducted from sales."
    )
    morning_meal_allowance = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Morning tea allowance (TZS/day). Tracked as an operating cost when the weekly meal policy grants it; never deducted from sales."
    )
    afternoon_meal_allowance = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Lunch allowance (TZS/day). Tracked as an operating cost when the weekly meal policy grants it; never deducted from sales."
    )
    evening_meal_allowance = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Dinner allowance (TZS/day). Tracked as an operating cost when the weekly meal policy grants it; never deducted from sales."
    )

    class WeekendMealSchedule(models.TextChoices):
        LUNCH_ONLY = 'LUNCH_ONLY', 'Lunch only'
        LUNCH_AND_DINNER = 'LUNCH_DINNER', 'Lunch + dinner'
        ALL_THREE = 'ALL_THREE', 'All three meals'

    weekend_meal_schedule = models.CharField(
        max_length=15, choices=WeekendMealSchedule.choices, default=WeekendMealSchedule.LUNCH_ONLY,
        help_text=(
            "Legacy fallback for Saturday & Sunday. New installations use the individual 7-day "
            "MealSchedule rows; this field is kept for backwards compatibility."
        )
    )

    REQUIRED_FIELDS = ['phone_number']

    def daily_meal_breakdown(self, for_date):
        """Return the payable breakfast/lunch/dinner amounts for one calendar date.

        Resolution order:
        1. A MealAdjustment for this exact employee/date, if present.
        2. The employee's WeeklyMealSchedule for that weekday.

        WeeklyMealSchedule is the single source of truth for normal weekly
        entitlements. The legacy weekend_meal_schedule field is retained only
        for database/backward-compatibility exports and is never used for new
        meal calculations. If no weekly row exists, no meal is silently granted.
        Dates before the employee account was created are always zero.
        """
        zero = Decimal('0')
        if for_date < self.created_at.date():
            return {'morning': zero, 'afternoon': zero, 'evening': zero}

        adjustment = self.meal_adjustments.filter(date=for_date).first()
        if adjustment is not None:
            return {
                'morning': self.morning_meal_allowance if adjustment.breakfast else zero,
                'afternoon': self.afternoon_meal_allowance if adjustment.lunch else zero,
                'evening': self.evening_meal_allowance if adjustment.dinner else zero,
            }

        schedule = self.meal_schedules.filter(weekday=for_date.weekday()).first()
        if schedule is not None:
            return {
                'morning': self.morning_meal_allowance if schedule.breakfast else zero,
                'afternoon': self.afternoon_meal_allowance if schedule.lunch else zero,
                'evening': self.evening_meal_allowance if schedule.dinner else zero,
            }

        # No implicit entitlement. Missing policy data is treated as no meal
        # entitlement rather than silently applying a legacy weekend rule.
        return {'morning': zero, 'afternoon': zero, 'evening': zero}

    def daily_meal_allowance(self, for_date):
        """Total meal money for this employee on for_date (sum of daily_meal_breakdown)."""
        breakdown = self.daily_meal_breakdown(for_date)
        return breakdown['morning'] + breakdown['afternoon'] + breakdown['evening']

    def save(self, *args, **kwargs):
        # Defense-in-depth: an Employee account must NEVER be able to reach
        # the Django Admin panel, no matter how the record was created or edited.
        if self.role in {self.Role.EMPLOYEE, self.Role.ASSISTANT, self.Role.BRANCH_DISPLAY}:
            self.is_staff = False
            self.is_superuser = False
        super().save(*args, **kwargs)

    def is_admin_role(self):
        return self.role == self.Role.ADMIN

    def is_manager_role(self):
        return self.role == self.Role.ADMIN

    def is_assistant_role(self):
        return self.role == self.Role.ASSISTANT

    def is_management_user(self):
        return self.role in {self.Role.ADMIN, self.Role.ASSISTANT}

    def business_owner(self):
        """Return the real business owner for management-scoped operations.

        A Manager Assistant is an operational delegate of the manager, so
        manager-owned subscriptions, branches, reports and billing context
        resolve to the assistant's manager. Platform superadmins remain their
        own authority and are handled separately.
        """
        if self.is_assistant_role():
            profile = getattr(self, 'assistant_profile', None)
            if profile and profile.active:
                return profile.manager
        return self

    def accessible_management_pharmacies(self):
        """Branches this management account is authorised to operate.

        Managers get their own active branches. Assistants inherit the same
        branch scope from their manager profile, dynamically.
        """
        if self.is_assistant_role():
            profile = getattr(self, 'assistant_profile', None)
            if profile and profile.active:
                return profile.accessible_pharmacies()
            return self.managed_pharmacies.none()
        return self.managed_pharmacies.filter(is_active=True).order_by('name')

    def has_assistant_permission(self, permission):
        """Return whether this account may perform an operational action.

        Manager Assistants are full operational delegates of their active
        manager.  The legacy JSON permission list is retained for backwards
        compatibility/audit display, but it is no longer a source of truth
        for an active Assistant.  This prevents old permission snapshots from
        silently blocking newly-added operational modules.
        """
        if self.is_admin_role() or self.is_superuser:
            return True
        if not self.is_assistant_role() or not self.is_active:
            return False
        profile = getattr(self, 'assistant_profile', None)
        return bool(profile and profile.active)

    def operational_owner(self):
        """Canonical business owner for operational data and billing context."""
        return self.business_owner()

    def is_employee_role(self):
        return self.role == self.Role.EMPLOYEE

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"


class ManagerAssistant(models.Model):
    """
    A separately authenticated assistant account delegated by one manager.

    Branch access is independent from feature permissions. The manager can
    either give the assistant all of the manager's currently accessible
    branches (dynamic) or explicitly select a subset. The assistant can never
    access a branch outside the manager's own managed_pharmacies set.
    """
    FULL_ACCESS_PERMISSION = '__ALL__'

    PERMISSION_CHOICES = (
        ('dashboard', 'Dashboard'),
        ('sales', 'Sales / POS'),
        ('inventory', 'Inventory'),
        ('customers', 'Customers'),
        ('debts', 'Debts'),
        ('returns', 'Returns & Voids'),
        ('expenses', 'Expenses & Cash Control'),
        ('employees', 'Employees'),
        ('attendance', 'Attendance'),
        ('presence', 'Workplace Monitoring'),
        ('chat', 'Chat'),
        ('announcements', 'Branch Announcements'),
        ('reports', 'Reports & Analytics'),
    )

    manager = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='manager_assistants',
        limit_choices_to={'role': User.Role.ADMIN},
    )
    class BranchAccessMode(models.TextChoices):
        ALL_MANAGER_BRANCHES = 'ALL', 'All Manager Branches'
        SELECTED_BRANCHES = 'SELECTED', 'Selected Branches'

    branch_access_mode = models.CharField(
        max_length=10, choices=BranchAccessMode.choices,
        default=BranchAccessMode.ALL_MANAGER_BRANCHES,
    )
    assistant = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='assistant_profile',
        limit_choices_to={'role': User.Role.ASSISTANT},
    )
    pharmacy = models.ForeignKey(
        'pharmacies.Pharmacy', on_delete=models.PROTECT, related_name='manager_assistants',
        help_text='Default branch used when the assistant first signs in. Actual access is controlled by branch_access_mode and branches.',
    )
    branches = models.ManyToManyField(
        'pharmacies.Pharmacy', blank=True, related_name='selected_manager_assistants',
        help_text='Only used when branch_access_mode is SELECTED.',
    )
    permissions = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='created_manager_assistants'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-active', '-created_at']

    @classmethod
    def permission_labels(cls):
        return dict(cls.PERMISSION_CHOICES)

    def has_permission(self, permission):
        # Full operational access is authoritative for active Assistants.
        # Keep the legacy permission JSON for compatibility and audit history,
        # but never let an old/stale list block an operational module.
        if self.active and getattr(self.assistant, 'is_active', False):
            return True
        return self.FULL_ACCESS_PERMISSION in (self.permissions or []) or permission in (self.permissions or [])

    def accessible_pharmacies(self):
        """Return the branches this assistant may currently access.

        ALL mode is intentionally dynamic: if the manager gains a branch the
        assistant gains it, and if the manager loses a branch the assistant
        loses it immediately. SELECTED mode is intersected with the manager's
        current branch set so stale M2M assignments can never leak access.
        """
        from pharmacies.models import Pharmacy, BranchAddOn
        manager_branches = self.manager.managed_pharmacies.filter(is_active=True).filter(
            models.Q(addon__isnull=True) | models.Q(addon__status=BranchAddOn.Status.ACTIVE)
        )
        if self.branch_access_mode == self.BranchAccessMode.ALL_MANAGER_BRANCHES:
            return manager_branches.distinct().order_by('name')
        return self.branches.filter(
            pk__in=manager_branches.values('pk'), is_active=True
        ).distinct().order_by('name')

    def can_access_pharmacy(self, pharmacy):
        return self.accessible_pharmacies().filter(pk=getattr(pharmacy, 'pk', pharmacy)).exists()

    def __str__(self):
        return f"{self.assistant} — Assistant of {self.manager} ({self.pharmacy})"


class ManagerAssistantAudit(models.Model):
    """Immutable audit trail for delegated Assistant administration."""
    class Action(models.TextChoices):
        CREATED = 'CREATED', 'Created'
        PERMISSIONS_CHANGED = 'PERMISSIONS_CHANGED', 'Permissions changed'
        BRANCH_SCOPE_CHANGED = 'BRANCH_SCOPE_CHANGED', 'Branch scope changed'
        ACTIVATED = 'ACTIVATED', 'Activated'
        DEACTIVATED = 'DEACTIVATED', 'Deactivated'

    profile = models.ForeignKey(ManagerAssistant, on_delete=models.CASCADE, related_name='audit_events')
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name='manager_assistant_audit_events')
    action = models.CharField(max_length=32, choices=Action.choices)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['profile', '-created_at']),
            models.Index(fields=['actor', '-created_at']),
        ]


class WeeklyMealSchedule(models.Model):
    """Default meal entitlement for one employee on one weekday (0=Monday..6=Sunday)."""
    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='meal_schedules')
    weekday = models.PositiveSmallIntegerField(choices=[
        (0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'),
        (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday'),
    ])
    breakfast = models.BooleanField(default=True)
    lunch = models.BooleanField(default=True)
    dinner = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['weekday']
        constraints = [
            models.UniqueConstraint(fields=['employee', 'weekday'], name='unique_employee_weekday_meal_schedule'),
        ]

    def __str__(self):
        return f"{self.employee} — {self.get_weekday_display()}"


class MealAdjustment(models.Model):
    """One-day override of the normal meal policy for an employee."""
    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='meal_adjustments')
    date = models.DateField()
    breakfast = models.BooleanField(default=False)
    lunch = models.BooleanField(default=False)
    dinner = models.BooleanField(default=False)
    reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_meal_adjustments')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-created_at']
        constraints = [
            models.UniqueConstraint(fields=['employee', 'date'], name='unique_employee_meal_adjustment_date'),
        ]

    def __str__(self):
        return f"{self.employee} — {self.date}"

class MealTimingSettings(models.Model):
    """Branch-level meal windows. Cut-offs remain the accounting eligibility boundary."""
    pharmacy = models.OneToOneField('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='meal_timing')
    breakfast_start = models.TimeField(default=_dt.time(7, 0))
    breakfast_cutoff = models.TimeField(default=_dt.time(10, 0))
    lunch_start = models.TimeField(default=_dt.time(12, 0))
    lunch_cutoff = models.TimeField(default=_dt.time(15, 0))
    dinner_start = models.TimeField(default=_dt.time(18, 0))
    dinner_cutoff = models.TimeField(default=_dt.time(21, 0))
    reminder_minutes_before = models.PositiveSmallIntegerField(default=0, help_text='Send the push reminder this many minutes before the meal window opens.')
    late_requests_per_week = models.PositiveSmallIntegerField('Late meal requests per week', default=3, help_text='Maximum late meal requests an employee may submit per week (0 turns late requests off).')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Meal timing — {self.pharmacy}"


class MealWorkday(models.Model):
    """Actual employee work window used to avoid charging meals before a late start."""
    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='meal_workdays')
    date = models.DateField()
    opened_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    opened_manually = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True)
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reopened_meal_workdays',
    )
    reopen_note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        constraints = [
            models.UniqueConstraint(fields=['employee', 'date'], name='unique_employee_meal_workday'),
        ]
        indexes = [models.Index(fields=['employee', '-date'], name='acc_mealwd_emp_date_idx')]

    @property
    def is_open(self):
        return self.closed_at is None

    def __str__(self):
        return f"{self.employee} — {self.date}"


class WorkdayReopenRequest(models.Model):
    """Employee request for manager approval to reopen an accidentally closed workday."""
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='workday_reopen_requests')
    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='workday_reopen_requests')
    work_date = models.DateField()
    reason = models.CharField(max_length=500)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='decided_workday_reopen_requests')
    decision_note = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ['-requested_at']
        indexes = [models.Index(fields=['pharmacy', 'status', '-requested_at'], name='acc_wdreq_ph_st_req_idx')]

    def __str__(self):
        return f'{self.employee} — {self.work_date} — {self.status}'


class MealLedger(models.Model):
    """Auditable meal entitlement/funding record, intentionally separate from salary."""
    class MealType(models.TextChoices):
        BREAKFAST = 'BREAKFAST', 'Breakfast / Tea'
        LUNCH = 'LUNCH', 'Lunch'
        DINNER = 'DINNER', 'Dinner'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        EARNED = 'EARNED', 'Earned / Funded'
        DEBT = 'DEBT', 'Meal Debt'
        WAIVED = 'WAIVED', 'Waived'
        MISSED = 'MISSED', 'Missed (not scanned)'

    class ClaimMethod(models.TextChoices):
        QR = 'QR', 'Scanned'
        MANUAL = 'MANUAL', 'Manually verified'

    class ManualReason(models.TextChoices):
        NETWORK_OUTAGE = 'NETWORK_OUTAGE', 'Network outage'
        POWER_OUTAGE = 'POWER_OUTAGE', 'Power outage'
        SYSTEM_OUTAGE = 'SYSTEM_OUTAGE', 'System unavailable'
        QR_UNAVAILABLE = 'QR_UNAVAILABLE', 'Branch QR unavailable'
        OTHER = 'OTHER', 'Other (note required)'

    class Source(models.TextChoices):
        WEEKLY = 'WEEKLY', 'Weekly policy'
        OVERRIDE = 'OVERRIDE', 'Date override'

    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='meal_ledger')
    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='meal_ledger')
    date = models.DateField()
    meal_type = models.CharField(max_length=12, choices=MealType.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cutoff_at = models.DateTimeField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.WEEKLY)
    reason = models.CharField(max_length=255, blank=True)
    earned_at = models.DateTimeField(null=True, blank=True)
    debt_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    # Scan-to-claim audit. A meal is only issued when it was claimed inside its
    # window: by scanning the branch QR, or by manager-verified exception.
    scan_required = models.BooleanField(default=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claim_method = models.CharField(max_length=10, choices=ClaimMethod.choices, blank=True)
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='verified_meal_claims')
    missed_at = models.DateTimeField(null=True, blank=True)
    scan_reopened_at = models.DateTimeField(null=True, blank=True)
    scan_reopened_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reopened_meal_scans')
    scan_reopen_note = models.CharField(max_length=500, blank=True)
    manual_reason = models.CharField(max_length=20, choices=ManualReason.choices, blank=True)
    manual_note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', 'cutoff_at']
        constraints = [
            models.UniqueConstraint(fields=['employee', 'date', 'meal_type'], name='unique_employee_meal_ledger_day_type'),
        ]
        indexes = [
            models.Index(fields=['pharmacy', '-date', 'status'], name='acc_mealled_ph_date_st_idx'),
            models.Index(fields=['employee', '-date', 'status'], name='acc_mealled_emp_date_st_idx'),
        ]

    def __str__(self):
        return f"{self.employee} — {self.date} — {self.get_meal_type_display()} — {self.get_status_display()}"


class AttendanceRecord(models.Model):
    class Status(models.TextChoices):
        VERIFIED = 'VERIFIED', 'Verified'
        REVIEW = 'REVIEW', 'Requires manager review'
        REJECTED = 'REJECTED', 'Rejected'
        CLOSED = 'CLOSED', 'Closed'

    class Method(models.TextChoices):
        GEOFENCE = 'GEOFENCE', 'GPS geofence'
        QR = 'QR', 'Dynamic branch QR'
        MANUAL = 'MANUAL', 'Manager approved'

    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='attendance_records')
    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='attendance_records')
    date = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REVIEW)
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.GEOFENCE)
    check_in_at = models.DateTimeField(null=True, blank=True)
    check_out_at = models.DateTimeField(null=True, blank=True)
    check_in_latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    check_in_longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    check_in_accuracy_m = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    check_in_distance_m = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    device_fingerprint = models.CharField(max_length=128, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name='reviewed_attendance_records')
    review_note = models.TextField(blank=True)
    class PresenceStatus(models.TextChoices):
        VERIFIED = 'VERIFIED', 'At workplace'
        OUTSIDE = 'OUTSIDE', 'Outside workplace'
        STALE = 'STALE', 'Location stale'
        UNAVAILABLE = 'UNAVAILABLE', 'Location unavailable'

    presence_status = models.CharField(max_length=12, choices=PresenceStatus.choices, default=PresenceStatus.VERIFIED)
    last_presence_at = models.DateTimeField(null=True, blank=True)
    last_presence_distance_m = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    last_presence_accuracy_m = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    outside_since = models.DateTimeField(null=True, blank=True)
    outside_alert_sent_at = models.DateTimeField(null=True, blank=True)
    presence_check_count = models.PositiveIntegerField(default=0)
    # Owner SMS bookkeeping: set once the alert for that event went out, so a retry,
    # a double scan or a re-opened workday can never message the boss twice.
    check_in_sms_sent_at = models.DateTimeField(null=True, blank=True)
    check_out_sms_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-check_in_at']
        constraints = [models.UniqueConstraint(fields=['employee', 'date'], name='unique_employee_attendance_day')]
        indexes = [models.Index(fields=['pharmacy', '-date', 'status'], name='accounts_atte_pharmac_idx'), models.Index(fields=['employee', '-date'], name='accounts_atte_employe_idx')]

    @property
    def is_verified(self):
        return self.status in {self.Status.VERIFIED, self.Status.CLOSED}

    @property
    def effective_presence_status(self):
        if not self.last_presence_at:
            return self.PresenceStatus.UNAVAILABLE
        interval = max(5, min(int(getattr(self.pharmacy, 'presence_check_interval_minutes', 15) or 15), 60))
        stale_after = timedelta(minutes=max(30, interval * 2))
        if timezone.now() - self.last_presence_at > stale_after:
            return self.PresenceStatus.STALE
        return self.presence_status

    @property
    def effective_presence_label(self):
        return dict(self.PresenceStatus.choices).get(self.effective_presence_status, 'Unknown')

    def __str__(self):
        return f'{self.employee} — {self.date} — {self.get_status_display()}'


class AttendanceCheckpoint(models.Model):
    class Type(models.TextChoices):
        BREAKFAST = 'BREAKFAST', 'Breakfast verification'
        LUNCH = 'LUNCH', 'Lunch verification'
        DINNER = 'DINNER', 'Dinner verification'
        CHECKOUT = 'CHECKOUT', 'End-of-shift check-out'

    class Method(models.TextChoices):
        QR = 'QR', 'Dynamic branch QR'
        GPS = 'GEOFENCE', 'GPS geofence'
        MANUAL = 'MANUAL', 'Manager approved'

    attendance = models.ForeignKey(AttendanceRecord, on_delete=models.CASCADE, related_name='checkpoints')
    checkpoint_type = models.CharField(max_length=12, choices=Type.choices)
    scanned_at = models.DateTimeField(auto_now_add=True)
    method = models.CharField(max_length=10, choices=Method.choices)
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    accuracy_m = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_fingerprint = models.CharField(max_length=128, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-scanned_at']
        constraints = [
            models.UniqueConstraint(
                fields=['attendance', 'checkpoint_type'],
                name='unique_attendance_checkpoint_type',
            ),
        ]
        indexes = [
            models.Index(fields=['attendance', '-scanned_at'], name='accounts_atte_checkpt_idx'),
        ]

    def __str__(self):
        return f'{self.attendance.employee} — {self.get_checkpoint_type_display()} — {self.scanned_at:%d %b %Y %H:%M}'


class AttendanceVerification(models.Model):
    class Result(models.TextChoices):
        VERIFIED = 'VERIFIED', 'Verified'
        OUTSIDE = 'OUTSIDE', 'Outside geofence'
        INACCURATE = 'INACCURATE', 'Location inaccurate'
        UNAVAILABLE = 'UNAVAILABLE', 'Location unavailable'
        REVIEW = 'REVIEW', 'Sent for review'

    attendance = models.ForeignKey(AttendanceRecord, on_delete=models.CASCADE, related_name='verifications')
    checked_at = models.DateTimeField(auto_now_add=True)
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    accuracy_m = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    distance_m = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    result = models.CharField(max_length=12, choices=Result.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_fingerprint = models.CharField(max_length=128, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-checked_at']
        indexes = [models.Index(fields=['attendance', '-checked_at'], name='accounts_atte_attenda_idx')]


class AttendanceExceptionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending review'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='attendance_exception_requests')
    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='attendance_exception_requests')
    date = models.DateField()
    requested_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name='decided_attendance_exceptions')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']
        constraints = [models.UniqueConstraint(fields=['employee', 'date'], name='unique_attendance_exception_day')]

    def __str__(self):
        return f'{self.employee} — {self.date} — {self.get_status_display()}'


class MealIssueReport(models.Model):
    """Exception path when the employee cannot scan. Reporting never issues a meal by itself."""
    class Reason(models.TextChoices):
        QR_NOT_SCANNING = 'QR_NOT_SCANNING', 'QR not scanning'
        CAMERA_UNAVAILABLE = 'CAMERA_UNAVAILABLE', 'Camera unavailable'
        NETWORK_PROBLEM = 'NETWORK_PROBLEM', 'Network problem'
        QR_EXPIRED = 'QR_EXPIRED', 'QR expired'
        DEVICE_PROBLEM = 'DEVICE_PROBLEM', 'Device problem'
        BUSY_AT_WORK = 'BUSY_AT_WORK', 'Busy at work (late request)'
        OTHER = 'OTHER', 'Other'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending verification'
        SCAN_REOPENED = 'SCAN_REOPENED', 'Reopened for employee scan'
        APPROVED = 'APPROVED', 'Manually verified'
        RETURNED = 'RETURNED', 'Returned to employee'
        REJECTED = 'REJECTED', 'Rejected'
        RESOLVED = 'RESOLVED', 'Resolved by scan'

    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='meal_issue_reports')
    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='meal_issue_reports')
    date = models.DateField()
    meal_type = models.CharField(max_length=12, choices=MealLedger.MealType.choices)
    window_start_at = models.DateTimeField()
    window_end_at = models.DateTimeField()
    reason = models.CharField(max_length=24, choices=Reason.choices)
    details = models.TextField(blank=True)
    is_late = models.BooleanField(default=False, help_text='Submitted after the meal window ended. Needs manager approval and counts against the weekly limit.')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reported_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='decided_meal_issues')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    return_count = models.PositiveSmallIntegerField(default=0)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-reported_at']
        constraints = [
            models.UniqueConstraint(fields=['employee', 'date', 'meal_type'], name='unique_meal_issue_day_type'),
        ]
        indexes = [
            models.Index(fields=['pharmacy', 'status', '-reported_at'], name='acc_mealissue_ph_st_idx'),
        ]

    @property
    def reference(self):
        return f'MI-{self.pk:06d}' if self.pk else 'MI-new'

    def __str__(self):
        return f'{self.reference} — {self.employee} — {self.date} — {self.get_meal_type_display()} — {self.get_status_display()}'


def generate_otp_code():
    return ''.join(random.choices(string.digits, k=6))


class OTP(models.Model):
    """One-time password used for password reset via SMS."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='otps')
    code = models.CharField(max_length=6, default=generate_otp_code)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    def is_valid(self):
        from django.conf import settings
        expiry = self.created_at + timedelta(minutes=settings.OTP_VALID_MINUTES)
        return (not self.is_used) and timezone.now() <= expiry

    def __str__(self):
        return f"OTP for {self.user.username} - {self.code}"


class AccountDeletionRequest(models.Model):
    """One-time SMS code that authorises PERMANENT deletion of a manager account.

    Only a keyed hash of the code is stored (never the code itself), each code
    has a short life, a limited number of attempts and can be used once.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='deletion_requests')
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed = models.BooleanField(default=False)
    requested_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', '-created_at'], name='acc_delreq_user_created_idx')]

    def __str__(self):
        return f"Account deletion code for {self.user_id} at {self.created_at:%Y-%m-%d %H:%M}"


class PushSubscription(models.Model):
    """Browser Web Push subscription for an authenticated user/device."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='push_subscriptions')
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    user_agent = models.CharField(max_length=500, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ['-updated_at']
        indexes = [models.Index(fields=['user', 'active'])]


class MealReminderDelivery(models.Model):
    """Idempotency record: one scheduled reminder per branch/date/meal."""
    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='meal_reminder_deliveries')
    date = models.DateField()
    meal_type = models.CharField(max_length=12)
    scheduled_for = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['pharmacy', 'date', 'meal_type'], name='unique_meal_reminder_delivery')]
        indexes = [models.Index(fields=['scheduled_for'])]


class PayrollRun(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        APPROVED = 'APPROVED', 'Approved'
        PAID = 'PAID', 'Paid'

    pharmacy = models.ForeignKey('pharmacies.Pharmacy', on_delete=models.CASCADE, related_name='payroll_runs')
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_payroll_runs')
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name='approved_payroll_runs')
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name='paid_payroll_runs')
    paid_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['-period_start', '-created_at']
        constraints = [
            models.UniqueConstraint(fields=['pharmacy', 'period_start', 'period_end'], name='unique_payroll_period_branch'),
        ]

    @property
    def total_gross(self):
        return sum((x.gross_pay for x in self.items.all()), Decimal('0'))

    @property
    def total_deductions(self):
        return sum((x.total_deductions for x in self.items.all()), Decimal('0'))

    @property
    def total_net(self):
        return sum((x.net_pay for x in self.items.all()), Decimal('0'))

    def __str__(self):
        return f'{self.pharmacy} — {self.period_start} to {self.period_end}'


class PayrollItem(models.Model):
    payroll = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name='items')
    employee = models.ForeignKey(User, on_delete=models.PROTECT, related_name='payroll_items')
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    earned_meals = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_allowances = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    working_days = models.PositiveIntegerField(default=0)
    attendance_days = models.PositiveIntegerField(default=0)
    note = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['payroll', 'employee'], name='unique_payroll_employee')]
        ordering = ['employee__first_name', 'employee__last_name', 'employee__username']

    @property
    def gross_pay(self):
        return (self.basic_salary or Decimal('0')) + (self.other_allowances or Decimal('0'))

    @property
    def total_deductions(self):
        return (self.tax_deduction or Decimal('0')) + (self.other_deductions or Decimal('0'))

    @property
    def net_pay(self):
        return max(Decimal('0'), self.gross_pay - self.total_deductions)

    def __str__(self):
        return f'{self.employee} — {self.payroll}'



class UserPin(models.Model):
    """The quick-sign-in PIN of a user (one per user, stored hashed).

    The PIN is only ever accepted together with a trusted-device cookie
    (see TrustedDevice). Wrong attempts use progressive cooldowns: attempt 3
    pauses for 30 seconds, attempt 5 pauses for 5 minutes, and the next wrong
    attempt permanently locks the PIN until password authentication recreates it.
    """
    MAX_FAILED_ATTEMPTS = 5

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='device_pin')
    pin_hash = models.CharField(max_length=128)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_locked(self):
        return self.locked_at is not None

    @property
    def is_in_cooldown(self):
        return self.locked_at is None and self.locked_until is not None and self.locked_until > timezone.now()

    def __str__(self):
        return f"PIN of {self.user}"


class TrustedDevice(models.Model):
    """A browser the user has signed in from, remembered for PIN sign-in.

    Only a SHA-256 hash of the random device token (HttpOnly cookie) is stored,
    so a database leak cannot be replayed. Deleting the row revokes the device.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='trusted_devices')
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    label = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-last_used_at', '-created_at']

    def __str__(self):
        return f"{self.user} — {self.label or 'device'}"


class PinSecurityAudit(models.Model):
    """Immutable audit trail for PIN resets and security lifecycle events."""
    class Action(models.TextChoices):
        MANAGER_RESET = 'MANAGER_RESET', 'Manager reset'
        ACCOUNT_RECOVERY = 'ACCOUNT_RECOVERY', 'Account recovery reset'
        LOCKED = 'LOCKED', 'PIN locked'
        CHANGED = 'CHANGED', 'PIN changed'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pin_security_audits')
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='pin_security_actions')
    action = models.CharField(max_length=32, choices=Action.choices)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['action', '-created_at']),
        ]


class ActiveDeviceSession(models.Model):
    """The ONE device an account is currently signed in on (single-device sign-in).

    Only a SHA-256 hash of the browser's random device token is stored. The row is
    claimed at sign-in, refreshed while the device is in use, and released on logout,
    by a manager, after a password recovery, or automatically once the session has
    expired / the device has been idle for SINGLE_DEVICE_IDLE_RELEASE_MINUTES.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='active_device_session')
    device_hash = models.CharField(max_length=64, db_index=True)
    session_key = models.CharField(max_length=40, blank=True, db_index=True)
    label = models.CharField(max_length=120, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.user} on {self.label or 'device'}"


class DeviceSessionAudit(models.Model):
    """Audit trail for single-device sign-in decisions (blocked attempts, releases)."""
    class Action(models.TextChoices):
        BLOCKED = 'BLOCKED', 'Sign-in blocked (active on another device)'
        RELEASED_BY_MANAGER = 'RELEASED_BY_MANAGER', 'Released by manager'
        RELEASED_BY_RECOVERY = 'RELEASED_BY_RECOVERY', 'Released by password recovery'
        SUPERSEDED = 'SUPERSEDED', 'Previous device signed out (took over after idle)'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='device_session_audits')
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='device_session_actions')
    action = models.CharField(max_length=32, choices=Action.choices)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', '-created_at'], name='accounts_dsa_user_created_idx')]
