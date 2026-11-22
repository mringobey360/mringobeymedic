from django.urls import path

from . import views

app_name = 'testimonials'

urlpatterns = [
    # Platform superadmin
    path('hq/testimonials/', views.admin_dashboard, name='admin'),
    path('hq/testimonials/settings/', views.admin_settings, name='admin_settings'),
    path('hq/testimonials/invite/', views.admin_invite, name='admin_invite'),
    path('hq/testimonials/invitation/<int:pk>/revoke/', views.admin_revoke, name='admin_revoke'),
    path('hq/testimonials/review/<int:pk>/<str:action>/', views.admin_moderate, name='admin_moderate'),
    # Customer popup (JSON)
    path('testimonials/popup/', views.popup_state, name='popup'),
    path('testimonials/submit/', views.popup_submit, name='submit'),
    path('testimonials/later/', views.popup_later, name='later'),
    path('testimonials/decline/', views.popup_decline, name='decline'),
]
