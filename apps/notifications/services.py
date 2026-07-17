"""Notification fanout — in-app records plus optional email copies.

Email delivery is best-effort and environment-driven (console backend in
development, SMTP in production). A failed email never breaks the caller.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail

from apps.core.services import get_setting_bool

from .models import Notification, NotificationTypes

__all__ = ["NotificationTypes", "notify", "mark_read", "mark_all_read"]

logger = logging.getLogger("eazyops.notifications")

#: Types that also send an email by default (user opt-out still applies).
EMAILED_TYPES = {
    NotificationTypes.JOB_ASSIGNED,
    NotificationTypes.JOB_REASSIGNED,
    NotificationTypes.JOB_OVERDUE,
    NotificationTypes.JOB_APPROVED,
    NotificationTypes.JOB_REJECTED,
    NotificationTypes.TASK_MISSED,
    NotificationTypes.SCHEDULER_FAILURE,
    NotificationTypes.INVITATION_ACCEPTED,
}


def notify(users, type, *, title, message="", url="", send_email=None):
    """Create notifications for one user or an iterable of users.

    Returns the list of created Notification rows. Duplicate userids are
    collapsed; inactive users are skipped.
    """
    if users is None:
        return []
    if not hasattr(users, "__iter__"):
        users = [users]

    seen, targets = set(), []
    for user in users:
        if user is None or not getattr(user, "pk", None) or not user.is_active:
            continue
        if user.pk in seen:
            continue
        seen.add(user.pk)
        targets.append(user)

    created = []
    for user in targets:
        wants_email = send_email if send_email is not None else (type in EMAILED_TYPES)
        emailed = False
        if wants_email:
            emailed = _send_email_copy(user, title, message, url)
        created.append(
            Notification.objects.create(
                user=user, type=type, title=title[:160], message=message,
                url=url[:300], email_sent=emailed,
            )
        )
    return created


def _send_email_copy(user, title, message, url):
    if not get_setting_bool("notification_emails_enabled", True):
        return False
    if not user.email_notifications or not user.email:
        return False
    body = message or title
    if url:
        body = f"{body}\n\nOpen in {settings.SITE_NAME}: {settings.SITE_URL}{url}"
    try:
        send_mail(title, body, settings.DEFAULT_FROM_EMAIL, [user.email])
        return True
    except Exception:  # pragma: no cover — SMTP failures must not break flows
        logger.exception("Failed to email notification to %s", user.email)
        return False


def mark_read(user, notification_id):
    from django.utils import timezone

    updated = Notification.objects.filter(pk=notification_id, user=user, is_read=False).update(
        is_read=True, read_at=timezone.now()
    )
    return bool(updated)


def mark_all_read(user):
    from django.utils import timezone

    return Notification.objects.filter(user=user, is_read=False).update(
        is_read=True, read_at=timezone.now()
    )
