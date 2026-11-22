from django.conf import settings
from django.db import models
from django.db.models import Q
import uuid


class ChatThread(models.Model):
    """
    Three kinds of thread: branch/direct chats, an all-branches manager chat, and a platform-wide customer group.

    - GROUP thread (employee is NULL): one per branch, shared by that
      branch's Shop Owner(s) and every employee assigned to it — for
      instructions/negotiation the owner wants the whole branch to see.
    - DIRECT thread (employee set): a private line between one specific
      employee and "management" (every Admin who manages that branch —
      if there's more than one owner/supervisor for a branch, they all
      see it; there's no illusion of it being private from a co-owner).

    Participants are never stored as a list on the thread itself — they're
    computed live from the branch's CURRENT staffing (see participants()),
    so an employee who leaves the branch immediately loses access, and a
    newly assigned employee immediately gains it, with nothing to keep in sync.
    """

    pharmacy = models.ForeignKey(
        'pharmacies.Pharmacy', on_delete=models.CASCADE, null=True, blank=True,
        related_name='chat_threads',
        help_text="Branch for a normal branch/direct chat. NULL for an all-branches manager thread.",
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
        related_name='managed_chat_threads',
        help_text="Owner of an all-branches thread. Blank for existing branch/direct chats.",
    )
    platform_group = models.BooleanField(default=False, help_text='Platform-wide customer group for the system owner and active customer owners.')
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
        related_name='direct_chat_threads',
        help_text="Set only for a private thread with ONE employee. Blank = the branch's shared group thread.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['pharmacy'],
                condition=Q(employee__isnull=True, manager__isnull=True, platform_group=False),
                name='one_group_thread_per_pharmacy',
            ),
            models.UniqueConstraint(
                fields=['pharmacy', 'employee'],
                condition=Q(employee__isnull=False, manager__isnull=True, platform_group=False),
                name='one_direct_thread_per_employee',
            ),
            models.UniqueConstraint(
                fields=['manager'], condition=Q(employee__isnull=True, manager__isnull=False, pharmacy__isnull=True, platform_group=False), name='one_all_branches_thread_per_manager',
            ),
            models.UniqueConstraint(
                fields=['manager'], condition=Q(employee__isnull=True, manager__isnull=False, pharmacy__isnull=True, platform_group=True), name='one_platform_customer_thread_per_manager',
            ),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return self.label

    @property
    def is_group(self):
        return self.employee_id is None

    @property
    def is_customer_group(self):
        return self.platform_group and self.pharmacy_id is None and self.manager_id is not None

    @property
    def is_all_branches(self):
        return self.is_group and not self.platform_group and self.manager_id is not None and self.pharmacy_id is None

    @property
    def label(self):
        if self.is_customer_group:
            return 'System Owner & Customers'
        if self.is_all_branches:
            manager_name = self.manager.get_full_name() or self.manager.username
            return f"All Branches — {manager_name}"
        if self.is_group:
            return f"Group — {self.pharmacy.name}"
        return f"{self.employee.get_full_name() or self.employee.username} (Private)"

    def participants(self):
        """Everyone currently allowed to see/post in this thread."""
        from accounts.models import User
        if self.is_customer_group:
            customers = User.objects.filter(role=User.Role.ADMIN, is_active=True, is_superuser=False)
            return (User.objects.filter(pk=self.manager_id) | customers).distinct()
        if self.is_all_branches:
            # Owner + every active employee across every branch this owner
            # manages — a single shared space for cross-branch instructions
            # and problem-sharing. Employees from different branches DO see
            # each other here (that's the point); branch-specific chat still
            # only happens in each branch's own Group thread.
            branch_ids = self.manager.managed_pharmacies.values_list('pk', flat=True)
            employees = User.objects.filter(
                role=User.Role.EMPLOYEE, is_active=True, pharmacy_id__in=branch_ids
            )
            assistants = User.objects.filter(
                role=User.Role.ASSISTANT, is_active=True,
                assistant_profile__manager_id=self.manager_id,
                assistant_profile__active=True,
                assistant_profile__branch_access_mode='ALL',
            )
            return (User.objects.filter(pk=self.manager_id) | employees | assistants).distinct()
        admins = self.pharmacy.managers.all()
        assistants = User.objects.filter(
            role=User.Role.ASSISTANT, is_active=True,
            assistant_profile__active=True,
            assistant_profile__manager__managed_pharmacies=self.pharmacy,
        ).filter(
            Q(assistant_profile__branch_access_mode='ALL') | Q(assistant_profile__branches=self.pharmacy)
        ).distinct()
        if self.is_group:
            employees = self.pharmacy.employees.filter(is_active=True)
            return (admins | assistants | employees).distinct()
        return (admins | User.objects.filter(pk=self.employee_id)).distinct()

    def user_can_access(self, user):
        if self.is_customer_group:
            return user.is_superuser or (user.is_active and user.is_management_user() and not user.is_superuser)
        if self.is_all_branches:
            if user.id == self.manager_id:
                return True
            if user.is_assistant_role():
                profile = getattr(user, 'assistant_profile', None)
                return bool(
                    profile and profile.active and profile.manager_id == self.manager_id
                    and profile.branch_access_mode == profile.BranchAccessMode.ALL_MANAGER_BRANCHES
                )
            return (
                user.is_employee_role()
                and user.is_active
                and user.pharmacy_id in self.manager.managed_pharmacies.values_list('pk', flat=True)
            )
        if user.is_management_user():
            return user.accessible_management_pharmacies().filter(pk=self.pharmacy_id).exists()
        if user.is_assistant_role():
            profile = getattr(user, 'assistant_profile', None)
            return bool(profile and profile.active and profile.manager_id in self.pharmacy.managers.values_list('pk', flat=True) and profile.can_access_pharmacy(self.pharmacy))
        if self.is_group:
            return user.pharmacy_id == self.pharmacy_id
        return self.employee_id == user.id


class ChatMessage(models.Model):
    thread = models.ForeignKey(ChatThread, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_chat_messages')
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    client_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, null=True, blank=True)
    # WhatsApp-style reply: the message this one answers (same thread only).
    reply_to = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='replies',
        help_text="The earlier message this one replies to.",
    )
    # "Delete for everyone". The text, attachments (including the stored files),
    # mentions and related notifications are erased for good; only an empty
    # tombstone row remains so other people's screens can show "This message
    # was deleted" and replies that pointed at it stay consistent.
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    def __str__(self):
        return f"{self.sender} @ {self.created_at:%H:%M}: {self.text[:40]}"



class ChatMention(models.Model):
    """A person explicitly tagged in a chat message with @username."""
    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name='mentions')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='chat_mentions')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['message', 'user'], name='one_chat_mention_per_message_user'),
        ]

    def __str__(self):
        return f"@{self.user.username} in message {self.message_id}"


class BroadcastMessage(models.Model):
    """A manager message sent once to every eligible branch they manage.

    Each broadcast is copied into each branch's existing group thread, so
    branch staff never gain visibility into another branch's conversation.
    """
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='branch_broadcasts'
    )
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.sender} @ {self.created_at:%Y-%m-%d %H:%M}: {self.text[:50]}"


class ChatAttachment(models.Model):
    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='chat_documents/%Y/%m/')
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.original_name
