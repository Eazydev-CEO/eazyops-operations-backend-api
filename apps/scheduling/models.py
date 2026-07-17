from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse

from apps.core.models import Department, TimeStampedModel
from apps.core.rbac import Roles
from apps.operations.models import Job, JobCategory


class ScheduledTask(TimeStampedModel):
    """A one-time or recurring task template.

    The scheduler materialises TaskOccurrence rows (and optionally full
    Jobs) from these templates when ``next_run_at`` comes due.
    """

    class Action(models.TextChoices):
        CREATE_OCCURRENCE = "CREATE_OCCURRENCE", "Create task occurrence (checklist item)"
        CREATE_JOB = "CREATE_JOB", "Create a full job / work order"

    class Frequency(models.TextChoices):
        ONCE = "ONCE", "One time"
        DAILY = "DAILY", "Daily"
        WEEKLY = "WEEKLY", "Weekly"
        MONTHLY = "MONTHLY", "Monthly"
        INTERVAL = "INTERVAL", "Custom interval"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        PAUSED = "PAUSED", "Paused"
        FINISHED = "FINISHED", "Finished"

    WEEKDAYS = [
        (0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"),
        (4, "Friday"), (5, "Saturday"), (6, "Sunday"),
    ]

    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    action = models.CharField(max_length=30, choices=Action.choices, default=Action.CREATE_OCCURRENCE)

    # Recurrence rule (interpreted in the project timezone, UTC by default)
    frequency = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.DAILY)
    time_of_day = models.TimeField(
        null=True, blank=True, help_text="Run time for daily/weekly/monthly schedules."
    )
    weekday = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=WEEKDAYS, help_text="For weekly schedules."
    )
    day_of_month = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="1–28, for monthly schedules."
    )
    interval_minutes = models.PositiveIntegerField(
        null=True, blank=True, help_text="Every N minutes (minimum 5), for custom intervals."
    )
    starts_at = models.DateTimeField(help_text="First moment the schedule may run.")
    ends_at = models.DateTimeField(null=True, blank=True)

    # Assignment: explicit staff and/or a whole department (team)
    assignees = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="scheduled_tasks",
        limit_choices_to={"role": Roles.STAFF},
    )
    team_department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="scheduled_tasks",
        help_text="Assign to every active staff member of this department.",
    )

    # CREATE_JOB template fields
    job_category = models.ForeignKey(JobCategory, null=True, blank=True, on_delete=models.SET_NULL)
    job_department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    job_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", limit_choices_to={"role__in": [Roles.SUPER_ADMIN, Roles.MANAGER]},
    )
    job_priority = models.CharField(
        max_length=10, choices=Job.Priority.choices, default=Job.Priority.MEDIUM
    )
    job_due_days = models.PositiveSmallIntegerField(
        default=3, help_text="Job due date = run time + this many days."
    )

    # Engine state
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    failure_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="created_schedules"
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_frequency_display()})"

    def get_absolute_url(self):
        return reverse("scheduling:task_detail", args=[self.pk])

    def clean(self):
        errors = {}
        if self.frequency in (self.Frequency.DAILY, self.Frequency.WEEKLY, self.Frequency.MONTHLY):
            if self.time_of_day is None:
                errors["time_of_day"] = "Required for daily, weekly and monthly schedules."
        if self.frequency == self.Frequency.WEEKLY and self.weekday is None:
            errors["weekday"] = "Pick the weekday this task runs on."
        if self.frequency == self.Frequency.MONTHLY:
            if not self.day_of_month or not (1 <= self.day_of_month <= 28):
                errors["day_of_month"] = "Use a day between 1 and 28 so every month qualifies."
        if self.frequency == self.Frequency.INTERVAL:
            if not self.interval_minutes or self.interval_minutes < 5:
                errors["interval_minutes"] = "Interval must be at least 5 minutes."
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            errors["ends_at"] = "End must be after the start."
        if self.action == self.Action.CREATE_JOB:
            if not self.job_category:
                errors["job_category"] = "Required when the task creates jobs."
            if not self.job_department:
                errors["job_department"] = "Required when the task creates jobs."
            if not self.job_manager:
                errors["job_manager"] = "Required when the task creates jobs."
        if errors:
            raise ValidationError(errors)

    def resolve_assignees(self):
        """Concrete list of active staff users this task targets."""
        users = {u.pk: u for u in self.assignees.filter(is_active=True, role=Roles.STAFF)}
        if self.team_department_id:
            for user in self.team_department.members.filter(is_active=True, role=Roles.STAFF):
                users.setdefault(user.pk, user)
        return list(users.values())


class TaskOccurrence(TimeStampedModel):
    """One materialised run slot of a ScheduledTask for one assignee."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMPLETED = "COMPLETED", "Completed"
        MISSED = "MISSED", "Missed"

    scheduled_task = models.ForeignKey(
        ScheduledTask, on_delete=models.CASCADE, related_name="occurrences"
    )
    scheduled_for = models.DateTimeField(db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="task_occurrences",
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    notes = models.TextField(blank=True)
    created_job = models.ForeignKey(
        Job, null=True, blank=True, on_delete=models.SET_NULL, related_name="source_occurrences"
    )

    class Meta:
        ordering = ["-scheduled_for"]
        constraints = [
            # duplicate-run guards: one row per (task, slot, assignee) and
            # one unassigned row per (task, slot)
            models.UniqueConstraint(
                fields=["scheduled_task", "scheduled_for", "assigned_to"],
                name="uniq_occurrence_per_assignee",
            ),
            models.UniqueConstraint(
                fields=["scheduled_task", "scheduled_for"],
                condition=Q(assigned_to__isnull=True),
                name="uniq_occurrence_unassigned",
            ),
        ]

    def __str__(self):
        return f"{self.scheduled_task.name} @ {self.scheduled_for:%Y-%m-%d %H:%M}"


class SchedulerRun(models.Model):
    """Execution log for every scheduler pass (cron or manual)."""

    class Status(models.TextChoices):
        RUNNING = "RUNNING", "Running"
        SUCCESS = "SUCCESS", "Success"
        PARTIAL = "PARTIAL", "Completed with errors"
        FAILED = "FAILED", "Failed"

    class Trigger(models.TextChoices):
        COMMAND = "COMMAND", "Management command / cron"
        MANUAL = "MANUAL", "Manual (dashboard)"

    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    triggered_by = models.CharField(max_length=10, choices=Trigger.choices, default=Trigger.COMMAND)
    triggered_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    tasks_processed = models.PositiveIntegerField(default=0)
    occurrences_created = models.PositiveIntegerField(default=0)
    jobs_created = models.PositiveIntegerField(default=0)
    occurrences_missed = models.PositiveIntegerField(default=0)
    jobs_marked_overdue = models.PositiveIntegerField(default=0)
    due_soon_notices = models.PositiveIntegerField(default=0)
    errors_count = models.PositiveIntegerField(default=0)
    log = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"SchedulerRun #{self.pk} {self.started_at:%Y-%m-%d %H:%M} [{self.status}]"

    @property
    def duration_seconds(self):
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


class SchedulerLock(models.Model):
    """Single-row lock preventing concurrent scheduler passes."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.CharField(max_length=120, blank=True)

    def __str__(self):
        return f"SchedulerLock(locked_at={self.locked_at})"
