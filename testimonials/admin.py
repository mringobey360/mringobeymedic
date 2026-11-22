from django.contrib import admin

from .models import TestimonialAuditLog, TestimonialInvitation, TestimonialReview, TestimonialSettings


@admin.register(TestimonialSettings)
class TestimonialSettingsAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'enabled', 'snooze_days', 'show_on_landing', 'updated_at')

    def has_add_permission(self, request):
        return not TestimonialSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TestimonialInvitation)
class TestimonialInvitationAdmin(admin.ModelAdmin):
    list_display = ('customer', 'status', 'authorized_by', 'authorized_at', 'responded_at')
    list_filter = ('status',)
    search_fields = ('customer__username', 'customer__first_name', 'customer__last_name')
    raw_id_fields = ('customer', 'authorized_by')


@admin.register(TestimonialReview)
class TestimonialReviewAdmin(admin.ModelAdmin):
    list_display = ('id', 'rating', 'customer', 'status', 'created_at', 'moderated_by')
    list_filter = ('status', 'rating')
    search_fields = ('comment', 'display_name', 'business_name', 'customer__username')
    raw_id_fields = ('customer', 'invitation', 'moderated_by')


@admin.register(TestimonialAuditLog)
class TestimonialAuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'action', 'actor', 'target_user', 'detail')
    list_filter = ('action',)
    readonly_fields = [f.name for f in TestimonialAuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
