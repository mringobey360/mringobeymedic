from django.urls import path
from . import views
app_name='customer_registry'
urlpatterns=[
    path('customers/',views.customer_list,name='list'),
    path('customers/register/',views.customer_create,name='create'),
    path('customers/bulk/',views.customer_bulk,name='bulk'),
    path('customers/bulk/template/',views.customer_bulk_template,name='bulk_template'),
    path('customers/<int:pk>/edit/',views.customer_edit,name='edit'),
    path('customers/<int:pk>/delete/',views.customer_delete,name='delete'),
    path('customers/bulk-delete/',views.customer_bulk_delete,name='bulk_delete'),
    path('customers/export/',views.customer_export,name='export'),
    path('customers/<int:pk>/toggle/',views.customer_toggle,name='toggle'),
    path('customers/campaigns/',views.campaign_list,name='campaign_list'),
    path('customers/campaigns/new/',views.campaign_create,name='campaign_create'),
    path('customers/campaigns/<int:pk>/',views.campaign_detail,name='campaign_detail'),
    path('customers/campaigns/<int:pk>/retry/',views.campaign_retry,name='campaign_retry'),
]
