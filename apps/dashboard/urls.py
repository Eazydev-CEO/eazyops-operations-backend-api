from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/data/", views.data, name="data"),

    # Custom admin area — system pages
    path("manage/settings/", views.system_settings, name="manage_settings"),
    path("manage/health/", views.system_health, name="manage_health"),
    path("manage/notifications/", views.notifications_monitor, name="manage_notifications"),
    path("manage/api/", views.api_overview, name="manage_api"),
]
