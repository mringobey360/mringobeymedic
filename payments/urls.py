from django.urls import path

from . import views

app_name = 'payments'

urlpatterns = [
    path('marketplace/service/<int:subscription_id>/pay/', views.initiate_marketplace_service_payment, name='marketplace_service_payment'),
    # --- Start a payment (per purpose) ---
    path('pay/subscription/<uuid:public_id>/', views.initiate_subscription_payment, name='pay_subscription'),
    path('pay/renewal/', views.initiate_renewal_payment, name='pay_renewal'),
    path('pay/branch/<int:pk>/', views.initiate_branch_addon_payment, name='pay_branch_addon'),
    path('pay/branch/<int:pk>/renew/', views.initiate_branch_addon_renewal_payment, name='pay_branch_addon_renewal'),
    path('pay/sms-debt/', views.initiate_sms_debt_payment, name='pay_sms_debt'),
    path('pay/sms-receipt/', views.initiate_sms_receipt_payment, name='pay_sms_receipt'),
    path('pay/sms-marketing/', views.initiate_sms_marketing_payment, name='pay_sms_marketing'),
    path('pay/intelligent-report/', views.initiate_daily_report_payment, name='pay_daily_report'),
    path('pay/layaway-sms/', views.initiate_layaway_sms_payment, name='pay_layaway_sms'),

    # --- Retry a failed/cancelled/invalid payment with a fresh order ---
    path('retry/<uuid:merchant_reference>/', views.retry_view, name='retry'),

    # --- Branded checkout + tracking / callbacks ---
    path('checkout/<uuid:merchant_reference>/', views.checkout_view, name='checkout'),
    path('cancel/<uuid:merchant_reference>/', views.cancel_view, name='cancel'),
    path('request/<uuid:merchant_reference>/cancel/', views.cancel_payment_request_view, name='cancel_request'),
    path('receipt/<uuid:merchant_reference>/', views.receipt_view, name='receipt'),
    path('mobile/<uuid:merchant_reference>/pay/', views.abliner_pay_view, name='abliner_pay'),
    path('mobile/<uuid:merchant_reference>/card/', views.abliner_switch_view, name='abliner_switch'),
    path('status/<uuid:merchant_reference>/', views.pending_view, name='pending'),
    path('status/<uuid:merchant_reference>/check/', views.status_check_view, name='status_check'),
    path('ipn/', views.ipn_view, name='ipn'),
    path('webhooks/abliner/', views.abliner_webhook_view, name='abliner_webhook'),
    path('return/', views.return_view, name='return'),
]
