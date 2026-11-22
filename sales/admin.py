from django.contrib import admin
from .models import Sale, SaleItem, Debt, SaleReturnRequest, SaleReturnLine


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    readonly_fields = ('product', 'batch', 'quantity', 'returned_quantity', 'unit_price', 'unit_cost')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('id', 'pharmacy', 'served_by', 'payment_method', 'status', 'created_at')
    list_filter = ('pharmacy', 'payment_method', 'status', 'created_at')
    inlines = [SaleItemInline]
    readonly_fields = ('pharmacy', 'served_by', 'payment_method', 'status', 'voided_at', 'customer_name', 'customer_phone', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # Deleting a sale would leave stock deducted and erase the audit trail.
        # Mistakes are corrected through Returns & Voids instead.
        return False


@admin.register(Debt)
class DebtAdmin(admin.ModelAdmin):
    list_display = ('customer_name', 'pharmacy', 'amount_owed', 'amount_paid', 'is_settled', 'created_at')
    list_filter = ('pharmacy', 'is_settled')
    search_fields = ('customer_name', 'customer_phone')


class SaleReturnLineInline(admin.TabularInline):
    model = SaleReturnLine
    extra = 0
    readonly_fields = ('sale_item', 'quantity')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SaleReturnRequest)
class SaleReturnRequestAdmin(admin.ModelAdmin):
    """Read-only audit view: who asked, who decided, why, and what it was worth."""
    list_display = ('id', 'sale', 'pharmacy', 'status', 'requested_by', 'decided_by', 'refund_amount', 'requested_at')
    list_filter = ('pharmacy', 'status', 'disposition')
    inlines = [SaleReturnLineInline]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
