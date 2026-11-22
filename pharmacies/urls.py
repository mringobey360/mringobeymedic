from django.urls import path
from . import views

app_name = 'pharmacies'

urlpatterns = [
    path('', views.pharmacy_list_view, name='list'),
    path('<int:pk>/edit/', views.pharmacy_edit_view, name='edit'),
    path('receipt-design/', views.receipt_design_view, name='receipt_design'),
    path('<int:pk>/receipt-design/', views.receipt_design_view, name='receipt_design_branch'),
    path('<int:pk>/receipt-design/reset/', views.receipt_design_reset_view, name='receipt_design_reset'),
    path('<int:pk>/receipt-design/sample.pdf', views.receipt_design_sample_view, name='receipt_design_sample'),
    path('<int:pk>/workplace-location/', views.workplace_location_view, name='workplace_location'),
    path('<int:pk>/switch/', views.pharmacy_switch_view, name='switch'),
    path('<int:pk>/payment/', views.branch_addon_payment_view, name='branch_addon_payment'),
    path('<int:pk>/cancel-addon/', views.pharmacy_cancel_addon_view, name='cancel_addon'),
]
