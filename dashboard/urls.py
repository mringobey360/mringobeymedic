from django.urls import path
from . import views, monitor_views

app_name = 'dashboard'

urlpatterns = [
    path('', views.dashboard_home_view, name='home'),
    path('overview/', views.business_overview_view, name='overview'),
    path('workforce/', views.workforce_presence_view, name='workforce'),
    path('my-cash/', views.my_cash_history_view, name='my_cash_history'),
    path('performance/', monitor_views.performance_monitor_view, name='performance'),
    path('performance/export/pdf/', monitor_views.performance_export_pdf, name='performance_pdf'),
    path('performance/export/excel/', monitor_views.performance_export_excel, name='performance_excel'),
    path('reports/', views.reports_view, name='reports'),
    path('reports/custom/', views.custom_reports_view, name='custom_reports'),
    path('reports/export/custom/', views.export_custom_sales_report_pdf, name='export_custom_report_pdf'),
    path('reports/export/custom/excel/', views.export_custom_sales_report_excel, name='export_custom_report_excel'),
    path('reports/export/comparison/', views.export_profit_comparison_pdf, name='export_profit_comparison_pdf'),
    path('reports/export/sales/', views.export_sales_report_pdf, name='export_sales_pdf'),
    path('reports/export/products/', views.export_products_report_pdf, name='export_products_pdf'),
    path('reports/export/profit/', views.export_profit_report_pdf, name='export_profit_pdf'),
    path('reports/payroll/', views.payroll_report_view, name='payroll_report'),
    path('reports/payroll/export/', views.export_payroll_report_pdf, name='export_payroll_pdf'),
]
