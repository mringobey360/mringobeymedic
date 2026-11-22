from django.urls import path
from . import views

app_name = 'daily_reports'
urlpatterns = [
    path('settings/', views.settings_view, name='settings'),
    path('', views.report_list, name='list'),
    path('public/<uuid:token>/', views.public_report, name='public_report'),
    path('public/<uuid:token>/pdf/', views.public_report_pdf, name='public_report_pdf'),
]
