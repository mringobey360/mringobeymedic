from django.urls import path
from . import views

app_name = 'sales'

urlpatterns = [
    path('pos/', views.pos_view, name='pos'),
    path('api/search-products/', views.product_search_api, name='product_search_api'),
    path('api/barcode-lookup/', views.barcode_lookup_api, name='barcode_lookup_api'),
    path('api/offline-catalog/', views.offline_catalog_api, name='offline_catalog_api'),
    path('api/pos-context/', views.pos_context_api, name='pos_context_api'),
    path('api/customer-search/', views.customer_search_api, name='customer_search_api'),
    path('api/customer-debt/', views.customer_debt_lookup_api, name='customer_debt_lookup_api'),
    path('api/checkout/', views.checkout_view, name='checkout'),
    path('api/session-status/', views.pos_session_status_view, name='pos_session_status'),
    path('api/sync-offline-sales/', views.sync_offline_sales_view, name='sync_offline_sales'),
    path('history/', views.sale_list_view, name='sale_list'),
    path('needs-review/', views.sales_needing_review_view, name='needs_review'),
    path('late-entries/', views.late_entry_reconcile_view, name='late_entry_reconcile'),
    path('needs-review/<int:pk>/mark-reviewed/', views.mark_sale_reviewed_view, name='mark_reviewed'),
    path('returns/', views.sale_returns_list_view, name='returns'),
    path('returns/review/', views.void_review_view, name='void_review'),
    path('returns/<int:pk>/decide/', views.sale_return_decide_view, name='return_decide'),
    path('<int:pk>/return-info/', views.sale_return_info_api, name='return_info'),
    path('<int:pk>/return/', views.sale_return_request_view, name='return_request'),
    path('debts/', views.debt_list_view, name='debt_list'),
    path('debts/reports/', views.debt_reports_view, name='debt_reports'),
    path('debts/reports/export.csv', views.debt_report_csv_view, name='debt_report_csv'),
    path('debts/reports/export.pdf', views.debt_report_pdf_view, name='debt_report_pdf'),
    path('debts/<int:pk>/', views.debt_detail_view, name='debt_detail'),
    path('debts/<int:pk>/pay/', views.debt_record_payment_view, name='debt_pay'),
    path('debts/<int:pk>/edit/', views.debt_edit_view, name='debt_edit'),
    path('debts/<int:pk>/remind/', views.debt_send_reminder_view, name='debt_remind'),
    path('receipts/<int:pk>/pdf/', views.sale_receipt_pdf_view, name='receipt_pdf'),
    path('receipts/<int:pk>/send-sms/', views.send_receipt_sms_manual_view, name='send_receipt_sms'),
    path('receipts/<int:pk>/edit-customer/', views.edit_sale_customer_contact_view, name='edit_sale_customer_contact'),
    path('receipt/<uuid:token>/download/', views.public_sale_receipt_pdf_view, name='public_receipt_pdf'),
]
