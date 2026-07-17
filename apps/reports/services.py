"""Report aggregations — every number comes from real database records."""
from collections import defaultdict
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone

from apps.audits.models import AuditLog
from apps.core.rbac import Roles
from apps.operations.models import Job, JobCategory
from apps.scheduling.models import ScheduledTask, TaskOccurrence

CATEGORY_FALLBACK_COLOR = "#6366f1"

STATUS_COLORS = {
    "DRAFT": "#94a3b8", "OPEN": "#38bdf8", "ASSIGNED": "#6366f1",
    "IN_PROGRESS": "#f59e0b", "AWAITING_REVIEW": "#a855f7",
    "COMPLETED": "#22c55e", "CANCELLED": "#475569", "OVERDUE": "#ef4444",
}
PRIORITY_COLORS = {"LOW": "#64748b", "MEDIUM": "#38bdf8", "HIGH": "#f59e0b", "URGENT": "#ef4444"}


def apply_report_filters(qs, params):
    """Common report filters: created date range, staff, category, status, priority."""
    date_from = params.get("date_from") or ""
    date_to = params.get("date_to") or ""
    staff = params.get("staff") or ""
    category = params.get("category") or ""
    status = params.get("status") or ""
    priority = params.get("priority") or ""

    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    if str(staff).isdigit():
        qs = qs.filter(assignments__staff_id=staff).distinct()
    if str(category).isdigit():
        qs = qs.filter(category_id=category)
    if status == Job.Status.OVERDUE:
        qs = qs.overdue()
    elif status in Job.Status.values:
        qs = qs.filter(status=status)
    if priority in Job.Priority.values:
        qs = qs.filter(priority=priority)
    return qs


# ---------------------------------------------------------------------------
# Job distribution reports
# ---------------------------------------------------------------------------
def jobs_by_status(qs):
    counts = dict(qs.values_list("status").annotate(n=Count("id")).order_by())
    overdue = qs.overdue().count()
    rows = []
    for value, label in Job.Status.choices:
        if value == Job.Status.OVERDUE:
            n = overdue
        else:
            n = counts.get(value, 0)
        rows.append({"key": value, "label": label, "count": n, "color": STATUS_COLORS.get(value)})
    return rows


def jobs_by_category(qs):
    data = (
        qs.values("category_id", "category__name", "category__color")
        .annotate(n=Count("id"), completed=Count("id", filter=Q(status=Job.Status.COMPLETED)))
        .order_by("-n")
    )
    return [
        {
            "key": row["category_id"],
            "label": row["category__name"],
            "count": row["n"],
            "completed": row["completed"],
            "color": row["category__color"] or CATEGORY_FALLBACK_COLOR,
        }
        for row in data
    ]


def jobs_by_priority(qs):
    counts = dict(qs.values_list("priority").annotate(n=Count("id")).order_by())
    return [
        {"key": value, "label": label, "count": counts.get(value, 0), "color": PRIORITY_COLORS.get(value)}
        for value, label in Job.Priority.choices
    ]


# ---------------------------------------------------------------------------
# Staff reports (management only)
# ---------------------------------------------------------------------------
def staff_workload():
    """Active jobs and pending scheduled occurrences per active staff member."""
    User = get_user_model()
    staff = User.objects.filter(role=Roles.STAFF, is_active=True).annotate(
        active_jobs=Count(
            "job_assignments",
            filter=Q(job_assignments__job__status__in=Job.ACTIVE_STATUSES),
            distinct=True,
        ),
        urgent_jobs=Count(
            "job_assignments",
            filter=Q(
                job_assignments__job__status__in=Job.ACTIVE_STATUSES,
                job_assignments__job__priority=Job.Priority.URGENT,
            ),
            distinct=True,
        ),
        pending_tasks=Count(
            "task_occurrences",
            filter=Q(task_occurrences__status=TaskOccurrence.Status.PENDING),
            distinct=True,
        ),
    ).select_related("department").order_by("-active_jobs")
    return [
        {
            "id": u.pk,
            "name": u.display_name,
            "department": u.department.name if u.department else "",
            "active_jobs": u.active_jobs,
            "urgent_jobs": u.urgent_jobs,
            "pending_tasks": u.pending_tasks,
        }
        for u in staff
    ]


def staff_performance(date_from=None, date_to=None):
    """Completion volume, average duration and on-time rate per staff member."""
    qs = Job.objects.filter(status=Job.Status.COMPLETED, completed_at__isnull=False)
    if date_from:
        qs = qs.filter(completed_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(completed_at__date__lte=date_to)

    rows = qs.values(
        "id", "completed_at", "start_date", "due_date",
        "assignments__staff_id", "assignments__staff__first_name",
        "assignments__staff__last_name", "assignments__staff__email",
    )
    buckets = {}
    for row in rows:
        staff_id = row["assignments__staff_id"]
        if staff_id is None:
            continue
        b = buckets.setdefault(staff_id, {
            "id": staff_id,
            "name": (f"{row['assignments__staff__first_name']} "
                     f"{row['assignments__staff__last_name']}").strip()
            or row["assignments__staff__email"],
            "completed": 0, "durations": [], "on_time": 0, "with_due": 0,
        })
        b["completed"] += 1
        if row["start_date"]:
            b["durations"].append((row["completed_at"].date() - row["start_date"]).days)
        if row["due_date"]:
            b["with_due"] += 1
            if row["completed_at"].date() <= row["due_date"]:
                b["on_time"] += 1

    results = []
    for b in buckets.values():
        durations = b.pop("durations")
        with_due = b.pop("with_due")
        on_time = b.pop("on_time")
        b["avg_days"] = round(sum(durations) / len(durations), 1) if durations else None
        b["on_time_rate"] = round(on_time * 100 / with_due) if with_due else None
        results.append(b)
    results.sort(key=lambda r: -r["completed"])
    return results


# ---------------------------------------------------------------------------
# Overdue / completion time
# ---------------------------------------------------------------------------
def overdue_jobs(qs):
    today = timezone.localdate()
    jobs = qs.overdue().select_related("category", "department", "manager").prefetch_related(
        "assignments__staff"
    ).order_by("due_date")
    return [
        {
            "id": j.pk, "number": j.number, "title": j.title,
            "category": j.category.name, "department": j.department.name,
            "priority": j.priority, "status": j.status,
            "due_date": j.due_date, "days_overdue": (today - j.due_date).days,
            "manager": j.manager.display_name,
            "staff": ", ".join(a.staff.display_name for a in j.assignments.all()),
        }
        for j in jobs
    ]


def completion_time(qs):
    """Average completion duration grouped by category and by priority."""
    completed = qs.filter(
        status=Job.Status.COMPLETED, completed_at__isnull=False, start_date__isnull=False
    ).values("category__name", "priority", "completed_at", "start_date")

    by_category, by_priority, all_durations = defaultdict(list), defaultdict(list), []
    for row in completed:
        days = (row["completed_at"].date() - row["start_date"]).days
        by_category[row["category__name"]].append(days)
        by_priority[row["priority"]].append(days)
        all_durations.append(days)

    def fold(bucket):
        return sorted(
            (
                {"label": key, "count": len(values), "avg_days": round(sum(values) / len(values), 1)}
                for key, values in bucket.items()
            ),
            key=lambda r: -r["count"],
        )

    return {
        "overall_avg": round(sum(all_durations) / len(all_durations), 1) if all_durations else None,
        "total_completed": len(all_durations),
        "by_category": fold(by_category),
        "by_priority": fold(by_priority),
    }


# ---------------------------------------------------------------------------
# Scheduling & audit reports
# ---------------------------------------------------------------------------
def scheduled_task_report(date_from=None, date_to=None):
    occ = TaskOccurrence.objects.all()
    if date_from:
        occ = occ.filter(scheduled_for__date__gte=date_from)
    if date_to:
        occ = occ.filter(scheduled_for__date__lte=date_to)
    data = occ.values("scheduled_task_id", "scheduled_task__name", "scheduled_task__frequency").annotate(
        total=Count("id"),
        completed=Count("id", filter=Q(status=TaskOccurrence.Status.COMPLETED)),
        missed=Count("id", filter=Q(status=TaskOccurrence.Status.MISSED)),
        pending=Count("id", filter=Q(status=TaskOccurrence.Status.PENDING)),
    ).order_by("scheduled_task__name")
    rows = []
    for row in data:
        done_or_missed = row["completed"] + row["missed"]
        rows.append({
            "id": row["scheduled_task_id"],
            "name": row["scheduled_task__name"],
            "frequency": row["scheduled_task__frequency"],
            "total": row["total"],
            "completed": row["completed"],
            "missed": row["missed"],
            "pending": row["pending"],
            "completion_rate": round(row["completed"] * 100 / done_or_missed) if done_or_missed else None,
        })
    return rows


def _parse_date(value, default):
    from datetime import date

    if isinstance(value, date):
        return value
    if value:
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            pass
    return default


def audit_activity(date_from=None, date_to=None):
    today = timezone.localdate()
    start = _parse_date(date_from, today - timedelta(days=13))
    end = _parse_date(date_to, today)
    if end < start:
        start, end = end, start

    qs = AuditLog.objects.filter(created_at__date__gte=start, created_at__date__lte=end)
    by_action = list(qs.values("action").annotate(n=Count("id")).order_by("-n")[:15])
    by_user = list(
        qs.exclude(actor_email="").values("actor_email").annotate(n=Count("id")).order_by("-n")[:10]
    )
    per_day_raw = dict(qs.values_list("created_at__date").annotate(n=Count("id")).order_by())

    days, day = [], start
    while day <= end and len(days) < 92:
        # SQLite may hand back ISO strings for the date key; normalise both.
        count = per_day_raw.get(day, per_day_raw.get(day.isoformat(), 0))
        days.append({"date": day.isoformat(), "count": count})
        day += timedelta(days=1)
    return {"by_action": by_action, "by_user": by_user, "per_day": days, "total": qs.count()}
