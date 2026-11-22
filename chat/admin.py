from django.contrib import admin

from .models import ChatThread, ChatMessage, BroadcastMessage, ChatAttachment


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ('sender', 'text', 'created_at')
    can_delete = False


@admin.register(ChatThread)
class ChatThreadAdmin(admin.ModelAdmin):
    list_display = ('label', 'pharmacy', 'employee', 'created_at')
    list_filter = ('pharmacy',)
    inlines = [ChatMessageInline]


@admin.register(BroadcastMessage)
class BroadcastMessageAdmin(admin.ModelAdmin):
    list_display = ('sender', 'created_at', 'short_text')
    list_filter = ('created_at',)
    search_fields = ('sender__username', 'sender__first_name', 'sender__last_name', 'text')
    readonly_fields = ('sender', 'text', 'created_at')

    @admin.display(description='Message')
    def short_text(self, obj):
        return obj.text[:80]


@admin.register(ChatAttachment)
class ChatAttachmentAdmin(admin.ModelAdmin):
    list_display = ('original_name', 'message', 'size', 'content_type', 'created_at')
    readonly_fields = ('message', 'file', 'original_name', 'content_type', 'size', 'created_at')
