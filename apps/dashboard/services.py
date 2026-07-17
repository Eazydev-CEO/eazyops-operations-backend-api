"""Dashboard aggregation — shared by the web dashboard and the API summary."""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from apps.audits.models import AuditLog
from apps.core.rbac import Roles
from apps.notifications.models import Notification
from apps.operations.models import Job
from apps.reports.services import jobs_by_priority, jobs_by_status, staff_workload
from apps.scheduling.models import ScheduledTask, SchedulerRun, TaskOccurrence


def dashboard_data(user, days=30):
    """All widget data for the requesting user's visibility scope."""
    days = max(7, min(int(days or 30), 365))
    now = timezone.now()
    since = now - timedelta(days=days)
    jobs = Job.objects.visible_to(user)
    is_management = user.role in (Roles.SUPER_ADMIN, Roles.MANAGER)

    stats = {
        "total_jobs": jobs.count(),
        "open_jobs": jobs.filter(status=Job.Status.OPEN).count(),
        "assigned_jobs": jobs.filter(status=Job.Status.ASSIGNED).count(),
        "in_progress_jobs": jobs.filter(status=Job.Status.IN_PROGRESS).count(),
        "awaiting_review_jobs": jobs.filter(status=Job.Status.AWAITING_REVIEW).count(),
        "overdue_jobs": jobs.overdue().count(),
        "completed_recent": jobs.filter(
            status=Job.Status.COMPLETED, completed_at__gte=since
        ).count(),
        "active_jobs": jobs.active().count(),
    }

    # Weekly completion trend — last 8 ISO weeks
    trend = []
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    for i in range(7, -1, -1):
        start = week_start - timedelta(weeks=i)
        end = start + timedelta(weeks=1)
        trend.append({
            "week": start.strftime("%d %b"),
            "completed": jobs.filter(
                status=Job.Status.COMPLETED, completed_at__gte=start, completed_at__lt=end
            ).count(),
            "created": jobs.filter(created_at__gte=start, created_at__lt=end).count(),
        })

    data = {
        "scope": "organisation" if is_management else "your",
        "days": days,
        "stats": stats,
        "by_status": jobs_by_status(jobs),
        "by_priority": jobs_by_priority(jobs),
        "weekly_trend": trend,
    }

    # Scheduling widgets
    if user.role == Roles.STAFF:
        upcoming = TaskOccurrence.objects.filter(
            assigned_to=user, status=TaskOccurrence.Status.PENDING
        ).select_related("scheduled_task").order_by("scheduled_for")[:6]
        data["upcoming_tasks"] = [
            {
                "id": o.pk, "name": o.scheduled_task.name,
                "scheduled_for": timezone.localtime(o.scheduled_for).strftime("%Y-%m-%d %H:%M"),
                "overdue": o.scheduled_for < now,
            }
            for o in upcoming
        ]
        data["my_pending_tasks"] = TaskOccurrence.objects.filter(
            assigned_to=user, status=TaskOccurrence.Status.PENDING
        ).count()
    elif is_management:
        upcoming = ScheduledTask.objects.filter(
            status=ScheduledTask.Status.ACTIVE, next_run_at__isnull=False
        ).order_by("next_run_at")[:6]
        data["upcoming_tasks"] = [
            {
                "id": t.pk, "name": t.name,
                "scheduled_for": timezone.localtime(t.next_run_at).strftime("%Y-%m-%d %H:%M"),
                "overdue": t.next_run_at < now,
            }
            for t in upcoming
        ]

    if is_management:
        data["failed_tasks"] = list(
            ScheduledTask.objects.filter(failure_count__gt=0)
            .values("id", "name", "failure_count", "last_error")[:5]
        )
        data["missed_occurrences_7d"] = TaskOccurrence.objects.filter(
            status=TaskOccurrence.Status.MISSED,
            scheduled_for__gte=now - timedelta(days=7),
        ).count()
        data["staff_workload"] = staff_workload()[:8]
        last_run = SchedulerRun.objects.first()
        data["last_scheduler_run"] = None
        if last_run:
            data["last_scheduler_run"] = {
                "id": last_run.pk,
                "started_at": timezone.localtime(last_run.started_at).strftime("%Y-%m-%d %H:%M"),
                "status": last_run.status,
                "age_minutes": int((now - last_run.started_at).total_seconds() // 60),
            }
        recent_audits = AuditLog.objects.select_related("actor")[:8]
        data["recent_audits"] = [
            {
                "id": a.pk,
                "actor": a.actor_email or "system",
                "action": a.get_action_display(),
                "object": a.object_repr,
                "at": timezone.localtime(a.created_at).strftime("%Y-%m-%d %H:%M"),
            }
            for a in recent_audits
        ]

    notifications = Notification.objects.filter(user=user)[:6]
    data["recent_notifications"] = [
        {
            "id": n.pk, "title": n.title, "url": n.url, "is_read": n.is_read,
            "type": n.type,
            "at": timezone.localtime(n.created_at).strftime("%Y-%m-%d %H:%M"),
        }
        for n in notifications
    ]
    return data
