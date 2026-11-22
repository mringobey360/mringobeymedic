from django.contrib import admin
from django.utils.html import format_html

from .models import SMSSettings, SMSLog, SMSWallet, CustomerSMSCredentials, SMSDebtPackage, SMSDebtSubscription, SMSDebtPurchase, SMSReceiptPackage, SMSReceiptSubscription, SMSReceiptPurchase, SMSMarketingPackage, SMSMarketingSubscription, SMSMarketingPurchase, SMSAnnouncement, SMSAnnouncementRecipient


@admin.register(SMSSettings)
class SMSSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Master Switch", {
            'fields': ('is_enabled',),
            'description': "Turn OFF to stop every outgoing SMS instantly, regardless of the toggles below."
        }),
        ("Africa's Talking Credentials", {
            'fields': ('at_username', 'at_api_key', 'sender_id'),
            'description': "Enter the credentials from your Africa's Talking dashboard (africastalking.com)."
        }),
        ("Cost Control — choose which SMS categories are allowed to send", {
            'fields': (
                'send_employee_credentials', 'send_otp', 'send_daily_summary',
                'send_low_stock_alerts', 'send_expiry_alerts', 'send_debt_reminders', 'send_receipt_sms', 'send_payment_receipts', 'send_customer_marketing', 'send_attendance_alerts',
            ),
        }),
    )

    def has_add_permission(self, request):
        # Singleton: block creating a second row once one exists.
        return not SMSSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SMSLog)
class SMSLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'category', 'recipient', 'sms_segments', 'status_badge', 'provider_status', 'delivery_status')
    list_filter = ('category', 'was_sent', 'created_at')
    search_fields = ('recipient', 'message', 'provider_message_id', 'provider_status', 'delivery_status')
    readonly_fields = [f.name for f in SMSLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def status_badge(self, obj):
        color = '#16a34a' if obj.was_sent else '#dc2626'
        label = 'SENT' if obj.was_sent else 'FAILED'
        return format_html('<span style="color:{}; font-weight:600;">{}</span>', color, label)
    status_badge.short_description = 'Status'


@admin.register(SMSWallet)
class SMSWalletAdmin(admin.ModelAdmin):
    list_display = ('owner', 'credits_granted', 'credits_used', 'remaining')
    search_fields = ('owner__username', 'owner__phone_number')
    readonly_fields = ('created_at', 'updated_at')
    def remaining(self, obj): return obj.credits_remaining


@admin.register(CustomerSMSCredentials)
class CustomerSMSCredentialsAdmin(admin.ModelAdmin):
    list_display = ('owner', 'access_enabled', 'at_username', 'sender_id', 'updated_at')
    list_filter = ('access_enabled',)
    search_fields = ('owner__username', 'owner__phone_number', 'sender_id')
    fieldsets = ((None, {'fields': ('owner', 'access_enabled')}), ("Customer SMS Credentials", {'fields': ('at_username','sender_id','at_api_key'), 'description': 'Enable access first. The customer can then enter/update these values from their own protected SMS Settings page.'}),)


@admin.register(SMSDebtPackage)
class SMSDebtPackageAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'sms_credits', 'duration_days', 'is_active', 'sort_order')
    list_filter = ('is_active',)
    search_fields = ('name', 'code')

@admin.register(SMSDebtSubscription)
class SMSDebtSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('owner', 'package', 'sms_credits', 'sms_used', 'remaining', 'start_date', 'end_date', 'status')
    list_filter = ('status', 'package')
    search_fields = ('owner__username', 'owner__phone_number')
    readonly_fields = ('created_at', 'updated_at')
    def remaining(self, obj): return obj.credits_remaining

@admin.register(SMSDebtPurchase)
class SMSDebtPurchaseAdmin(admin.ModelAdmin):
    list_display = ('requested_at', 'owner', 'package', 'amount', 'payment_reference', 'status', 'reviewed_at')
    list_filter = ('status', 'package')
    search_fields = ('owner__username', 'owner__phone_number', 'payment_reference')
    readonly_fields = ('requested_at', 'reviewed_at')


@admin.register(SMSReceiptPackage)
class SMSReceiptPackageAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'sms_credits', 'duration_days', 'is_active', 'sort_order')
    list_filter = ('is_active',)
    search_fields = ('name', 'code')

@admin.register(SMSReceiptSubscription)
class SMSReceiptSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('owner', 'package', 'sms_credits', 'sms_used', 'remaining', 'start_date', 'end_date', 'status')
    list_filter = ('status', 'package')
    search_fields = ('owner__username', 'owner__phone_number')
    readonly_fields = ('created_at', 'updated_at')
    def remaining(self, obj): return obj.credits_remaining

@admin.register(SMSReceiptPurchase)
class SMSReceiptPurchaseAdmin(admin.ModelAdmin):
    list_display = ('requested_at', 'owner', 'package', 'amount', 'payment_reference', 'status', 'reviewed_at')
    list_filter = ('status', 'package')
    search_fields = ('owner__username', 'owner__phone_number', 'payment_reference')
    readonly_fields = ('requested_at', 'reviewed_at')


class SMSAnnouncementRecipientInline(admin.TabularInline):
    model = SMSAnnouncementRecipient
    extra = 0
    can_delete = False
    fields = ('display_name', 'phone_number', 'status', 'segments', 'provider_status', 'error_message', 'sent_at', 'opened_at')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SMSAnnouncement)
class SMSAnnouncementAdmin(admin.ModelAdmin):
    """Read-only audit view. Campaigns are created from HQ > SMS Announcements."""
    list_display = ('title', 'message_type', 'audience', 'status', 'sent_count', 'failed_count', 'skipped_count', 'total_recipients', 'created_at')
    list_filter = ('status', 'message_type', 'audience')
    search_fields = ('title', 'message')
    inlines = [SMSAnnouncementRecipientInline]

    def has_add_permission(self, request):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

@admin.register(SMSMarketingPackage)
class SMSMarketingPackageAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'sms_credits', 'duration_days', 'is_active', 'sort_order')
    list_filter = ('is_active',)
    search_fields = ('name', 'code')

@admin.register(SMSMarketingSubscription)
class SMSMarketingSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('owner', 'package', 'sms_credits', 'sms_used', 'remaining', 'start_date', 'end_date', 'status')
    list_filter = ('status', 'package')
    search_fields = ('owner__username', 'owner__phone_number')
    readonly_fields = ('created_at', 'updated_at')
    def remaining(self, obj): return obj.credits_remaining

@admin.register(SMSMarketingPurchase)
class SMSMarketingPurchaseAdmin(admin.ModelAdmin):
    list_display = ('requested_at', 'owner', 'package', 'amount', 'payment_reference', 'status', 'reviewed_at')
    list_filter = ('status', 'package')
    search_fields = ('owner__username', 'owner__phone_number', 'payment_reference')
    readonly_fields = ('requested_at', 'reviewed_at')


from .models import SMSProspect  # noqa: E402


@admin.register(SMSProspect)
class SMSProspectAdmin(admin.ModelAdmin):
    list_display = ('phone_number', 'name', 'business_name', 'business_type', 'region', 'status', 'sms_count', 'last_sms_at', 'created_at')
    list_filter = ('status', 'business_type')
    search_fields = ('phone_number', 'name', 'business_name', 'region')
    readonly_fields = ('sms_count', 'last_sms_at', 'created_at', 'updated_at')
