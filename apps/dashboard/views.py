import platform
import sys
from datetime import timedelta

import django
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audits.services import Actions, record_audit
from apps.core.rbac import require_capability
from apps.core.services import SETTINGS_REGISTRY, get_setting, set_setting
from apps.notifications.models import Notification
from apps.operations.models import Job
from apps.scheduling.models import ScheduledTask, SchedulerRun, TaskOccurrence

from .services import dashboard_data


@login_required
def home(request):
    days = request.GET.get("range", "30")
    try:
        days = int(days)
    except ValueError:
        days = 30
    data = dashboard_data(request.user, days=days)
    return render(request, "dashboard/home.html", {
        "data": data,
        "range": data["days"],
    })


@login_required
def data(request):
    """AJAX refresh endpoint for dashboard widgets/charts."""
    days = request.GET.get("range", "30")
    try:
        days = int(days)
    except ValueError:
        days = 30
    return JsonResponse(dashboard_data(request.user, days=days))


# ---------------------------------------------------------------------------
# Custom admin area — system pages
# ---------------------------------------------------------------------------
@login_required
def system_settings(request):
    require_capability(request.user, "settings.manage")
    if request.method == "POST":
        key = request.POST.get("key", "")
        value = (request.POST.get("value", "") or "").strip()
        if key not in SETTINGS_REGISTRY:
            return JsonResponse({"ok": False, "error": "Unknown setting."}, status=400)
        if not value:
            return JsonResponse({"ok": False, "error": "Value cannot be empty."}, status=400)
        try:
            _validate_setting(key, value)
        except ValidationError as exc:
            return JsonResponse({"ok": False, "error": "; ".join(exc.messages)}, status=400)
        obj, old = set_setting(key, value, user=request.user)
        record_audit(
            actor=request.user, action=Actions.SETTING_CHANGED, instance=obj,
            changes={key: [old, value]}, request=request,
        )
        return JsonResponse({"ok": True, "key": key, "value": value})

    rows = []
    for key, (default, description) in SETTINGS_REGISTRY.items():
        rows.append({
            "key": key,
            "value": get_setting(key),
            "default": default,
            "description": description,
        })
    return render(request, "dashboard/manage/settings.html", {"settings_rows": rows})


def _validate_setting(key, value):
    numeric_keys = {
        "job_due_soon_days", "max_upload_mb", "invitation_expiry_days",
        "login_max_failures", "login_lockout_minutes", "occurrence_missed_grace_hours",
    }
    if key in numeric_keys:
        try:
            n = int(value)
        except ValueError:
            raise ValidationError("Must be a whole number.")
        if not (1 <= n <= 100000):
            raise ValidationError("Must be between 1 and 100000.")
    elif key == "notification_emails_enabled":
        if value.lower() not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            raise ValidationError("Use true or false.")


@login_required
def system_health(request):
    require_capability(request.user, "system.health")
    User = get_user_model()
    now = timezone.now()

    db_ok, db_error = True, ""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # pragma: no cover
        db_ok, db_error = False, str(exc)

    pending_migrations = []
    try:
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        pending_migrations = [f"{m.app_label}.{m.name}" for m, _ in plan]
    except Exception:  # pragma: no cover
        pending_migrations = ["<unable to inspect>"]

    last_run = SchedulerRun.objects.first()
    scheduler_age_minutes = None
    if last_run:
        scheduler_age_minutes = int((now - last_run.started_at).total_seconds() // 60)

    health = {
        "db_ok": db_ok,
        "db_error": db_error,
        "db_engine": connection.settings_dict.get("ENGINE", "").rsplit(".", 1)[-1],
        "debug": settings.DEBUG,
        "python_version": platform.python_version(),
        "django_version": django.get_version(),
        "email_backend": settings.EMAIL_BACKEND.rsplit(".", 2)[-2],
        "pending_migrations": pending_migrations,
        "last_scheduler_run": last_run,
        "scheduler_age_minutes": scheduler_age_minutes,
        "scheduler_stale": scheduler_age_minutes is None or scheduler_age_minutes > 60,
        "failed_tasks": ScheduledTask.objects.filter(failure_count__gt=0).count(),
        "missed_7d": TaskOccurrence.objects.filter(
            status=TaskOccurrence.Status.MISSED, scheduled_for__gte=now - timedelta(days=7)
        ).count(),
        "counts": {
            "users": User.objects.count(),
            "active_users": User.objects.filter(is_active=True).count(),
            "jobs": Job.objects.count(),
            "active_jobs": Job.objects.active().count(),
            "notifications_30d": Notification.objects.filter(
                created_at__gte=now - timedelta(days=30)
            ).count(),
            "scheduled_tasks": ScheduledTask.objects.count(),
        },
    }
    return render(request, "dashboard/manage/health.html", {"health": health})


@login_required
def notifications_monitor(request):
    require_capability(request.user, "notifications.monitor")
    qs = Notification.objects.select_related("user").order_by("-created_at")
    ntype = request.GET.get("type", "")
    if ntype:
        qs = qs.filter(type=ntype)
    from django.core.paginator import Paginator
    from apps.notifications.models import NotificationTypes

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))
    stats = {
        "total_30d": Notification.objects.filter(
            created_at__gte=timezone.now() - timedelta(days=30)
        ).count(),
        "unread": Notification.objects.filter(is_read=False).count(),
        "emailed_30d": Notification.objects.filter(
            email_sent=True, created_at__gte=timezone.now() - timedelta(days=30)
        ).count(),
    }
    return render(request, "dashboard/manage/notifications_monitor.html", {
        "page_obj": page,
        "notifications": page.object_list,
        "stats": stats,
        "types": NotificationTypes.choices,
        "current_type": ntype,
        "is_paginated": page.has_other_pages(),
    })


@login_required
def api_overview(request):
    require_capability(request.user, "api.overview")
    throttles = settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {})
    jwt_conf = settings.SIMPLE_JWT
    resources = [
        ("POST /api/v1/auth/token/", "Obtain JWT access + refresh pair", "anon (throttled)"),
        ("POST /api/v1/auth/token/refresh/", "Refresh an access token", "refresh token"),
        ("POST /api/v1/auth/token/verify/", "Verify a token", "any"),
        ("GET /api/v1/me/", "Current user profile", "authenticated"),
        ("GET/PATCH /api/v1/users/", "User administration", "Super Admin (read: +Manager)"),
        ("GET /api/v1/roles/", "Role & capability matrix", "management"),
        ("GET/POST /api/v1/jobs/", "Jobs list/create + filters", "per role"),
        ("POST /api/v1/jobs/{id}/transition/", "Status change", "per role"),
        ("POST /api/v1/jobs/{id}/assign/", "Replace staff assignments", "management"),
        ("GET/POST /api/v1/jobs/{id}/comments/", "Job comments", "per role"),
        ("GET/POST /api/v1/jobs/{id}/attachments/", "Job attachments", "per role"),
        ("GET /api/v1/assignments/", "Assignment records", "management"),
        ("GET/POST /api/v1/scheduled-tasks/", "Recurring task templates", "management"),
        ("GET /api/v1/occurrences/", "Task occurrences (+complete action)", "per role"),
        ("GET /api/v1/notifications/", "Own notifications (+mark read)", "authenticated"),
        ("GET /api/v1/audit-logs/", "Audit trail (read-only)", "management"),
        ("GET /api/v1/dashboard/summary/", "Dashboard aggregate", "authenticated"),
        ("GET /api/v1/reports/{slug}/", "Report data as JSON", "management/viewer"),
    ]
    return render(request, "dashboard/manage/api_overview.html", {
        "resources": resources,
        "throttles": throttles,
        "access_lifetime": jwt_conf.get("ACCESS_TOKEN_LIFETIME"),
        "refresh_lifetime": jwt_conf.get("REFRESH_TOKEN_LIFETIME"),
    })
