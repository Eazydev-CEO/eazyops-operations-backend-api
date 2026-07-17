"""Template context shared across the dashboard shell."""
from django.conf import settings

from apps.core import rbac


def site_context(request):
    user = getattr(request, "user", None)
    unread = 0
    if user is not None and getattr(user, "is_authenticated", False):
        try:
            from apps.notifications.models import Notification

            unread = Notification.objects.filter(user=user, is_read=False).count()
        except Exception:  # error pages must render even if the DB is down
            unread = 0

    def can(capability):
        return rbac.user_can(user, capability) if user else False

    return {
        "SITE_NAME": getattr(settings, "SITE_NAME", "EazyOps"),
        "unread_notifications_count": unread,
        "nav_can": {
            "users_manage": can("users.manage"),
            "users_view": can("users.view"),
            "jobs_create": can("jobs.create"),
            "schedules_view": can("schedules.view"),
            "schedules_manage": can("schedules.manage"),
            "audits_view": can("audits.view"),
            "reports_view": can("reports.view"),
            "settings_manage": can("settings.manage"),
            "system_health": can("system.health"),
            "categories_manage": can("categories.manage"),
            "departments_manage": can("departments.manage"),
            "notifications_monitor": can("notifications.monitor"),
            "api_overview": can("api.overview"),
        },
    }
