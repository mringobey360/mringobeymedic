from django.contrib import admin, messages
from django.db import transaction
from django.utils import timezone
from .models import LayawayPlan, LayawayPayment, LayawayAudit, LayawaySMSWallet, LayawaySMSPackage, LayawaySMSPurchase

class PaymentInline(admin.TabularInline):
    model = LayawayPayment
    extra = 0
    readonly_fields = ('idempotency_key', 'received_at')

@admin.register(LayawayPlan)
class LayawayPlanAdmin(admin.ModelAdmin):
    list_display = ('reference', 'pharmacy', 'product', 'customer_name', 'quantity', 'status', 'created_at')
    list_filter = ('status', 'pharmacy', 'created_at')
    search_fields = ('reference', 'customer_name', 'customer_phone', 'product__name')
    readonly_fields = ('reference', 'created_at', 'completed_at', 'resolved_at')
    inlines = [PaymentInline]

@admin.register(LayawayPayment)
class LayawayPaymentAdmin(admin.ModelAdmin):
    list_display = ('plan', 'amount', 'method', 'status', 'received_by', 'received_at')
    list_filter = ('method', 'status', 'received_at')
    search_fields = ('plan__reference', 'plan__customer_name', 'transaction_reference')
    readonly_fields = ('idempotency_key', 'received_at')

@admin.register(LayawayAudit)
class LayawayAuditAdmin(admin.ModelAdmin):
    list_display = ('plan', 'action', 'actor', 'created_at')
    list_filter = ('action', 'created_at')
    readonly_fields = ('plan', 'actor', 'action', 'detail', 'created_at')
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False

@admin.register(LayawaySMSWallet)
class LayawaySMSWalletAdmin(admin.ModelAdmin):
    list_display = ('pharmacy', 'trial_used', 'paid_credits', 'paid_used', 'updated_at')
    readonly_fields = ('trial_used', 'paid_used', 'updated_at')

@admin.register(LayawaySMSPackage)
class LayawaySMSPackageAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'sms_credits', 'is_featured', 'is_active', 'sort_order')
    list_editable = ('is_featured', 'is_active', 'sort_order')

@admin.register(LayawaySMSPurchase)
class LayawaySMSPurchaseAdmin(admin.ModelAdmin):
    list_display = ('pharmacy', 'package', 'amount', 'status', 'payment_reference', 'requested_at', 'credits_granted')
    list_filter = ('status', 'package', 'requested_at')
    search_fields = ('pharmacy__name', 'payment_reference', 'requested_by__username')
    readonly_fields = ('requested_at', 'reviewed_at', 'reviewed_by', 'credits_granted')
    actions = ('approve_selected', 'reject_selected')

    @admin.action(description='Approve selected purchases and grant SMS credits')
    def approve_selected(self, request, queryset):
        count = 0
        for purchase in queryset.select_related('package', 'pharmacy'):
            with transaction.atomic():
                locked = LayawaySMSPurchase.objects.select_for_update().get(pk=purchase.pk)
                if locked.status == LayawaySMSPurchase.Status.APPROVED and locked.credits_granted:
                    continue
                locked.status = LayawaySMSPurchase.Status.APPROVED
                locked.reviewed_at = timezone.now()
                locked.reviewed_by = request.user
                locked.save(update_fields=['status', 'reviewed_at', 'reviewed_by'])
                wallet, _ = LayawaySMSWallet.objects.get_or_create(pharmacy=locked.pharmacy)
                wallet = LayawaySMSWallet.objects.select_for_update().get(pk=wallet.pk)
                if not locked.credits_granted:
                    wallet.paid_credits += locked.package.sms_credits
                    wallet.save(update_fields=['paid_credits', 'updated_at'])
                    locked.credits_granted = True
                    locked.save(update_fields=['credits_granted'])
                count += 1
        self.message_user(request, f'Approved {count} purchase(s); credits were granted once per purchase.', messages.SUCCESS)

    @admin.action(description='Reject selected purchases')
    def reject_selected(self, request, queryset):
        queryset.exclude(status=LayawaySMSPurchase.Status.APPROVED).update(status=LayawaySMSPurchase.Status.REJECTED, reviewed_at=timezone.now(), reviewed_by=request.user)

from .models import LayawaySMSLog

@admin.register(LayawaySMSLog)
class LayawaySMSLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'pharmacy', 'plan', 'event_type', 'phone', 'status', 'attempts', 'sent_at')
    list_filter = ('status', 'event_type', 'pharmacy', 'created_at')
    search_fields = ('plan__reference', 'phone', 'pharmacy__name', 'event_key', 'provider_result')
    readonly_fields = ('pharmacy', 'plan', 'event_key', 'event_type', 'phone', 'message', 'status', 'provider_result', 'attempts', 'created_at', 'updated_at', 'sent_at')
    date_hierarchy = 'created_at'
