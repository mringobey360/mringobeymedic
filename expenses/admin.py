from django.contrib import admin
from .models import Expense, ExpenseCategory, CashDay, CashOpeningAdjustment, CashCount

@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display=('name','pharmacy','is_active','is_system')
    list_filter=('is_active','is_system','pharmacy')
    search_fields=('name',)

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display=('expense_date','title','amount','payment_source','status','submitted_by','decided_by')
    list_filter=('status','payment_source','expense_date','pharmacy','category')
    search_fields=('title','description','reference','submitted_by__username')
    readonly_fields=('submitted_at','decided_at','updated_at')

@admin.register(CashDay)
class CashDayAdmin(admin.ModelAdmin):
    list_filter=('status','pharmacy','date')
    readonly_fields=('opened_at','closed_at','opening_source','carried_from','opening_is_provisional','opening_provisional_note','opening_reconciled_at','day_ended_at','day_ended_automatically','closed_automatically','auto_close_reason')
    list_display=('date','pharmacy','opening_cash','opening_source','expected_closing_cash','actual_closing_cash','cash_difference','physical_counted_by','physical_counted_at','day_ended_at','status')

@admin.register(CashOpeningAdjustment)
class CashOpeningAdjustmentAdmin(admin.ModelAdmin):
    list_display = ('cash_day', 'previous_amount', 'new_amount', 'adjusted_by', 'adjusted_at')
    list_filter = ('cash_day__pharmacy', 'adjusted_at')
    search_fields = ('reason', 'adjusted_by__username')
    readonly_fields = ('cash_day', 'previous_amount', 'new_amount', 'reason', 'adjusted_by', 'adjusted_at')


@admin.register(CashCount)
class CashCountAdmin(admin.ModelAdmin):
    list_display = ('cash_day', 'counted_at', 'counted_by', 'expected_cash', 'physical_cash', 'variance')
    list_filter = ('cash_day__pharmacy',)
    search_fields = ('cash_day__pharmacy__name', 'counted_by__username')
    readonly_fields = ('cash_day', 'counted_at', 'counted_by', 'expected_cash', 'physical_cash', 'variance', 'note', 'confirmed', 'created_at')


from .models import CashAuditEvent, CashClosingSnapshot


class _ReadOnlyAdmin(admin.ModelAdmin):
    """Audit records are append-only: viewable, never editable or deletable in admin."""
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CashAuditEvent)
class CashAuditEventAdmin(_ReadOnlyAdmin):
    list_display = ('created_at', 'business_date', 'pharmacy', 'action', 'actor', 'old_value', 'new_value')
    list_filter = ('action', 'pharmacy', 'business_date')
    search_fields = ('reason', 'actor__username')


@admin.register(CashClosingSnapshot)
class CashClosingSnapshotAdmin(_ReadOnlyAdmin):
    list_display = ('cash_day', 'revision', 'opening_cash', 'expected_cash', 'physical_cash', 'variance', 'closed_by', 'closed_at')
    list_filter = ('cash_day__pharmacy',)


from .models import CashTransfer


@admin.register(CashTransfer)
class CashTransferAdmin(admin.ModelAdmin):
    list_display = ('transfer_date', 'pharmacy', 'direction', 'amount', 'reference', 'created_by', 'created_at')
    list_filter = ('direction', 'pharmacy')
    search_fields = ('reference', 'note')
    date_hierarchy = 'transfer_date'
    readonly_fields = ('created_at',)
