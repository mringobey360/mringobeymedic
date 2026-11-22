from django.contrib import admin
from .models import DailyReportSettings, DailySalesReport, DailyReportDeliveryLog

@admin.register(DailyReportSettings)
class DailyReportSettingsAdmin(admin.ModelAdmin):
    list_display = ('owner', 'enabled', 'delivery_time', 'phone_number', 'last_sent_for_date', 'updated_at')
    list_filter = ('enabled',)
    search_fields = ('owner__username', 'owner__phone_number', 'phone_number')

@admin.register(DailySalesReport)
class DailySalesReportAdmin(admin.ModelAdmin):
    list_display = ('owner', 'report_date', 'generated_at', 'sms_sent_at', 'expires_at')
    list_filter = ('report_date',)
    search_fields = ('owner__username', 'owner__phone_number')
    readonly_fields = ('access_token', 'generated_at', 'sms_sent_at')

@admin.register(DailyReportDeliveryLog)
class DailyReportDeliveryLogAdmin(admin.ModelAdmin):
    list_display = ('owner', 'report_date', 'scheduled_time', 'status', 'recipient', 'attempted_at')
    list_filter = ('status', 'report_date')
    search_fields = ('owner__username', 'recipient')
    readonly_fields = ('attempted_at',)


from .models import DailyReportPackage, DailyReportSubscription, DailyReportPurchase

@admin.register(DailyReportPackage)
class DailyReportPackageAdmin(admin.ModelAdmin):
    list_display = ('name', 'report_sms_credits', 'price', 'duration_days', 'is_active', 'sort_order')
    list_filter = ('is_active',)
    search_fields = ('code', 'name')

@admin.register(DailyReportSubscription)
class DailyReportSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('owner', 'package', 'report_sms_credits', 'report_sms_used', 'status', 'start_date', 'end_date', 'amount_paid')
    list_filter = ('status',)
    search_fields = ('owner__username', 'owner__first_name', 'owner__last_name')

@admin.register(DailyReportPurchase)
class DailyReportPurchaseAdmin(admin.ModelAdmin):
    list_display = ('owner', 'package', 'amount', 'payment_reference', 'status', 'requested_at', 'reviewed_at')
    list_filter = ('status',)
    search_fields = ('owner__username', 'payment_reference', 'package__name')
