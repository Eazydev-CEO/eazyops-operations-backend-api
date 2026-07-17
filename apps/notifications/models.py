from django.conf import settings
from django.db import models


class NotificationTypes(models.TextChoices):
    JOB_ASSIGNED = "JOB_ASSIGNED", "Job assigned"
    JOB_REASSIGNED = "JOB_REASSIGNED", "Job reassigned"
    JOB_DUE_SOON = "JOB_DUE_SOON", "Job due soon"
    JOB_OVERDUE = "JOB_OVERDUE", "Job overdue"
    JOB_SUBMITTED = "JOB_SUBMITTED", "Job submitted for review"
    JOB_APPROVED = "JOB_APPROVED", "Job approved"
    JOB_REJECTED = "JOB_REJECTED", "Job rejected"
    TASK_DUE = "TASK_DUE", "Scheduled task due"
    TASK_MISSED = "TASK_MISSED", "Scheduled task missed"
    SCHEDULER_FAILURE = "SCHEDULER_FAILURE", "Scheduler failure"
    INVITATION_ACCEPTED = "INVITATION_ACCEPTED", "Invitation accepted"
    WELCOME = "WELCOME", "Welcome"
    SYSTEM = "SYSTEM", "System"


class Notification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    type = models.CharField(max_length=30, choices=NotificationTypes.choices, db_index=True)
    title = models.CharField(max_length=160)
    message = models.TextField(blank=True)
    url = models.CharField(max_length=300, blank=True, help_text="In-app path this notification links to.")
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    email_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "is_read", "created_at"])]

    def __str__(self):
        return f"{self.user_id}:{self.type}:{self.title[:40]}"
