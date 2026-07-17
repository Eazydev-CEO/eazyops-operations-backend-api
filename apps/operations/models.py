import os

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Department, TimeStampedModel
from apps.core.rbac import Roles


class JobCategory(TimeStampedModel):
    name = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=255, blank=True)
    color = models.CharField(max_length=7, default="#6366f1", help_text="Hex colour for badges.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Job categories"

    def __str__(self):
        return self.name


class Tag(models.Model):
    name = models.CharField(max_length=40, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class JobQuerySet(models.QuerySet):
    ACTIVE_STATUSES = ("OPEN", "ASSIGNED", "IN_PROGRESS", "AWAITING_REVIEW")

    def visible_to(self, user):
        """Row-level access boundary — the single source of truth for job reads."""
        if not user.is_authenticated or not user.is_active:
            return self.none()
        if user.role in (Roles.SUPER_ADMIN, Roles.MANAGER):
            return self
        if user.role == Roles.STAFF:
            return self.filter(assignments__staff=user).distinct()
        # Viewer / client: only their department's non-draft jobs
        if user.department_id:
            return self.filter(department_id=user.department_id).exclude(status=Job.Status.DRAFT)
        return self.none()

    def active(self):
        return self.filter(status__in=self.ACTIVE_STATUSES)

    def overdue(self):
        today = timezone.localdate()
        return self.active().filter(due_date__lt=today)

    def due_soon(self, days):
        today = timezone.localdate()
        return self.active().filter(
            due_date__gte=today, due_date__lte=today + timezone.timedelta(days=days)
        )


class Job(TimeStampedModel):
    """A unit of operational work (work order)."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        OPEN = "OPEN", "Open"
        ASSIGNED = "ASSIGNED", "Assigned"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        AWAITING_REVIEW = "AWAITING_REVIEW", "Awaiting Review"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"
        OVERDUE = "OVERDUE", "Overdue"  # derived: active + past due date

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        URGENT = "URGENT", "Urgent"

    #: statuses a job can still be worked in
    ACTIVE_STATUSES = JobQuerySet.ACTIVE_STATUSES
    TERMINAL_STATUSES = (Status.COMPLETED, Status.CANCELLED)

    number = models.CharField(max_length=20, unique=True, editable=False)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.ForeignKey(JobCategory, on_delete=models.PROTECT, related_name="jobs")
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.MEDIUM, db_index=True
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="jobs",
        help_text="Client or internal department this job belongs to.",
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="managed_jobs",
        limit_choices_to={"role__in": [Roles.SUPER_ADMIN, Roles.MANAGER]},
    )
    staff = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="JobAssignment",
        through_fields=("job", "staff"), related_name="assigned_jobs", blank=True,
    )

    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    estimated_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    actual_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    tags = models.ManyToManyField(Tag, blank=True, related_name="jobs")

    # Review workflow
    submitted_for_review_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reviewed_jobs",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)

    # Overdue bookkeeping: notification sent once per overdue transition
    overdue_notified_at = models.DateTimeField(null=True, blank=True)
    due_soon_notified_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="created_jobs"
    )

    objects = JobQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "due_date"]),
            models.Index(fields=["department", "status"]),
        ]

    def __str__(self):
        return f"{self.number} — {self.title}"

    def get_absolute_url(self):
        return reverse("operations:job_detail", args=[self.pk])

    @property
    def is_overdue(self):
        return (
            self.status in self.ACTIVE_STATUSES
            and self.due_date is not None
            and self.due_date < timezone.localdate()
        )

    @property
    def effective_status(self):
        """Status with the automatic OVERDUE overlay applied."""
        return self.Status.OVERDUE if self.is_overdue else self.Status(self.status)

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES

    @property
    def completion_days(self):
        if self.completed_at and self.start_date:
            return (self.completed_at.date() - self.start_date).days
        return None

    @property
    def cost_variance(self):
        if self.estimated_cost is not None and self.actual_cost is not None:
            return self.actual_cost - self.estimated_cost
        return None


class JobAssignment(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="assignments")
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="job_assignments"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["assigned_at"]
        constraints = [
            models.UniqueConstraint(fields=["job", "staff"], name="uniq_job_staff_assignment")
        ]

    def __str__(self):
        return f"{self.job.number} -> {self.staff}"


class JobComment(TimeStampedModel):
    class Kind(models.TextChoices):
        COMMENT = "COMMENT", "Comment"
        PROGRESS = "PROGRESS", "Progress update"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="job_comments"
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.COMMENT)
    body = models.TextField()

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment<{self.job.number} by {self.author}>"


def attachment_upload_to(instance, filename):
    return f"job_attachments/{timezone.now():%Y/%m}/{filename}"


class JobAttachment(TimeStampedModel):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="attachments")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="job_attachments"
    )
    file = models.FileField(upload_to=attachment_upload_to, max_length=300)
    original_name = models.CharField(max_length=200)
    size = models.PositiveBigIntegerField(default=0)
    content_type = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_name

    @property
    def extension(self):
        return os.path.splitext(self.original_name)[1].lstrip(".").lower()

    @property
    def size_display(self):
        size = float(self.size or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024
        return f"{size:.1f} GB"


class JobSequence(models.Model):
    """Per-year counter backing gap-free, human-friendly job numbers."""

    year = models.PositiveIntegerField(unique=True)
    last_value = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.year}: {self.last_value}"
