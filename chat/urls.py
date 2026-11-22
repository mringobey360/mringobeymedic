from django.urls import path

from . import views

app_name = 'chat'

urlpatterns = [
    path('chat/', views.inbox_view, name='inbox'),
    path('chat/broadcast/', views.broadcast_view, name='broadcast'),
    path('chat/all-branches/open/', views.open_all_branches_thread_view, name='open_all_branches'),
    path('chat/group/open/', views.open_group_thread_view, name='open_group'),
    path('chat/customers/open/', views.open_customer_group_view, name='open_customer_group'),
    path('chat/direct/open/', views.open_my_direct_thread_view, name='open_my_direct'),
    path('chat/direct/open/<int:employee_id>/', views.open_direct_thread_view, name='open_direct'),
    path('chat/<int:pk>/', views.thread_detail_view, name='thread_detail'),
    path('chat/<int:pk>/api/messages/', views.messages_feed_api, name='api_messages'),
    path('chat/<int:pk>/api/send/', views.send_message_api, name='api_send'),
    path('chat/<int:pk>/api/messages/<int:message_id>/edit/', views.edit_message_api, name='api_edit_message'),
    path('chat/<int:pk>/api/messages/<int:message_id>/delete/', views.delete_message_api, name='api_delete_message'),
    path('chat/<int:pk>/api/mention-users/', views.mention_users_api, name='api_mention_users'),
    path('chat/<int:pk>/api/typing/', views.typing_api, name='api_typing'),
    path('chat/<int:pk>/api/typing-users/', views.typing_users_api, name='api_typing_users'),
]
