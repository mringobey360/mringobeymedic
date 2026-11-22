from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User, ManagerAssistant, ManagerAssistantAudit, OTP, WeeklyMealSchedule, MealAdjustment, MealTimingSettings, MealWorkday, MealLedger, AttendanceRecord, AttendanceVerification, AttendanceExceptionRequest, MealIssueReport, PushSubscription, MealReminderDelivery, PinSecurityAudit, ActiveDeviceSession, DeviceSessionAudit


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'first_name', 'last_name', 'role', 'phone_number', 'is_active', 'must_change_password')
    list_filter = ('role', 'is_active')
    fieldsets = UserAdmin.fieldsets + (
        ('Role Info', {'fields': ('role', 'phone_number', 'must_change_password', 'allow_multiple_devices')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Role Info', {'fields': ('role', 'phone_number', 'must_change_password', 'allow_multiple_devices')}),
    )


@admin.register(OTP)
class OTPAdmin(admin.ModelAdmin):
    list_display = ('user', 'code', 'created_at', 'is_used')
    readonly_fields = ('code', 'created_at')
    list_filter = ('is_used',)


@admin.register(WeeklyMealSchedule)
class WeeklyMealScheduleAdmin(admin.ModelAdmin):
    list_display = ('employee', 'weekday', 'breakfast', 'lunch', 'dinner', 'updated_at')
    list_filter = ('weekday', 'breakfast', 'lunch', 'dinner')


@admin.register(MealAdjustment)
class MealAdjustmentAdmin(admin.ModelAdmin):
    list_display = ('employee', 'date', 'breakfast', 'lunch', 'dinner', 'reason', 'created_by', 'created_at')
    list_filter = ('date', 'breakfast', 'lunch', 'dinner')
    search_fields = ('employee__username', 'employee__first_name', 'employee__last_name', 'reason')

@admin.register(MealTimingSettings)
class MealTimingSettingsAdmin(admin.ModelAdmin):
    list_display = ('pharmacy', 'breakfast_cutoff', 'lunch_cutoff', 'dinner_cutoff', 'updated_at')

@admin.register(MealWorkday)
class MealWorkdayAdmin(admin.ModelAdmin):
    list_display = ('employee', 'date', 'opened_at', 'closed_at', 'opened_manually')
    list_filter = ('date', 'opened_manually')
    search_fields = ('employee__username', 'employee__first_name', 'employee__last_name')

@admin.register(MealLedger)
class MealLedgerAdmin(admin.ModelAdmin):
    list_display = ('employee', 'date', 'meal_type', 'amount', 'status', 'claim_method', 'claimed_at', 'cutoff_at', 'earned_at', 'debt_at')
    list_filter = ('status', 'meal_type', 'source', 'date')
    search_fields = ('employee__username', 'employee__first_name', 'employee__last_name', 'reason')
    readonly_fields = ('created_at', 'updated_at', 'earned_at', 'debt_at', 'resolved_at', 'claimed_at', 'missed_at', 'manual_reason', 'manual_note')


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display=('employee','pharmacy','date','status','method','check_in_at','check_out_at','check_in_distance_m','reviewed_by')
    list_filter=('status','method','pharmacy','date')
    search_fields=('employee__username','employee__first_name','employee__last_name','pharmacy__name')
    readonly_fields=('created_at','updated_at','verified_at')

@admin.register(AttendanceVerification)
class AttendanceVerificationAdmin(admin.ModelAdmin):
    list_display=('attendance','checked_at','result','distance_m','accuracy_m')
    list_filter=('result','checked_at')
    search_fields=('attendance__employee__username','attendance__pharmacy__name')
    readonly_fields=('checked_at',)

@admin.register(AttendanceExceptionRequest)
class AttendanceExceptionRequestAdmin(admin.ModelAdmin):
    list_display=('employee','pharmacy','date','status','requested_at','decided_by')
    list_filter=('status','pharmacy','date')
    search_fields=('employee__username','employee__first_name','employee__last_name','reason')
    readonly_fields=('requested_at',)


@admin.register(ManagerAssistant)
class ManagerAssistantAdmin(admin.ModelAdmin):
    list_display = ('assistant', 'manager', 'pharmacy', 'active', 'created_at', 'deactivated_at')
    list_filter = ('active', 'pharmacy')
    search_fields = ('assistant__username', 'assistant__first_name', 'assistant__last_name', 'manager__username', 'pharmacy__name')
    readonly_fields = ('created_at', 'updated_at', 'deactivated_at')


@admin.register(ManagerAssistantAudit)
class ManagerAssistantAuditAdmin(admin.ModelAdmin):
    list_display = ('profile', 'actor', 'action', 'created_at')
    list_filter = ('action', 'created_at')
    search_fields = ('profile__assistant__username', 'profile__manager__username', 'actor__username')
    readonly_fields = ('profile', 'actor', 'action', 'details', 'created_at')


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'active', 'created_at', 'updated_at')
    list_filter = ('active', 'created_at')
    search_fields = ('user__username', 'endpoint')
    readonly_fields = ('created_at', 'updated_at')

@admin.register(MealReminderDelivery)
class MealReminderDeliveryAdmin(admin.ModelAdmin):
    list_display = ('pharmacy', 'date', 'meal_type', 'scheduled_for', 'delivered_count', 'created_at')
    list_filter = ('meal_type', 'date', 'pharmacy')
    readonly_fields = ('created_at',)


@admin.register(MealIssueReport)
class MealIssueReportAdmin(admin.ModelAdmin):
    list_display = ('id', 'employee', 'pharmacy', 'date', 'meal_type', 'reason', 'is_late', 'status', 'reported_at', 'decided_by', 'decided_at')
    list_filter = ('status', 'is_late', 'meal_type', 'reason', 'pharmacy', 'date')
    search_fields = ('employee__username', 'employee__first_name', 'employee__last_name', 'details')
    readonly_fields = ('reported_at', 'decided_at', 'window_start_at', 'window_end_at', 'ip_address')


@admin.register(PinSecurityAudit)
class PinSecurityAuditAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'action', 'actor', 'ip_address')
    list_filter = ('action', 'created_at')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'actor__username', 'ip_address')
    readonly_fields = ('user', 'actor', 'action', 'details', 'ip_address', 'user_agent', 'created_at')


@admin.register(ActiveDeviceSession)
class ActiveDeviceSessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'label', 'ip_address', 'created_at', 'last_seen')
    search_fields = ('user__username', 'label')
    readonly_fields = ('device_hash', 'session_key')


@admin.register(DeviceSessionAudit)
class DeviceSessionAuditAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'action', 'actor', 'ip_address')
    list_filter = ('action',)
    search_fields = ('user__username',)
    readonly_fields = ('user', 'actor', 'action', 'details', 'ip_address', 'user_agent', 'created_at')

    def has_add_permission(self, request):
        return False
