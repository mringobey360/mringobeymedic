from django.contrib import admin

from .models import PaymentSettings, SignupRequest, Subscription, SubscriptionPaymentReceipt


@admin.register(PaymentSettings)
class PaymentSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Payment Instructions (shown to customers)", {
            'fields': ('enable_lipa_namba', 'lipa_namba', 'payee_name', 'support_phone', 'instructions_text'),
            'description': 'Switch off manual Lipa Namba to show only the configured online/mobile-money gateway on public signup payment pages. Make sure an online gateway is active before disabling manual payments.',
        }),
        ("Arifa kwako", {
            'fields': ('owner_notification_phone',),
            'description': "Registration SMS notifications are sent here when a customer reaches the payment step.",
        }),
        ("Bei za Mipango", {
            'fields': ('starter_monthly_price', 'starter_yearly_price', 'business_monthly_price', 'business_yearly_price', 'professional_monthly_price', 'professional_yearly_price', 'enterprise_monthly_price', 'enterprise_yearly_price', 'branch_addon_price'),
        }),
    )

    def has_add_permission(self, request):
        return not PaymentSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SignupRequest)
class SignupRequestAdmin(admin.ModelAdmin):
    list_display = ('business_name', 'owner_full_name', 'phone_number', 'tier', 'plan', 'sms_package', 'receipt_sms_package', 'is_trial', 'status', 'created_at')
    list_filter = ('status', 'is_trial', 'tier', 'plan', 'created_at')
    search_fields = ('business_name', 'owner_first_name', 'owner_last_name', 'phone_number', 'email')
    readonly_fields = ('public_id', 'created_at', 'confirmed_at', 'confirmed_by', 'created_pharmacy', 'created_owner')


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('owner', 'tier', 'plan', 'status', 'is_trial', 'premium_daily_reports_enabled', 'start_date', 'end_date', 'amount_paid', 'payment_reference')
    list_filter = ('tier', 'plan', 'status', 'is_trial')
    search_fields = ('owner__username', 'owner__first_name', 'owner__last_name', 'signup_request__notes')
    fieldsets = ((None, {'fields': ('owner', 'signup_request', 'plan', 'tier', 'status', 'start_date', 'end_date', 'amount_paid', 'payment_reference')}), ('Premium Features', {'fields': ('premium_daily_reports_enabled',)}))


@admin.register(SubscriptionPaymentReceipt)
class SubscriptionPaymentReceiptAdmin(admin.ModelAdmin):
    list_display = ('receipt_number', 'subscription', 'sms_status', 'sms_attempts', 'sms_sent_at', 'created_at')
    list_filter = ('sms_status', 'created_at')
    search_fields = ('receipt_number', 'subscription__owner__username', 'subscription__owner__phone_number')
    readonly_fields = [f.name for f in SubscriptionPaymentReceipt._meta.fields]

    def has_add_permission(self, request):
        return False


from .models import Referral, ReferralCode  # noqa: E402


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'owner', 'created_at')
    search_fields = ('code', 'owner__username')


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ('referrer', 'signup', 'status', 'reward_percent', 'created_at', 'qualified_at', 'used_at')
    list_filter = ('status',)
    search_fields = ('referrer__username', 'signup__business_name', 'code_used')
