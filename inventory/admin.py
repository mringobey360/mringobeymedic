from django.contrib import admin
from django.contrib import messages
from django.db.models.deletion import ProtectedError
from .models import Category, UnitCatalog, Product, ProductBatch, StockMovement, StockImport
from .purge import dependency_counts, purge_product


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'pharmacy')
    list_filter = ('pharmacy',)
    search_fields = ('name', 'pharmacy__name')


@admin.register(UnitCatalog)
class UnitCatalogAdmin(admin.ModelAdmin):
    list_display = ('name', 'pharmacy', 'created_at')
    list_filter = ('pharmacy',)
    search_fields = ('name', 'pharmacy__name')
    readonly_fields = ('normalized_name', 'created_at')


class ProductBatchInline(admin.TabularInline):
    model = ProductBatch
    extra = 0
    readonly_fields = ('received_at',)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'pharmacy', 'category', 'total_quantity', 'requires_expiry', 'discount_enabled', 'min_discount_percent', 'max_discount_percent', 'is_active')
    list_filter = ('pharmacy', 'category', 'is_active', 'unit', 'requires_expiry')
    search_fields = ('name',)
    inlines = [ProductBatchInline]

    def total_quantity(self, obj):
        return obj.total_quantity
    total_quantity.short_description = 'Stock'

    # ---- Deleting: sale lines / layaway plans / transfer lines no longer block the delete ----
    def get_deleted_objects(self, objs, request):
        deletable, model_count, perms_needed, _protected = super().get_deleted_objects(objs, request)
        warn = []
        for obj in objs:
            c = dependency_counts(obj)
            if any(c.values()):
                warn.append(
                    f'WARNING - {obj.name}: also permanently deletes {c["sale_items"]} sale line(s) '
                    f'(sales left empty are removed), {c["layaway_plans"]} layaway plan(s) with their payments, '
                    f'and {c["transfer_lines"]} branch-transfer record(s).')
        # PROTECT relations are cleared by purge_product(), so they must not block the confirmation page.
        return warn + list(deletable), model_count, perms_needed, []

    def delete_model(self, request, obj):
        try:
            purge_product(obj)
        except ProtectedError as exc:
            self.message_user(request, f'Could not delete {obj.name}: still referenced by {len(exc.protected_objects)} protected record(s).', messages.ERROR)

    def delete_queryset(self, request, queryset):
        failed = 0
        for obj in list(queryset):
            try:
                purge_product(obj)
            except ProtectedError:
                failed += 1
        if failed:
            self.message_user(request, f'{failed} product(s) could not be deleted because other protected records still reference them.', messages.ERROR)


@admin.register(ProductBatch)
class ProductBatchAdmin(admin.ModelAdmin):
    list_display = ('product', 'batch_number', 'quantity', 'initial_quantity', 'expiry_date', 'received_at')
    list_filter = ('expiry_date',)
    search_fields = ('product__name', 'batch_number')
    date_hierarchy = 'expiry_date'


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('product', 'batch', 'movement_type', 'quantity_change', 'performed_by', 'created_at')
    list_filter = ('movement_type', 'created_at')
    readonly_fields = [f.name for f in StockMovement._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(StockImport)
class StockImportAdmin(admin.ModelAdmin):
    list_display = ('reference', 'pharmacy', 'mode', 'row_count', 'new_products', 'new_batches', 'topped_up', 'created_by', 'created_at')
    list_filter = ('mode', 'pharmacy', 'created_at')
    search_fields = ('reference', 'file_name', 'created_by__username')
    readonly_fields = [f.name for f in StockImport._meta.fields]

    def has_add_permission(self, request):
        return False
