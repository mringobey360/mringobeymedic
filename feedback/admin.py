from django.contrib import admin
from .models import Feedback, FeedbackReply, SupportTicket, SupportMessage

class FeedbackReplyInline(admin.TabularInline):
    model = FeedbackReply
    extra = 0
    readonly_fields = ('author', 'created_at')

@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'branch', 'rating', 'category', 'status', 'created_at')
    list_filter = ('status', 'category', 'rating', 'created_at')
    search_fields = ('user__username', 'user__email', 'comment', 'branch__name')
    readonly_fields = ('created_at', 'updated_at', 'user_agent', 'page_url')
    inlines = [FeedbackReplyInline]

class SupportMessageInline(admin.TabularInline):
    model = SupportMessage
    extra = 0
    readonly_fields = ('author', 'created_at')

@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ('ticket_number', 'subject', 'user', 'branch', 'category', 'priority', 'status', 'assigned_to', 'updated_at')
    list_filter = ('status', 'priority', 'category', 'created_at')
    search_fields = ('ticket_number', 'subject', 'user__username', 'user__email', 'branch__name')
    readonly_fields = ('ticket_number', 'created_at', 'updated_at', 'resolved_at')
    inlines = [SupportMessageInline]

@admin.register(FeedbackReply)
class FeedbackReplyAdmin(admin.ModelAdmin):
    list_display = ('feedback', 'author', 'is_internal', 'created_at')
    list_filter = ('is_internal', 'created_at')
    search_fields = ('message', 'author__username')

@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'author', 'is_internal', 'created_at')
    list_filter = ('is_internal', 'created_at')
    search_fields = ('body', 'author__username', 'ticket__ticket_number')
