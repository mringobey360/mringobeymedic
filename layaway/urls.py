from django.urls import path
from . import views
app_name='layaway'
urlpatterns=[path('',views.plan_list,name='list'),path('reports/export.pdf',views.plan_report_pdf,name='report_pdf'),path('premium-messaging/',views.sms_packages,name='sms_packages'),path('new/',views.plan_create,name='create'),path('<int:pk>/',views.plan_detail,name='detail'),path('<int:pk>/resolve/',views.resolve_plan,name='resolve'),path('<int:pk>/handover/',views.handover_plan,name='handover')]
