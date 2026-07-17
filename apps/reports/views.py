import csv
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.audits.services import Actions, record_audit
from apps.core.rbac import Roles, require_capability
from apps.operations.models import Job, JobCategory

from . import services


def _csv_response(request, filename, header, rows):
    response = HttpResponse(content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d-%H%M")
    response["Content-Disposition"] = f'attachment; filename="{filename}-{stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    record_audit(
        actor=request.user, action=Actions.EXPORT_GENERATED,
        object_type="reports", object_repr=f"{filename}.csv",
        metadata={"rows": len(rows)}, request=request,
    )
    return response


def _base_jobs(request):
    """Job queryset scoped to the requesting user's visibility + filters."""
    qs = Job.objects.visible_to(request.user)
    return services.apply_report_filters(qs, request.GET)


def _common_context(request, title, slug):
    User = get_user_model()
    return {
        "title": title,
        "slug": slug,
        "categories": JobCategory.objects.filter(is_active=True),
        "staff_users": User.objects.filter(role=Roles.STAFF, is_active=True),
        "statuses": Job.Status.choices,
        "priorities": Job.Priority.choices,
        "filters": {k: request.GET.get(k, "") for k in
                    ("date_from", "date_to", "staff", "category", "status", "priority")},
        "is_full": request.user.can("reports.full"),
    }


@login_required
def index(request):
    require_capability(request.user, "reports.view")
    full = request.user.can("reports.full")
    reports = [
        ("status", "Jobs by status", "Distribution of jobs across workflow states.", True),
        ("category", "Jobs by category", "Volume and completion per job category.", True),
        ("priority", "Jobs by priority", "How work splits across priorities.", True),
        ("overdue", "Overdue jobs", "Everything past its due date, oldest first.", True),
        ("workload", "Staff workload", "Active jobs and pending tasks per staff member.", full),
        ("performance", "Staff performance", "Completions, durations and on-time rates.", full),
        ("completion-time", "Completion time", "Average time to complete, by category and priority.", full),
        ("scheduled", "Scheduled task completion", "Recurring task completion and misses.", full),
        ("audit", "Audit activity", "Platform activity volumes and top actors.", full),
    ]
    return render(request, "reports/index.html", {
        "reports": [r for r in reports if r[3]],
    })


@login_required
def jobs_by_status(request):
    require_capability(request.user, "reports.view")
    rows = services.jobs_by_status(_base_jobs(request))
    if request.GET.get("export") == "csv":
        return _csv_response(request, "jobs-by-status", ["Status", "Jobs"],
                             [[r["label"], r["count"]] for r in rows])
    ctx = _common_context(request, "Jobs by status", "status")
    ctx["rows"] = rows
    ctx["total_count"] = sum(r["count"] for r in rows)
    ctx["chart"] = json.dumps({
        "labels": [r["label"] for r in rows],
        "values": [r["count"] for r in rows],
        "colors": [r["color"] for r in rows],
    })
    return render(request, "reports/simple_distribution.html", ctx)


@login_required
def jobs_by_category(request):
    require_capability(request.user, "reports.view")
    rows = services.jobs_by_category(_base_jobs(request))
    if request.GET.get("export") == "csv":
        return _csv_response(request, "jobs-by-category", ["Category", "Jobs", "Completed"],
                             [[r["label"], r["count"], r["completed"]] for r in rows])
    ctx = _common_context(request, "Jobs by category", "category")
    ctx["rows"] = rows
    ctx["show_completed"] = True
    ctx["total_count"] = sum(r["count"] for r in rows)
    ctx["chart"] = json.dumps({
        "labels": [r["label"] for r in rows],
        "values": [r["count"] for r in rows],
        "colors": [r["color"] for r in rows],
    })
    return render(request, "reports/simple_distribution.html", ctx)


@login_required
def jobs_by_priority(request):
    require_capability(request.user, "reports.view")
    rows = services.jobs_by_priority(_base_jobs(request))
    if request.GET.get("export") == "csv":
        return _csv_response(request, "jobs-by-priority", ["Priority", "Jobs"],
                             [[r["label"], r["count"]] for r in rows])
    ctx = _common_context(request, "Jobs by priority", "priority")
    ctx["rows"] = rows
    ctx["total_count"] = sum(r["count"] for r in rows)
    ctx["chart"] = json.dumps({
        "labels": [r["label"] for r in rows],
        "values": [r["count"] for r in rows],
        "colors": [r["color"] for r in rows],
    })
    return render(request, "reports/simple_distribution.html", ctx)


@login_required
def overdue_report(request):
    require_capability(request.user, "reports.view")
    rows = services.overdue_jobs(_base_jobs(request))
    if request.GET.get("export") == "csv":
        return _csv_response(
            request, "overdue-jobs",
            ["Number", "Title", "Category", "Department", "Priority", "Due date",
             "Days overdue", "Manager", "Staff"],
            [[r["number"], r["title"], r["category"], r["department"], r["priority"],
              r["due_date"], r["days_overdue"], r["manager"], r["staff"]] for r in rows],
        )
    ctx = _common_context(request, "Overdue jobs", "overdue")
    ctx["rows"] = rows
    return render(request, "reports/overdue.html", ctx)


@login_required
def workload_report(request):
    require_capability(request.user, "reports.full")
    rows = services.staff_workload()
    if request.GET.get("export") == "csv":
        return _csv_response(
            request, "staff-workload",
            ["Staff", "Department", "Active jobs", "Urgent jobs", "Pending tasks"],
            [[r["name"], r["department"], r["active_jobs"], r["urgent_jobs"], r["pending_tasks"]]
             for r in rows],
        )
    ctx = _common_context(request, "Staff workload", "workload")
    ctx["rows"] = rows
    ctx["chart"] = json.dumps({
        "labels": [r["name"] for r in rows[:12]],
        "values": [r["active_jobs"] for r in rows[:12]],
        "colors": ["#6366f1"] * min(len(rows), 12),
    })
    return render(request, "reports/workload.html", ctx)


@login_required
def performance_report(request):
    require_capability(request.user, "reports.full")
    rows = services.staff_performance(
        request.GET.get("date_from") or None, request.GET.get("date_to") or None
    )
    if request.GET.get("export") == "csv":
        return _csv_response(
            request, "staff-performance",
            ["Staff", "Completed jobs", "Avg days", "On-time rate %"],
            [[r["name"], r["completed"], r["avg_days"] if r["avg_days"] is not None else "",
              r["on_time_rate"] if r["on_time_rate"] is not None else ""] for r in rows],
        )
    ctx = _common_context(request, "Staff completion performance", "performance")
    ctx["rows"] = rows
    return render(request, "reports/performance.html", ctx)


@login_required
def completion_time_report(request):
    require_capability(request.user, "reports.full")
    data = services.completion_time(_base_jobs(request))
    if request.GET.get("export") == "csv":
        rows = [["Category", r["label"], r["count"], r["avg_days"]] for r in data["by_category"]]
        rows += [["Priority", r["label"], r["count"], r["avg_days"]] for r in data["by_priority"]]
        return _csv_response(request, "completion-time",
                             ["Group", "Value", "Completed jobs", "Avg days"], rows)
    ctx = _common_context(request, "Completion time", "completion-time")
    ctx["data"] = data
    return render(request, "reports/completion_time.html", ctx)


@login_required
def scheduled_report(request):
    require_capability(request.user, "reports.full")
    rows = services.scheduled_task_report(
        request.GET.get("date_from") or None, request.GET.get("date_to") or None
    )
    if request.GET.get("export") == "csv":
        return _csv_response(
            request, "scheduled-task-completion",
            ["Task", "Frequency", "Occurrences", "Completed", "Missed", "Pending", "Completion rate %"],
            [[r["name"], r["frequency"], r["total"], r["completed"], r["missed"], r["pending"],
              r["completion_rate"] if r["completion_rate"] is not None else ""] for r in rows],
        )
    ctx = _common_context(request, "Scheduled task completion", "scheduled")
    ctx["rows"] = rows
    return render(request, "reports/scheduled.html", ctx)


@login_required
def audit_report(request):
    require_capability(request.user, "reports.full")
    data = services.audit_activity(
        request.GET.get("date_from") or None, request.GET.get("date_to") or None
    )
    if request.GET.get("export") == "csv":
        rows = [[d["date"], d["count"]] for d in data["per_day"]]
        return _csv_response(request, "audit-activity", ["Date", "Events"], rows)
    ctx = _common_context(request, "Audit activity", "audit")
    ctx["data"] = data
    ctx["chart"] = json.dumps({
        "labels": [d["date"] for d in data["per_day"]],
        "values": [d["count"] for d in data["per_day"]],
    })
    return render(request, "reports/audit.html", ctx)
