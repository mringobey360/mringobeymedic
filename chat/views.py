from datetime import timedelta
import logging
import re
import time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse, HttpResponseForbidden, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods

from accounts.decorators import admin_required, employee_or_admin_required
from accounts.models import User
from notifications.models import Notification
from notifications.utils import notify

from .models import ChatThread, ChatMessage, BroadcastMessage, ChatMention, ChatAttachment

MESSAGE_FEED_LIMIT = 100
logger = logging.getLogger(__name__)


def _active_managed_branches(user):
    """Branches the manager can use for an all-branches broadcast.

    A branch is eligible only when the manager's own subscription is active;
    the global subscription lock middleware already prevents normal use when
    the manager's subscription is expired.
    """
    if user.is_assistant_role():
        profile = getattr(user, 'assistant_profile', None)
        from pharmacies.models import Pharmacy
        return profile.accessible_pharmacies() if profile and profile.active else Pharmacy.objects.none()
    return user.accessible_management_pharmacies().all().order_by('name')


@employee_or_admin_required
def inbox_view(request):
    """Chat home: manager gets an all-branches broadcast hub plus branch chats."""
    if request.user.is_superuser:
        customer_thread, _ = ChatThread.objects.get_or_create(manager=request.user, pharmacy=None, employee=None, platform_group=True)
        return render(request, 'chat/inbox.html', {'employees': None, 'pharmacy': None, 'managed_branches': [], 'all_branches_threads': [], 'customer_group_thread': customer_thread, 'is_manager': True, 'is_platform_owner': True})
    if request.user.is_management_user():
        if request.user.is_assistant_role():
            profile = getattr(request.user, 'assistant_profile', None)
            manager = profile.manager if profile and profile.active else None
            branches = _active_managed_branches(manager) if manager else User.objects.none()
            current = getattr(request, 'pharmacy', None)
            employees = User.objects.filter(pharmacy=current, role=User.Role.EMPLOYEE, is_active=True) if current else User.objects.none()
            all_branches_thread = None
            if manager and profile and profile.branch_access_mode == profile.BranchAccessMode.ALL_MANAGER_BRANCHES:
                all_branches_thread, _ = ChatThread.objects.get_or_create(manager=manager, pharmacy=None, employee=None, platform_group=False)
            return render(request, 'chat/inbox.html', {
                'employees': employees, 'pharmacy': current, 'managed_branches': branches,
                'all_branches_thread': all_branches_thread, 'customer_group_thread': None,
                'is_manager': False, 'is_assistant': True, 'assistant_profile': profile,
            })
        owner = User.objects.filter(is_superuser=True, is_active=True).order_by('pk').first()
        customer_group_thread = None
        if owner:
            customer_group_thread, _ = ChatThread.objects.get_or_create(
                manager=owner, pharmacy=None, employee=None, platform_group=True
            )
        branches = _active_managed_branches(request.user)
        current = getattr(request, 'pharmacy', None)
        employees = User.objects.filter(
            pharmacy=current, role=User.Role.EMPLOYEE, is_active=True
        ) if current else User.objects.none()
        all_branches_thread, _ = ChatThread.objects.get_or_create(
            manager=request.user, pharmacy=None, employee=None, platform_group=False
        )
        return render(request, 'chat/inbox.html', {
            'employees': employees,
            'pharmacy': current,
            'managed_branches': branches,
            'all_branches_thread': all_branches_thread,
            'customer_group_thread': customer_group_thread,
            'is_manager': True,
        })

    # Shared cross-branch space: the employee's manager(s) All Branches
    # thread(s), where staff from every branch that manager runs can share
    # updates and instructions together.
    global_threads = ChatThread.objects.filter(
        manager__isnull=False, pharmacy__isnull=True, employee__isnull=True, platform_group=False,
        manager__managed_pharmacies=request.user.pharmacy,
    ).select_related('manager').distinct()
    return render(request, 'chat/inbox.html', {
        'employees': None,
        'pharmacy': request.user.pharmacy,
        'managed_branches': None,
        'all_branches_threads': global_threads,
        'is_manager': False, 'is_platform_owner': False, 'customer_group_thread': None,
    })


@admin_required
def broadcast_view(request):
    """Open the manager's single live conversation shared by all managed branches."""
    manager = request.user
    if request.user.is_assistant_role():
        profile = getattr(request.user, 'assistant_profile', None)
        if not profile or not profile.active:
            raise PermissionDenied('Your Assistant account is inactive.')
        manager = profile.manager
    thread, _ = ChatThread.objects.get_or_create(
        manager=manager, pharmacy=None, employee=None, platform_group=False
    )
    return redirect('chat:thread_detail', pk=thread.pk)


@employee_or_admin_required
def open_all_branches_thread_view(request):
    """Open the manager-owned all-branches thread."""
    if request.user.is_assistant_role():
        profile = getattr(request.user, 'assistant_profile', None)
        if not profile or not profile.active or profile.branch_access_mode != profile.BranchAccessMode.ALL_MANAGER_BRANCHES:
            raise PermissionDenied('All-branches chat is available only when this Assistant has All Manager Branches access.')
        manager = profile.manager
    elif request.user.is_management_user():
        manager = request.user
    else:
        manager = request.user.pharmacy.managers.order_by('pk').first() if request.user.pharmacy else None
    if not manager:
        messages.error(request, "No manager is assigned to your branch.")
        return redirect('chat:inbox')

    thread, _ = ChatThread.objects.get_or_create(
        manager=manager, pharmacy=None, employee=None
    )
    return redirect('chat:thread_detail', pk=thread.pk)


@employee_or_admin_required
def open_group_thread_view(request):
    pharmacy = request.pharmacy
    branch_id = request.GET.get('branch')
    if request.user.is_assistant_role():
        profile = getattr(request.user, 'assistant_profile', None)
        if not profile or not profile.active:
            raise PermissionDenied('Your Assistant account is inactive.')
        if branch_id:
            try:
                requested_branch_id = int(branch_id)
            except (TypeError, ValueError):
                raise PermissionDenied('Invalid branch selection.')
            pharmacy = profile.accessible_pharmacies().filter(pk=requested_branch_id).first()
            if pharmacy is None:
                raise PermissionDenied('You cannot access that branch.')
        else:
            pharmacy = getattr(request, 'pharmacy', None)
        if pharmacy is None or not profile.can_access_pharmacy(pharmacy):
            raise PermissionDenied('You cannot access that branch.')
    elif request.user.is_management_user() and branch_id:
        pharmacy = get_object_or_404(request.user.accessible_management_pharmacies(), pk=branch_id)
    if not pharmacy:
        messages.error(request, "No branch is assigned to your account.")
        return redirect('chat:inbox')
    thread, _ = ChatThread.objects.get_or_create(
        pharmacy=pharmacy, employee=None, manager=None
    )
    return redirect('chat:thread_detail', pk=thread.pk)


@admin_required
def open_direct_thread_view(request, employee_id):
    employee = get_object_or_404(User, pk=employee_id, role=User.Role.EMPLOYEE)
    if request.user.is_assistant_role():
        profile = getattr(request.user, 'assistant_profile', None)
        allowed = bool(profile and profile.active and profile.accessible_pharmacies().filter(pk=employee.pharmacy_id).exists())
    else:
        allowed = employee.pharmacy_id in request.user.accessible_management_pharmacies().values_list('pk', flat=True)
    if not allowed:
        return HttpResponseForbidden("You cannot open a chat with an employee from a branch you do not manage.")
    thread, _ = ChatThread.objects.get_or_create(
        pharmacy=employee.pharmacy, employee=employee, manager=None
    )
    return redirect('chat:thread_detail', pk=thread.pk)


@employee_or_admin_required
def open_my_direct_thread_view(request):
    if request.user.is_management_user():
        return redirect('chat:inbox')
    if not request.user.pharmacy:
        messages.error(request, "No branch is assigned to your account.")
        return redirect('chat:inbox')
    thread, _ = ChatThread.objects.get_or_create(
        pharmacy=request.user.pharmacy, employee=request.user, manager=None
    )
    return redirect('chat:thread_detail', pk=thread.pk)


@login_required
def open_customer_group_view(request):
    if not (request.user.is_superuser or request.user.is_management_user()):
        return HttpResponseForbidden('Customer group is available to system owner and customer owners only.')
    owner = request.user if request.user.is_superuser else User.objects.filter(is_superuser=True, is_active=True).order_by('pk').first()
    if not owner:
        return HttpResponseForbidden('No system owner is configured.')
    thread, _ = ChatThread.objects.get_or_create(manager=owner, pharmacy=None, employee=None, platform_group=True)
    return redirect('chat:thread_detail', pk=thread.pk)


@login_required
def thread_detail_view(request, pk):
    thread = get_object_or_404(ChatThread, pk=pk)
    if not thread.user_can_access(request.user):
        raise Http404("No ChatThread matches the given query.")
    # Mark only the chat notifications visible to this user/branch as read.
    # Do not touch another branch's notifications when a manager switches.
    chat_notifications = Notification.objects.filter(
        recipient=request.user,
        category=Notification.Category.CHAT,
        is_read=False,
    ).filter(
        Q(pharmacy=thread.pharmacy) | Q(pharmacy__isnull=True)
    )
    chat_notifications.filter(link_url__startswith=f'/chat/{thread.pk}/').update(
        is_read=True, read_at=timezone.now()
    )

    recent_messages = (
        thread.messages
        .select_related('sender', 'sender__pharmacy', 'reply_to', 'reply_to__sender')
        .prefetch_related('attachments', 'reply_to__attachments', 'mentions__user')
        .order_by('-created_at')[:MESSAGE_FEED_LIMIT]
    )
    recent_messages = list(reversed(recent_messages))
    initial_messages = [_serialize_message(m, request.user) for m in recent_messages]

    header_user = None
    if not thread.is_group:
        header_user = thread.employee
    elif request.user.is_employee_role():
        header_user = thread.manager if thread.is_all_branches else request.user

    header_name = thread.label
    if header_user:
        header_name = header_user.get_full_name() or header_user.username
    header_photo_url = header_user.profile_photo.url if header_user and header_user.profile_photo else ''
    header_initials = (header_name or '?')[0].upper()

    return render(request, 'chat/thread.html', {
        'thread': thread, 'initial_messages': initial_messages,
        'server_now': timezone.now().isoformat(),
        'header_user': header_user, 'header_name': header_name,
        'header_photo_url': header_photo_url, 'header_initials': header_initials,
    })


def chat_timestamp(dt):
    local_dt = timezone.localtime(dt)
    today = timezone.localdate()
    if local_dt.date() == today:
        return f"Today · {local_dt.strftime('%I:%M %p').lstrip('0')}"
    if local_dt.date() == today - timedelta(days=1):
        return f"Yesterday · {local_dt.strftime('%I:%M %p').lstrip('0')}"
    return f"{local_dt.strftime('%b')} {local_dt.day}, {local_dt.year} · {local_dt.strftime('%I:%M %p').lstrip('0')}"


def _eligible_mention_users(thread):
    """Users who can be tagged in this thread, ordered for predictable autocomplete."""
    return list(thread.participants().filter(is_active=True).order_by('first_name', 'last_name', 'username'))


def _extract_mentions(thread, text):
    """Resolve @username tokens only against current thread participants."""
    usernames = set(re.findall(r'(?<![\w@])@([A-Za-z0-9_.+\-]+)', text or ''))
    if not usernames:
        return []
    users = {u.username.lower(): u for u in _eligible_mention_users(thread)}
    return [users[name.lower()] for name in usernames if name.lower() in users]


def _reply_preview(m, request_user):
    """Small summary of the message that `m` replies to, shown as a quote above the reply."""
    original = m.reply_to
    if original is None:
        return None
    if original.sender_id == request_user.id:
        sender_name = 'You'
    else:
        sender_name = original.sender.get_full_name() or original.sender.username
    if original.deleted_at:
        return {'id': original.id, 'sender_name': sender_name, 'text': '', 'attachment_name': '', 'is_deleted': True}
    attachments = list(original.attachments.all())
    return {
        'id': original.id,
        'sender_name': sender_name,
        'text': (original.text or '').strip()[:120],
        'attachment_name': attachments[0].original_name if attachments else '',
        'is_deleted': False,
    }


def _serialize_message(m, request_user):
    """Serialize a chat message without ever confusing ChatMention with User.

    Mentions are ChatMention rows; the public user identity lives on
    mention.user. Keep this serializer defensive because it is used by both
    the initial feed and the incremental polling endpoint.

    A deleted message is serialized as an empty tombstone: no text, no
    attachments, no mentions and no reply quote.
    """
    sender = m.sender
    is_deleted = m.deleted_at is not None
    mentions = []
    if not is_deleted:
        for mention in m.mentions.select_related('user').all():
            user = mention.user
            if not user or not user.is_active:
                continue
            mentions.append({
                'id': user.id,
                'username': user.username,
                'name': user.get_full_name() or user.username,
            })

    profile_photo = getattr(sender, 'profile_photo', None)
    sender_name = sender.get_full_name() or sender.username
    pharmacy = getattr(sender, 'pharmacy', None)

    return {
        'id': m.id,
        'text': '' if is_deleted else m.text,
        'sender_name': sender_name,
        'is_mine': m.sender_id == request_user.id,
        'created_at_display': chat_timestamp(m.created_at),
        'created_at_iso': m.created_at.isoformat(),
        'edited_at_iso': m.edited_at.isoformat() if m.edited_at else '',
        'is_edited': bool(m.edited_at) and not is_deleted,
        'is_deleted': is_deleted,
        'deleted_at_iso': m.deleted_at.isoformat() if m.deleted_at else '',
        'sender_photo_url': profile_photo.url if profile_photo else '',
        'sender_initials': (sender_name or '?')[0].upper(),
        'mentions': mentions,
        'reply_to': None if is_deleted else _reply_preview(m, request_user),
        'attachments': [] if is_deleted else [
            {'id': a.id, 'url': a.file.url, 'name': a.original_name, 'content_type': a.content_type, 'size': a.size}
            for a in m.attachments.all()
        ],
        'sender_branch': (
            pharmacy.name if pharmacy else ('Management' if sender.is_admin_role() else '')
        ),
    }


# --- typing indicator -------------------------------------------------------
# One small cache entry per thread ({user_id: (timestamp, name)}) instead of one
# entry per participant. The old approach loaded EVERY participant of the thread
# (the customers group has every shop owner in it) and did one cache lookup each,
# on every poll. Now it is a single cache read and no database query.
TYPING_FRESH_SECONDS = 4


def _typing_key(thread):
    return f"chat-typing:{thread.pk}"


def _mark_typing(thread, user):
    now = time.time()
    state = cache.get(_typing_key(thread)) or {}
    state = {uid: v for uid, v in state.items() if now - v[0] <= TYPING_FRESH_SECONDS}
    state[user.pk] = (now, user.get_full_name() or user.username)
    cache.set(_typing_key(thread), state, timeout=TYPING_FRESH_SECONDS * 3)


def _typing_names(thread, user):
    now = time.time()
    state = cache.get(_typing_key(thread)) or {}
    fresh = [(ts, name) for uid, (ts, name) in state.items()
             if uid != user.pk and now - ts <= TYPING_FRESH_SECONDS]
    return [name for _ts, name in sorted(fresh)]


@employee_or_admin_required
def messages_feed_api(request, pk):
    """New messages after `after_id`, plus edits/deletions made since `since`.

    `changes` lets everyone's screen update when somebody edits or deletes a
    message they already have. `server_time` is echoed back as the next `since`
    so the client never depends on its own clock.
    """
    thread = get_object_or_404(ChatThread, pk=pk)
    if not thread.user_can_access(request.user):
        raise Http404("No ChatThread matches the given query.")
    try:
        after_id = int(request.GET.get('after_id', 0) or 0)
    except ValueError:
        after_id = 0
    server_time = timezone.now()

    base_qs = (
        thread.messages
        .select_related('sender', 'sender__pharmacy', 'reply_to', 'reply_to__sender')
        .prefetch_related('mentions__user', 'attachments', 'reply_to__attachments')
    )
    qs = base_qs
    if after_id:
        qs = qs.filter(pk__gt=after_id)
    else:
        qs = qs.order_by('-created_at')[:MESSAGE_FEED_LIMIT]
        qs = reversed(list(qs))
    items = [_serialize_message(m, request.user) for m in qs]
    latest_id = items[-1]['id'] if items else after_id

    changes = []
    since = parse_datetime(request.GET.get('since', '') or '')
    if since is not None and after_id:
        changed = (
            base_qs.filter(pk__lte=after_id)
            .filter(Q(edited_at__gt=since) | Q(deleted_at__gt=since))
        )
        changes = [_serialize_message(m, request.user) for m in changed[:100]]

    return JsonResponse({
        'messages': items, 'latest_id': latest_id,
        'changes': changes, 'server_time': server_time.isoformat(),
        # Typing users ride along with the feed, so the browser needs only one poll.
        'typing_users': _typing_names(thread, request.user),
    })


@employee_or_admin_required
def mention_users_api(request, pk):
    thread = get_object_or_404(ChatThread, pk=pk)
    if not thread.user_can_access(request.user):
        raise Http404("No ChatThread matches the given query.")
    q = (request.GET.get('q') or '').strip().lstrip('@').lower()
    users = _eligible_mention_users(thread)
    if q:
        users = [u for u in users if q in (u.get_full_name() or '').lower() or q in u.username.lower()]
    users = users[:12]
    return JsonResponse({'users': [
        {'id': u.id, 'username': u.username, 'name': u.get_full_name() or u.username,
         'role': u.get_role_display(), 'photo_url': u.profile_photo.url if u.profile_photo else ''}
        for u in users
    ]})


@employee_or_admin_required
@require_http_methods(["POST"])
def typing_api(request, pk):
    thread = get_object_or_404(ChatThread, pk=pk)
    if not thread.user_can_access(request.user):
        raise Http404("No ChatThread matches the given query.")
    _mark_typing(thread, request.user)
    return JsonResponse({'ok': True})


@employee_or_admin_required
def typing_users_api(request, pk):
    """Kept for older cached pages; new pages get typing_users from the message feed."""
    thread = get_object_or_404(ChatThread, pk=pk)
    if not thread.user_can_access(request.user):
        raise Http404("No ChatThread matches the given query.")
    return JsonResponse({'typing_users': _typing_names(thread, request.user)})


@employee_or_admin_required
@require_http_methods(["POST"])
def edit_message_api(request, pk, message_id):
    # The URL carries BOTH the thread id (pk) and the message id.
    message = get_object_or_404(ChatMessage.objects.select_related('thread'), pk=message_id, thread_id=pk)
    if not message.thread.user_can_access(request.user):
        raise Http404("No ChatThread matches the given query.")
    if message.sender_id != request.user.id:
        return HttpResponseForbidden("You can only edit your own messages.")
    if message.deleted_at:
        return JsonResponse({'error': "This message was deleted and can no longer be edited."}, status=400)
    text = (request.POST.get('text') or '').strip()
    uploaded = request.FILES.get('file')
    if not text and not uploaded:
        return JsonResponse({'error': "Write a message or attach a document."}, status=400)
    if len(text) > 5000:
        return JsonResponse({'error': "Message is too long."}, status=400)
    if uploaded:
        allowed = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.txt', '.ppt', '.pptx', '.jpg', '.jpeg', '.png', '.webp'}
        import os
        if os.path.splitext(uploaded.name)[1].lower() not in allowed:
            return JsonResponse({'error': 'File type is not supported.'}, status=400)
        if uploaded.size > 10 * 1024 * 1024:
            return JsonResponse({'error': 'Maximum file size is 10 MB.'}, status=400)
    if text == message.text:
        return JsonResponse({'message': _serialize_message(message, request.user), 'client_token': str(message.client_token) if message.client_token else ''})
    message.text = text
    message.edited_at = timezone.now()
    message.save(update_fields=['text', 'edited_at'])
    message.mentions.all().delete()
    if uploaded:
        ChatAttachment.objects.create(
            message=message, file=uploaded, original_name=uploaded.name[:255],
            content_type=getattr(uploaded, 'content_type', '')[:120], size=uploaded.size,
        )
    tagged_users = _extract_mentions(message.thread, text)
    ChatMention.objects.bulk_create([ChatMention(message=message, user=u) for u in tagged_users])
    for user in tagged_users:
        if user.id != request.user.id:
            sender_name = request.user.get_full_name() or request.user.username
            try:
                notify(
                    user, title=f"{sender_name} mentioned you",
                    message=f"You were tagged in an edited chat message: {text[:180]}",
                    category=Notification.Category.CHAT, level=Notification.Level.INFO,
                    link_url=f"/chat/{message.thread.pk}/#message-{message.pk}",
                    pharmacy=(user.pharmacy if message.thread.is_all_branches else message.thread.pharmacy),
                )
            except Exception:
                # A notification failure must never make the chat message fail.
                logger.exception("Failed to create chat mention notification for message %s", message.pk)
    return JsonResponse({'message': _serialize_message(message, request.user), 'client_token': str(message.client_token) if message.client_token else ''})


@employee_or_admin_required
@require_http_methods(["POST"])
def delete_message_api(request, pk, message_id):
    """Delete one of MY messages for everyone in the chat, permanently.

    The text, attachments (rows AND stored files), mentions and any notification
    that quoted the message are erased and cannot be recovered. An empty
    tombstone is kept so other screens can show "This message was deleted".
    """
    message = get_object_or_404(ChatMessage.objects.select_related('thread'), pk=message_id, thread_id=pk)
    thread = message.thread
    if not thread.user_can_access(request.user):
        raise Http404("No ChatThread matches the given query.")
    if message.sender_id != request.user.id:
        return JsonResponse({'error': "You can only delete your own messages."}, status=403)

    if not message.deleted_at:
        with transaction.atomic():
            locked = ChatMessage.objects.select_for_update().get(pk=message.pk)
            if not locked.deleted_at:
                attachments = list(locked.attachments.all())
                locked.text = ''
                locked.reply_to = None
                locked.deleted_at = timezone.now()
                locked.save(update_fields=['text', 'reply_to', 'deleted_at'])
                locked.mentions.all().delete()
                locked.attachments.all().delete()
                Notification.objects.filter(
                    category=Notification.Category.CHAT,
                    link_url=f"/chat/{thread.pk}/#message-{locked.pk}",
                ).delete()
                stored_files = [a.file for a in attachments if a.file]
                # Remove the files only once the database change has really committed.
                transaction.on_commit(lambda: _erase_stored_files(stored_files))
            message = locked

    message = (ChatMessage.objects.select_related('sender', 'sender__pharmacy').get(pk=message.pk))
    return JsonResponse({'message': _serialize_message(message, request.user)})


def _erase_stored_files(files):
    for f in files:
        try:
            f.delete(save=False)
        except Exception:  # noqa: BLE001 - a storage hiccup must not undo the deletion
            logger.exception("Could not remove chat attachment file %s", getattr(f, 'name', ''))


@employee_or_admin_required
@require_http_methods(["POST"])
def send_message_api(request, pk):
    thread = get_object_or_404(ChatThread, pk=pk)
    if not thread.user_can_access(request.user):
        raise Http404("No ChatThread matches the given query.")
    text = (request.POST.get('text') or '').strip()
    uploaded = request.FILES.get('file')
    if not text and not uploaded:
        return JsonResponse({'error': "Write a message or attach a document."}, status=400)
    if len(text) > 5000:
        return JsonResponse({'error': "Message is too long."}, status=400)
    if uploaded:
        allowed = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.txt', '.ppt', '.pptx', '.jpg', '.jpeg', '.png', '.webp'}
        import os
        if os.path.splitext(uploaded.name)[1].lower() not in allowed:
            return JsonResponse({'error': 'File type is not supported.'}, status=400)
        if uploaded.size > 10 * 1024 * 1024:
            return JsonResponse({'error': 'Maximum file size is 10 MB.'}, status=400)

    reply_to = None
    raw_reply_to = (request.POST.get('reply_to') or '').strip()
    if raw_reply_to:
        try:
            reply_to = ChatMessage.objects.select_related('sender').filter(
                pk=int(raw_reply_to), thread=thread, deleted_at__isnull=True
            ).first()
        except ValueError:
            reply_to = None
        if reply_to is None:
            return JsonResponse({'error': "The message you are replying to is no longer available."}, status=400)

    # Client tokens make retries idempotent. A timeout or a failed response
    # must never create a second copy of the same chat message.
    # Client tokens are an idempotency aid, not a reason to reject an otherwise
    # valid message. Older cached browsers may send values such as "undefined"
    # or a non-UUID token. Convert those deterministically to a UUID so retries
    # remain idempotent instead of returning HTTP 400.
    import uuid
    raw_client_token = (request.POST.get('client_token') or request.headers.get('X-Client-Token') or '').strip()
    if len(raw_client_token) > 200:
        raw_client_token = raw_client_token[:200]
    if raw_client_token:
        try:
            client_token = uuid.UUID(raw_client_token)
        except (ValueError, AttributeError, TypeError):
            client_token = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"pharmacy-manager-chat:{request.user.pk}:{thread.pk}:{raw_client_token}",
            )
    else:
        client_token = uuid.uuid4()

    if client_token:
        existing = (ChatMessage.objects
                    .select_related('sender', 'sender__pharmacy', 'thread')
                    .prefetch_related('mentions__user', 'attachments')
                    .filter(client_token=client_token, thread=thread, sender=request.user)
                    .first())
        if existing:
            return JsonResponse({'message': _serialize_message(existing, request.user), 'duplicate': True, 'client_token': str(existing.client_token) if existing.client_token else ''})

    try:
        with transaction.atomic():
            message = ChatMessage.objects.create(
                thread=thread, sender=request.user, text=text or '', client_token=client_token, reply_to=reply_to,
            )
            if uploaded:
                ChatAttachment.objects.create(message=message, file=uploaded, original_name=uploaded.name[:255], content_type=getattr(uploaded, 'content_type', '')[:120], size=uploaded.size)
            tagged_users = _extract_mentions(thread, text)
            ChatMention.objects.bulk_create(
                [ChatMention(message=message, user=u) for u in tagged_users],
                ignore_conflicts=True,
            )
    except IntegrityError:
        # Two identical retries can arrive at the same time. The unique
        # client_token makes one request the winner; return that message to
        # the loser instead of producing a 500 or a duplicate chat message.
        if client_token:
            existing = (ChatMessage.objects
                        .select_related('sender', 'sender__pharmacy', 'thread')
                        .prefetch_related('mentions__user', 'attachments')
                        .filter(client_token=client_token, thread=thread, sender=request.user)
                        .first())
            if existing:
                return JsonResponse({'message': _serialize_message(existing, request.user), 'duplicate': True, 'client_token': str(existing.client_token) if existing.client_token else ''})
        logger.exception("Unexpected chat message integrity error for thread %s", thread.pk)
        return JsonResponse({'error': 'Message could not be saved. Please try again.'}, status=409)
    other_participants = [p for p in thread.participants() if p.id != request.user.id]
    sender_name = request.user.get_full_name() or request.user.username
    tagged_ids = {u.id for u in tagged_users}
    for participant in other_participants:
        if participant.id in tagged_ids:
            title = f"{sender_name} mentioned you"
            note = f"You were tagged in a chat message: {text[:180]}"
        elif reply_to is not None and participant.id == reply_to.sender_id:
            title = f"{sender_name} replied to your message"
            note = text[:200]
        else:
            if thread.is_group:
                group_name = thread.label if thread.is_customer_group else (thread.pharmacy.name if thread.pharmacy_id else "All branches")
                title = f"New message — {group_name}"
            else:
                title = f"Message from {sender_name}"
            note = text[:200]
        try:
            notify(
                participant,
                title=title,
                message=note, category=Notification.Category.CHAT, level=Notification.Level.INFO,
                link_url=f"/chat/{thread.pk}/#message-{message.pk}",
                pharmacy=(participant.pharmacy if thread.is_all_branches else thread.pharmacy),
            )
        except Exception:
            # Notification creation is best-effort; sending the chat message must remain successful.
            logger.exception("Failed to create chat notification for message %s and user %s", message.pk, participant.pk)

    return JsonResponse({'message': _serialize_message(message, request.user), 'client_token': str(message.client_token) if message.client_token else ''})
