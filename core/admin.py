from django.contrib import admin

from .models import UserDataBackup


@admin.register(UserDataBackup)
class UserDataBackupAdmin(admin.ModelAdmin):
    list_display = ('owner', 'filename', 'size_display', 'created_at')
    search_fields = ('owner__username', 'owner__phone_number', 'filename')
    readonly_fields = ('owner', 'filename', 'storage_path', 'size_bytes', 'record_counts', 'created_at')
    ordering = ('-created_at',)

    @admin.display(description='Size')
    def size_display(self, obj):
        return f'{obj.size_mb:.2f} MB'
