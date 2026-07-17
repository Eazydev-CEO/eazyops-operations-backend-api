"""Small presentation helpers used across dashboard templates."""
from django import template
from django.utils.html import format_html, format_html_join

register = template.Library()

STATUS_BADGES = {
    "DRAFT": "secondary",
    "OPEN": "info",
    "ASSIGNED": "primary",
    "IN_PROGRESS": "warning",
    "AWAITING_REVIEW": "purple",
    "COMPLETED": "success",
    "CANCELLED": "dark",
    "OVERDUE": "danger",
    # scheduling
    "PENDING": "info",
    "MISSED": "danger",
    "ACTIVE": "success",
    "PAUSED": "secondary",
    "FINISHED": "dark",
    # scheduler runs
    "RUNNING": "warning",
    "SUCCESS": "success",
    "PARTIAL": "warning",
    "FAILED": "danger",
}

PRIORITY_BADGES = {
    "LOW": "secondary",
    "MEDIUM": "info",
    "HIGH": "warning",
    "URGENT": "danger",
}


@register.filter
def status_badge(value):
    return STATUS_BADGES.get(str(value or "").upper(), "secondary")


@register.filter
def priority_badge(value):
    return PRIORITY_BADGES.get(str(value or "").upper(), "secondary")


@register.filter
def initials(user):
    """Two-letter initials for avatar placeholders."""
    if user is None:
        return "?"
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    if first or last:
        return f"{first[:1]}{last[:1]}".upper() or "?"
    email = getattr(user, "email", "") or ""
    return email[:2].upper() or "?"


@register.simple_tag
def field_errors(field):
    if not getattr(field, "errors", None):
        return ""
    return format_html(
        "{}",
        format_html_join(
            "", "<div class='invalid-feedback d-block'>{}</div>", ((e,) for e in field.errors)
        ),
    )


@register.filter
def add_class(field, css):
    """Render a bound form field with extra CSS classes (keeps widgets clean)."""
    attrs = {"class": css}
    if getattr(field, "errors", None):
        attrs["class"] += " is-invalid"
    return field.as_widget(attrs=attrs)


@register.filter
def percent_of(value, total):
    try:
        value, total = float(value or 0), float(total or 0)
        return round(value * 100 / total) if total else 0
    except (TypeError, ValueError):
        return 0
