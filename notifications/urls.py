from django.urls import path

from . import views

app_name = 'notifications'

urlpatterns = [
    path('notifications/', views.notification_list_view, name='list'),
    path('notifications/tangazo/', views.announcement_create_view, name='announcement_create'),
    path('notifications/open/<int:pk>/', views.notification_open_view, name='open'),
    path('notifications/api/feed/', views.bell_feed_api, name='api_feed'),
    path('notifications/api/announcement-popup/', views.announcement_popup_api, name='api_announcement_popup'),
    path('notifications/api/announcement-popup/dismiss/', views.announcement_popup_dismiss_api, name='api_announcement_popup_dismiss'),
    path('notifications/api/<int:pk>/read/', views.mark_read_api, name='api_mark_read'),
    path('notifications/api/read-all/', views.mark_all_read_api, name='api_mark_all_read'),
    path('notifications/api/push/config/', views.web_push_config_api, name='api_push_config'),
    path('notifications/api/push/subscribe/', views.web_push_subscribe_api, name='api_push_subscribe'),
    path('notifications/api/push/unsubscribe/', views.web_push_unsubscribe_api, name='api_push_unsubscribe'),
]
