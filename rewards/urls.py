from django.urls import path
from . import views

app_name = "rewards"
urlpatterns = [
    path("", views.overview, name="overview"),
    path("vip-settings/", views.vip_settings, name="vip-settings"),
    path("propose/", views.propose_rewards, name="propose"),
    path("<int:pk>/approve/", views.approve_reward, name="approve"),
    path("<int:pk>/reject/", views.reject_reward, name="reject"),
    path("<int:pk>/paid/", views.mark_reward_paid, name="paid"),
]
