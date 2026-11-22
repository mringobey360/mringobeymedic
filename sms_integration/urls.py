from django.urls import path
from . import views
from hq import views as hq_views

app_name = 'sms_integration'

urlpatterns = [
    path('usage/', views.sms_usage_view, name='usage'),
    # The customer-facing Premium Debt SMS page is implemented in HQ views
    # because payment settings, notifications and admin approval live there.
    # Expose it here so the existing /sms/debt-sms/ route remains stable.
    path('debt-sms/', hq_views.debt_sms_access_view, name='debt_sms'),
    path('receipt-sms/', hq_views.receipt_sms_access_view, name='receipt_sms'),

    # Public (no login) redirect behind the {link} placeholder in an SMS announcement.
    # Short on purpose — every character here is billed as part of the SMS segment count.
    path('o/<uuid:token>/', views.announcement_open_view, name='announcement_open'),
    path('delivery-report/', views.africa_talking_delivery_report_view, name='africa_talking_delivery_report'),
]
