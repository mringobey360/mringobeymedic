from django.contrib import admin

from .models import CustomerDataAccessLog


@admin.register(CustomerDataAccessLog)
class CustomerDataAccessLogAdmin(admin.ModelAdmin):
    """Read-only trail: who exported / bulk-imported / bulk-deleted customer numbers."""
    list_display = ('created_at', 'username', 'action', 'record_count', 'pharmacy', 'ip_address')
    list_filter = ('action', 'pharmacy')
    search_fields = ('username', 'ip_address')
    date_hierarchy = 'created_at'
    readonly_fields = [f.name for f in CustomerDataAccessLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
