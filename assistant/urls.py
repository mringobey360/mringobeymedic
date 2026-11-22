from django.urls import path

from . import views

app_name = 'assistant'

urlpatterns = [
    path('assistant/api/public/', views.public_chat_api, name='public_chat'),
    path('assistant/api/staff/', views.staff_chat_api, name='staff_chat'),
]
