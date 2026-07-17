from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.accounts.models import Invitation
from apps.audits.models import AuditLog
from apps.core.models import Department
from apps.core.rbac import CAPABILITIES, Roles
from apps.core.validators import validate_avatar, validate_upload
from apps.notifications.models import Notification
from apps.operations.models import (
    Job, JobAssignment, JobAttachment, JobCategory, JobComment, Tag,
)
from apps.scheduling.models import ScheduledTask, SchedulerRun, TaskOccurrence

User = get_user_model()


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "name", "kind", "description", "contact_email", "is_active"]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = JobCategory
        fields = ["id", "name", "description", "color", "is_active"]


class RoleSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    capabilities = serializers.ListField(child=serializers.CharField())

    @staticmethod
    def build_matrix():
        rows = []
        for value, label in Roles.choices:
            caps = sorted(cap for cap, roles in CAPABILITIES.items() if value in roles)
            rows.append({"key": value, "label": label, "capabilities": caps})
        return rows


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class UserSerializer(serializers.ModelSerializer):
    department = DepartmentSerializer(read_only=True)
    full_name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "email", "first_name", "last_name", "full_name", "role",
            "department", "job_title", "phone", "photo", "is_active",
            "last_login", "last_activity_at", "date_joined",
        ]
        read_only_fields = fields


class UserAdminUpdateSerializer(serializers.ModelSerializer):
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.filter(is_active=True), required=False, allow_null=True
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "role", "department", "job_title", "phone"]


class MeSerializer(serializers.ModelSerializer):
    department = DepartmentSerializer(read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "email", "first_name", "last_name", "role", "role_label",
            "department", "job_title", "phone", "photo", "email_notifications",
            "last_login", "last_activity_at",
        ]
        read_only_fields = ["id", "email", "role", "role_label", "department",
                            "job_title", "last_login", "last_activity_at"]

    def validate_photo(self, value):
        if value:
            validate_avatar(value)
        return value


class InvitationSerializer(serializers.ModelSerializer):
    invited_by = serializers.StringRelatedField(read_only=True)
    status = serializers.CharField(read_only=True)
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.filter(is_active=True), required=False, allow_null=True
    )

    class Meta:
        model = Invitation
        fields = ["id", "email", "role", "department", "job_title", "invited_by",
                  "expires_at", "accepted_at", "status", "created_at"]
        read_only_fields = ["id", "invited_by", "expires_at", "accepted_at", "status", "created_at"]


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
class UserBriefSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="display_name", read_only=True)

    class Meta:
        model = User
        fields = ["id", "name", "email", "role"]


class JobListSerializer(serializers.ModelSerializer):
    category = serializers.StringRelatedField()
    department = serializers.StringRelatedField()
    manager = UserBriefSerializer(read_only=True)
    assigned_staff = UserBriefSerializer(source="staff", many=True, read_only=True)
    effective_status = serializers.CharField(read_only=True)
    tags = serializers.SlugRelatedField(slug_field="name", many=True, read_only=True)

    class Meta:
        model = Job
        fields = [
            "id", "number", "title", "category", "priority", "status",
            "effective_status", "department", "manager", "assigned_staff",
            "start_date", "due_date", "completed_at", "tags", "created_at",
        ]


class JobDetailSerializer(JobListSerializer):
    category = CategorySerializer(read_only=True)
    department = DepartmentSerializer(read_only=True)
    reviewed_by = UserBriefSerializer(read_only=True)
    created_by = UserBriefSerializer(read_only=True)
    comment_count = serializers.IntegerField(source="comments.count", read_only=True)
    attachment_count = serializers.IntegerField(source="attachments.count", read_only=True)

    class Meta(JobListSerializer.Meta):
        fields = JobListSerializer.Meta.fields + [
            "description", "estimated_cost", "actual_cost",
            "submitted_for_review_at", "reviewed_by", "reviewed_at", "review_note",
            "created_by", "updated_at", "comment_count", "attachment_count",
        ]


class JobWriteSerializer(serializers.ModelSerializer):
    staff = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True, role=Roles.STAFF),
        many=True, required=False, write_only=True,
    )
    tags = serializers.ListField(
        child=serializers.CharField(max_length=40), required=False, write_only=True
    )
    status = serializers.ChoiceField(
        choices=[Job.Status.DRAFT, Job.Status.OPEN], required=False, write_only=True,
        default=Job.Status.OPEN,
        help_text="Initial status on create (Draft or Open, default Open). Ignored on update.",
    )

    class Meta:
        model = Job
        fields = [
            "title", "description", "category", "priority", "department", "manager",
            "start_date", "due_date", "estimated_cost", "actual_cost", "staff", "tags", "status",
        ]

    def validate_manager(self, value):
        if value.role not in (Roles.SUPER_ADMIN, Roles.MANAGER) or not value.is_active:
            raise serializers.ValidationError("Manager must be an active manager or super admin.")
        return value

    def validate(self, attrs):
        start, due = attrs.get("start_date"), attrs.get("due_date")
        if self.instance is not None:
            start = start if "start_date" in attrs else self.instance.start_date
            due = due if "due_date" in attrs else self.instance.due_date
        if start and due and due < start:
            raise serializers.ValidationError({"due_date": "Due date cannot be before the start date."})
        for field in ("estimated_cost", "actual_cost"):
            if attrs.get(field) is not None and attrs[field] < 0:
                raise serializers.ValidationError({field: "Cost cannot be negative."})
        return attrs


class TransitionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Job.Status.choices)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class AssignSerializer(serializers.Serializer):
    staff = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True, role=Roles.STAFF), many=True
    )


class JobCommentSerializer(serializers.ModelSerializer):
    author = UserBriefSerializer(read_only=True)
    job = serializers.PrimaryKeyRelatedField(queryset=Job.objects.all())

    class Meta:
        model = JobComment
        fields = ["id", "job", "author", "kind", "body", "created_at"]
        read_only_fields = ["id", "author", "created_at"]


class JobAttachmentSerializer(serializers.ModelSerializer):
    uploaded_by = UserBriefSerializer(read_only=True)
    job = serializers.PrimaryKeyRelatedField(queryset=Job.objects.all())
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = JobAttachment
        fields = ["id", "job", "uploaded_by", "file", "original_name", "size",
                  "content_type", "download_url", "created_at"]
        read_only_fields = ["id", "uploaded_by", "original_name", "size",
                            "content_type", "download_url", "created_at"]
        extra_kwargs = {"file": {"write_only": True}}

    def get_download_url(self, obj) -> str:
        return f"/api/v1/job-attachments/{obj.pk}/download/"

    def validate_file(self, value):
        validate_upload(value)
        return value


class JobAssignmentSerializer(serializers.ModelSerializer):
    staff = UserBriefSerializer(read_only=True)
    assigned_by = UserBriefSerializer(read_only=True)
    job_number = serializers.CharField(source="job.number", read_only=True)

    class Meta:
        model = JobAssignment
        fields = ["id", "job", "job_number", "staff", "assigned_by", "assigned_at"]


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
class ScheduledTaskSerializer(serializers.ModelSerializer):
    assignees = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True, role=Roles.STAFF),
        many=True, required=False,
    )
    assignee_details = UserBriefSerializer(source="assignees", many=True, read_only=True)
    created_by = UserBriefSerializer(read_only=True)

    class Meta:
        model = ScheduledTask
        fields = [
            "id", "name", "description", "action", "frequency", "time_of_day",
            "weekday", "day_of_month", "interval_minutes", "starts_at", "ends_at",
            "assignees", "assignee_details", "team_department",
            "job_category", "job_department", "job_manager", "job_priority", "job_due_days",
            "status", "next_run_at", "last_run_at", "failure_count", "last_error",
            "created_by", "created_at",
        ]
        read_only_fields = ["id", "status", "next_run_at", "last_run_at",
                            "failure_count", "last_error", "created_by", "created_at"]

    def validate(self, attrs):
        # Run the model's clean() against the would-be final state so API
        # writes obey the same recurrence rules as the web forms.
        if self.instance is not None:
            candidate = ScheduledTask(**{
                f.attname: getattr(self.instance, f.attname)
                for f in ScheduledTask._meta.concrete_fields
            })
        else:
            candidate = ScheduledTask()
        for key, value in attrs.items():
            if key != "assignees":
                setattr(candidate, key, value)
        candidate.full_clean(exclude=["created_by", "next_run_at"])
        return attrs


class TaskOccurrenceSerializer(serializers.ModelSerializer):
    scheduled_task_name = serializers.CharField(source="scheduled_task.name", read_only=True)
    assigned_to = UserBriefSerializer(read_only=True)
    completed_by = UserBriefSerializer(read_only=True)
    created_job_number = serializers.CharField(source="created_job.number", read_only=True, default=None)

    class Meta:
        model = TaskOccurrence
        fields = ["id", "scheduled_task", "scheduled_task_name", "scheduled_for",
                  "assigned_to", "status", "completed_at", "completed_by", "notes",
                  "created_job", "created_job_number"]
        read_only_fields = fields


class SchedulerRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchedulerRun
        fields = ["id", "started_at", "finished_at", "status", "triggered_by",
                  "tasks_processed", "occurrences_created", "jobs_created",
                  "occurrences_missed", "jobs_marked_overdue", "due_soon_notices",
                  "errors_count"]


class CompleteOccurrenceSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, default="")


# ---------------------------------------------------------------------------
# Notifications / audit
# ---------------------------------------------------------------------------
class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "type", "title", "message", "url", "is_read", "read_at", "created_at"]
        read_only_fields = fields


class AuditLogSerializer(serializers.ModelSerializer):
    actor = UserBriefSerializer(read_only=True)

    class Meta:
        model = AuditLog
        fields = ["id", "actor", "actor_email", "action", "object_type", "object_id",
                  "object_repr", "changes", "metadata", "ip_address", "created_at"]
        read_only_fields = fields
