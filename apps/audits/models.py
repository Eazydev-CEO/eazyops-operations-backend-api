from django.conf import settings
from django.db import models


class Actions(models.TextChoices):
    # Authentication
    LOGIN = "LOGIN", "Logged in"
    LOGOUT = "LOGOUT", "Logged out"
    LOGIN_FAILED = "LOGIN_FAILED", "Login failed"
    PASSWORD_CHANGED = "PASSWORD_CHANGED", "Password changed"
    API_TOKEN_ISSUED = "API_TOKEN_ISSUED", "API token issued"
    # Accounts
    INVITATION_SENT = "INVITATION_SENT", "Invitation sent"
    INVITATION_ACCEPTED = "INVITATION_ACCEPTED", "Invitation accepted"
    INVITATION_CANCELLED = "INVITATION_CANCELLED", "Invitation cancelled"
    USER_CREATED = "USER_CREATED", "User created"
    USER_UPDATED = "USER_UPDATED", "User updated"
    USER_ROLE_CHANGED = "USER_ROLE_CHANGED", "User role changed"
    USER_ACTIVATED = "USER_ACTIVATED", "User activated"
    USER_DEACTIVATED = "USER_DEACTIVATED", "User deactivated"
    # Jobs
    JOB_CREATED = "JOB_CREATED", "Job created"
    JOB_UPDATED = "JOB_UPDATED", "Job updated"
    JOB_STATUS_CHANGED = "JOB_STATUS_CHANGED", "Job status changed"
    JOB_ASSIGNED = "JOB_ASSIGNED", "Job assigned"
    JOB_UNASSIGNED = "JOB_UNASSIGNED", "Job unassigned"
    JOB_COMMENTED = "JOB_COMMENTED", "Job comment added"
    JOB_ATTACHMENT_ADDED = "JOB_ATTACHMENT_ADDED", "Job attachment added"
    JOB_ATTACHMENT_DELETED = "JOB_ATTACHMENT_DELETED", "Job attachment deleted"
    JOB_DELETE_ATTEMPTED = "JOB_DELETE_ATTEMPTED", "Job delete attempted"
    JOB_DELETED = "JOB_DELETED", "Job deleted"
    # Scheduling
    SCHEDULE_CREATED = "SCHEDULE_CREATED", "Scheduled task created"
    SCHEDULE_UPDATED = "SCHEDULE_UPDATED", "Scheduled task updated"
    SCHEDULE_PAUSED = "SCHEDULE_PAUSED", "Scheduled task paused"
    SCHEDULE_RESUMED = "SCHEDULE_RESUMED", "Scheduled task resumed"
    SCHEDULER_EXECUTED = "SCHEDULER_EXECUTED", "Scheduler run executed"
    OCCURRENCE_COMPLETED = "OCCURRENCE_COMPLETED", "Task occurrence completed"
    # Configuration
    SETTING_CHANGED = "SETTING_CHANGED", "System setting changed"
    CATEGORY_CHANGED = "CATEGORY_CHANGED", "Job category changed"
    DEPARTMENT_CHANGED = "DEPARTMENT_CHANGED", "Department changed"
    # Data access
    EXPORT_GENERATED = "EXPORT_GENERATED", "Export generated"


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("Audit logs are immutable.")

    def delete(self):
        raise PermissionError("Audit logs are immutable.")


class AuditLog(models.Model):
    """Tamper-resistant audit record.

    Rows are insert-only: ``save()`` refuses updates, ``delete()`` and
    queryset-level update/delete raise. The actor's email is denormalised
    so history stays readable even if the account is ever purged.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="audit_entries",
    )
    actor_email = models.CharField(max_length=254, blank=True, db_index=True)
    action = models.CharField(max_length=40, choices=Actions.choices, db_index=True)
    object_type = models.CharField(max_length=60, blank=True, db_index=True)
    object_id = models.CharField(max_length=40, blank=True, db_index=True)
    object_repr = models.CharField(max_length=200, blank=True)
    changes = models.JSONField(
        null=True, blank=True,
        help_text='Per-field before/after values: {"field": [before, after]}',
    )
    metadata = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["object_type", "object_id"])]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.actor_email or 'system'} {self.action}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise PermissionError("Audit logs are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit logs are immutable.")
