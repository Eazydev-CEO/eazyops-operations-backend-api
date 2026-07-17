"""Job list filtering/sorting shared by web views, CSV export and reports."""
from django.db.models import Q

from .models import Job

SORTABLE = {
    "created": "-created_at",
    "created_asc": "created_at",
    "due": "due_date",
    "due_desc": "-due_date",
    "priority": "priority",
    "number": "number",
    "title": "title",
    "status": "status",
}

PRIORITY_ORDER = ["URGENT", "HIGH", "MEDIUM", "LOW"]


def filter_jobs(qs, params):
    """Apply search / filter / sort query params to a Job queryset."""
    q = (params.get("q") or "").strip()
    status = params.get("status") or ""
    priority = params.get("priority") or ""
    category = params.get("category") or ""
    department = params.get("department") or ""
    staff = params.get("staff") or ""
    tag = (params.get("tag") or "").strip()
    due_from = params.get("due_from") or ""
    due_to = params.get("due_to") or ""
    created_from = params.get("created_from") or ""
    created_to = params.get("created_to") or ""

    if q:
        qs = qs.filter(
            Q(number__icontains=q) | Q(title__icontains=q)
            | Q(description__icontains=q) | Q(tags__name__icontains=q)
        ).distinct()
    if status == Job.Status.OVERDUE:
        qs = qs.overdue()
    elif status in Job.Status.values:
        qs = qs.filter(status=status)
    if priority in Job.Priority.values:
        qs = qs.filter(priority=priority)
    if str(category).isdigit():
        qs = qs.filter(category_id=category)
    if str(department).isdigit():
        qs = qs.filter(department_id=department)
    if str(staff).isdigit():
        qs = qs.filter(assignments__staff_id=staff).distinct()
    if tag:
        qs = qs.filter(tags__name__iexact=tag).distinct()
    if due_from:
        qs = qs.filter(due_date__gte=due_from)
    if due_to:
        qs = qs.filter(due_date__lte=due_to)
    if created_from:
        qs = qs.filter(created_at__date__gte=created_from)
    if created_to:
        qs = qs.filter(created_at__date__lte=created_to)

    sort = params.get("sort") or "created"
    qs = qs.order_by(SORTABLE.get(sort, "-created_at"), "-id")
    return qs
