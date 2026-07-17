"""REST API v1 viewsets. Business rules live in the app service layers."""
import django_filters
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import FileResponse, Http404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import services as account_services
from apps.accounts.models import Invitation
from apps.audits.models import AuditLog
from apps.core.models import Department
from apps.core.rbac import Roles, user_can
from apps.dashboard.services import dashboard_data
from apps.notifications import services as notification_services
from apps.notifications.models import Notification
from apps.operations import services as job_services
from apps.operations.models import (
    Job, JobAssignment, JobAttachment, JobCategory, JobComment,
)
from apps.reports import services as report_services
from apps.scheduling import services as scheduling_services
from apps.scheduling.models import ScheduledTask, SchedulerRun, TaskOccurrence

from .permissions import HasCapability, IsActiveAuthenticated
from .serializers import (
    AssignSerializer, AuditLogSerializer, CategorySerializer,
    CompleteOccurrenceSerializer, DepartmentSerializer, InvitationSerializer,
    JobAssignmentSerializer, JobAttachmentSerializer, JobCommentSerializer,
    JobDetailSerializer, JobListSerializer, JobWriteSerializer, MeSerializer,
    NotificationSerializer, RoleSerializer, ScheduledTaskSerializer,
    SchedulerRunSerializer, TaskOccurrenceSerializer, TransitionSerializer,
    UserAdminUpdateSerializer, UserSerializer,
)
from .throttling import SensitiveScopedThrottle

User = get_user_model()


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------
class MeView(APIView):
    permission_classes = [IsActiveAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(responses=MeSerializer, description="Current authenticated user profile.")
    def get(self, request):
        return Response(MeSerializer(request.user, context={"request": request}).data)

    @extend_schema(request=MeSerializer, responses=MeSerializer,
                   description="Update own profile fields (name, phone, photo, email preferences).")
    def patch(self, request):
        serializer = MeSerializer(
            request.user, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Users / roles / invitations
# ---------------------------------------------------------------------------
class UserViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                  mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """User administration. Read: managers+. Write: Super Admin."""

    queryset = User.objects.select_related("department").order_by("first_name", "last_name")
    permission_classes = [HasCapability]
    required_capability = {"read": "users.view", "write": "users.manage"}
    throttle_classes = [SensitiveScopedThrottle]
    throttle_scope = "sensitive"
    filterset_fields = ["role", "is_active", "department"]
    search_fields = ["email", "first_name", "last_name", "job_title"]
    ordering_fields = ["first_name", "last_name", "email", "date_joined", "last_activity_at"]

    def get_serializer_class(self):
        if self.action in ("update", "partial_update"):
            return UserAdminUpdateSerializer
        return UserSerializer

    def perform_update(self, serializer):
        account_services.update_user_management_fields(
            actor=self.request.user,
            user=self.get_object(),
            cleaned=serializer.validated_data,
            request=self.request,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        instance.refresh_from_db()
        return Response(UserSerializer(instance, context={"request": request}).data)

    @extend_schema(request=None, responses=UserSerializer,
                   description="Deactivate a user account (audit history is preserved).")
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        user = self.get_object()
        account_services.set_user_active(actor=request.user, user=user, active=False, request=request)
        user.refresh_from_db()
        return Response(UserSerializer(user, context={"request": request}).data)

    @extend_schema(request=None, responses=UserSerializer, description="Reactivate a user account.")
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        user = self.get_object()
        account_services.set_user_active(actor=request.user, user=user, active=True, request=request)
        user.refresh_from_db()
        return Response(UserSerializer(user, context={"request": request}).data)


class RoleListView(APIView):
    permission_classes = [HasCapability]
    required_capability = "roles.view"

    @extend_schema(responses=RoleSerializer(many=True),
                   description="Role catalogue with the capability matrix used across web and API.")
    def get(self, request):
        return Response(RoleSerializer(RoleSerializer.build_matrix(), many=True).data)


class InvitationViewSet(mixins.ListModelMixin, mixins.CreateModelMixin,
                        mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Invite-only registration: create and track invitations."""

    queryset = Invitation.objects.select_related("invited_by", "department")
    serializer_class = InvitationSerializer
    permission_classes = [HasCapability]
    required_capability = "users.manage"
    throttle_classes = [SensitiveScopedThrottle]
    throttle_scope = "sensitive"

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = account_services.create_invitation(
            inviter=request.user,
            email=serializer.validated_data["email"],
            role=serializer.validated_data.get("role", Roles.STAFF),
            department=serializer.validated_data.get("department"),
            job_title=serializer.validated_data.get("job_title", ""),
            request=request,
        )
        out = self.get_serializer(invitation)
        return Response(out.data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=InvitationSerializer, description="Cancel a pending invitation.")
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        invitation = self.get_object()
        account_services.cancel_invitation(invitation=invitation, actor=request.user, request=request)
        invitation.refresh_from_db()
        return Response(self.get_serializer(invitation).data)


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [HasCapability]
    required_capability = {"read": None, "write": "departments.manage"}
    filterset_fields = ["kind", "is_active"]
    search_fields = ["name"]

    def destroy(self, request, *args, **kwargs):
        return Response(
            {"success": False,
             "error": {"code": "method_not_allowed",
                       "detail": "Departments are deactivated, not deleted."}},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = JobCategory.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [HasCapability]
    required_capability = {"read": None, "write": "categories.manage"}
    filterset_fields = ["is_active"]
    search_fields = ["name"]

    def destroy(self, request, *args, **kwargs):
        return Response(
            {"success": False,
             "error": {"code": "method_not_allowed",
                       "detail": "Categories are deactivated, not deleted."}},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
class JobFilter(django_filters.FilterSet):
    status = django_filters.CharFilter(method="filter_status")
    due_from = django_filters.DateFilter(field_name="due_date", lookup_expr="gte")
    due_to = django_filters.DateFilter(field_name="due_date", lookup_expr="lte")
    created_from = django_filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    created_to = django_filters.DateFilter(field_name="created_at", lookup_expr="date__lte")
    staff = django_filters.NumberFilter(field_name="assignments__staff_id", distinct=True)
    tag = django_filters.CharFilter(field_name="tags__name", lookup_expr="iexact", distinct=True)

    class Meta:
        model = Job
        fields = ["status", "priority", "category", "department", "manager"]

    def filter_status(self, queryset, name, value):
        value = (value or "").upper()
        if value == Job.Status.OVERDUE:
            return queryset.overdue()
        if value in Job.Status.values:
            return queryset.filter(status=value)
        return queryset.none()


class JobViewSet(viewsets.ModelViewSet):
    """Jobs / work orders with workflow actions."""

    permission_classes = [HasCapability]
    required_capability = {
        "create": "jobs.create",
        "destroy": None,  # enforced (and audited) inside the service layer
        "read": None,     # row-level scoping via visible_to()
        "write": None,    # object checks in services
    }
    filterset_class = JobFilter
    search_fields = ["number", "title", "description", "tags__name"]
    ordering_fields = ["created_at", "due_date", "priority", "number", "title", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = Job.objects.visible_to(self.request.user).select_related(
            "category", "department", "manager", "reviewed_by", "created_by"
        ).prefetch_related("staff", "tags")
        return qs.distinct()

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return JobWriteSerializer
        if self.action == "retrieve":
            return JobDetailSerializer
        return JobListSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        staff = data.pop("staff", [])
        tags = data.pop("tags", [])
        job = job_services.create_job(
            actor=request.user, data=data, staff=staff,
            tags_raw=", ".join(tags), request=request,
        )
        return Response(
            JobDetailSerializer(job, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        job = self.get_object()
        serializer = self.get_serializer(job, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data.pop("status", None)
        staff = data.pop("staff", None)
        tags = data.pop("tags", None)
        job_services.update_job(
            actor=request.user, job=job, data=data,
            tags_raw=", ".join(tags) if tags is not None else None,
            request=request,
        )
        if staff is not None:
            job_services.assign_staff(actor=request.user, job=job, staff_users=staff, request=request)
        job.refresh_from_db()
        return Response(JobDetailSerializer(job, context={"request": request}).data)

    def destroy(self, request, *args, **kwargs):
        job = self.get_object()
        job_services.delete_job(actor=request.user, job=job, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=TransitionSerializer, responses=JobDetailSerializer,
                   description="Perform a validated status transition (see the status machine in API docs).")
    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        job = self.get_object()
        serializer = TransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job_services.transition_job(
            actor=request.user, job=job,
            new_status=serializer.validated_data["status"],
            note=serializer.validated_data.get("note", ""),
            request=request,
        )
        job.refresh_from_db()
        return Response(JobDetailSerializer(job, context={"request": request}).data)

    @extend_schema(request=AssignSerializer, responses=JobDetailSerializer,
                   description="Replace the set of assigned staff (management only).")
    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        job = self.get_object()
        serializer = AssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job_services.assign_staff(
            actor=request.user, job=job,
            staff_users=list(serializer.validated_data["staff"]), request=request,
        )
        job.refresh_from_db()
        return Response(JobDetailSerializer(job, context={"request": request}).data)

    @extend_schema(responses=AuditLogSerializer(many=True),
                   description="Activity timeline (audit entries) for this job.")
    @action(detail=True, methods=["get"])
    def timeline(self, request, pk=None):
        job = self.get_object()
        entries = AuditLog.objects.filter(
            object_type="operations.job", object_id=str(job.pk)
        ).select_related("actor")[:100]
        return Response(AuditLogSerializer(entries, many=True).data)


class JobCommentViewSet(mixins.ListModelMixin, mixins.CreateModelMixin,
                        mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Comments & progress updates. List requires ?job=<id>."""

    serializer_class = JobCommentSerializer
    permission_classes = [IsActiveAuthenticated]
    filterset_fields = ["job", "kind"]

    def get_queryset(self):
        visible_jobs = Job.objects.visible_to(self.request.user)
        return JobComment.objects.filter(job__in=visible_jobs).select_related("author", "job")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = serializer.validated_data["job"]
        if not job_services.can_view_job(request.user, job):
            raise Http404
        comment = job_services.add_comment(
            actor=request.user, job=job,
            body=serializer.validated_data["body"],
            kind=serializer.validated_data.get("kind", JobComment.Kind.COMMENT),
            request=request,
        )
        return Response(self.get_serializer(comment).data, status=status.HTTP_201_CREATED)


class JobAttachmentViewSet(mixins.ListModelMixin, mixins.CreateModelMixin,
                           mixins.RetrieveModelMixin, mixins.DestroyModelMixin,
                           viewsets.GenericViewSet):
    """Job attachments (evidence uploads). List requires ?job=<id>."""

    serializer_class = JobAttachmentSerializer
    permission_classes = [IsActiveAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    filterset_fields = ["job"]

    def get_queryset(self):
        visible_jobs = Job.objects.visible_to(self.request.user)
        return JobAttachment.objects.filter(job__in=visible_jobs).select_related("uploaded_by", "job")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = serializer.validated_data["job"]
        if not job_services.can_view_job(request.user, job):
            raise Http404
        attachment = job_services.add_attachment(
            actor=request.user, job=job,
            uploaded_file=serializer.validated_data["file"], request=request,
        )
        return Response(self.get_serializer(attachment).data, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        job_services.delete_attachment(actor=self.request.user, attachment=instance, request=self.request)

    @extend_schema(description="Download the attachment binary (permission-gated).")
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        attachment = self.get_object()
        try:
            handle = attachment.file.open("rb")
        except (FileNotFoundError, ValueError):
            raise Http404
        return FileResponse(handle, as_attachment=True, filename=attachment.original_name)


class JobAssignmentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                           viewsets.GenericViewSet):
    """Read-only assignment records (who is/was assigned, by whom, when)."""

    serializer_class = JobAssignmentSerializer
    permission_classes = [HasCapability]
    required_capability = "jobs.view_all"
    filterset_fields = ["job", "staff"]
    ordering_fields = ["assigned_at"]

    def get_queryset(self):
        return JobAssignment.objects.select_related("job", "staff", "assigned_by")


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
class ScheduledTaskViewSet(viewsets.ModelViewSet):
    serializer_class = ScheduledTaskSerializer
    permission_classes = [HasCapability]
    required_capability = {"read": "schedules.view", "write": "schedules.manage"}
    filterset_fields = ["status", "frequency", "action"]
    search_fields = ["name", "description"]
    ordering_fields = ["next_run_at", "name", "created_at"]

    def get_queryset(self):
        qs = ScheduledTask.objects.select_related(
            "team_department", "job_category", "job_department", "job_manager", "created_by"
        ).prefetch_related("assignees")
        if getattr(self, "swagger_fake_view", False):
            return qs.none()
        user = self.request.user
        if user.role == Roles.STAFF:
            qs = qs.filter(
                Q(assignees=user) | Q(team_department_id=user.department_id,
                                      team_department__isnull=False)
            ).distinct()
        return qs

    def perform_create(self, serializer):
        assignees = serializer.validated_data.pop("assignees", None)
        instance = ScheduledTask(**serializer.validated_data)
        scheduling_services.create_scheduled_task(
            actor=self.request.user, instance=instance,
            assignees=assignees, request=self.request,
        )
        serializer.instance = instance

    def perform_update(self, serializer):
        assignees = serializer.validated_data.pop("assignees", None)
        instance = serializer.instance
        for key, value in serializer.validated_data.items():
            setattr(instance, key, value)
        scheduling_services.update_scheduled_task(
            actor=self.request.user, instance=instance,
            assignees=assignees, request=self.request,
        )

    def destroy(self, request, *args, **kwargs):
        return Response(
            {"success": False,
             "error": {"code": "method_not_allowed",
                       "detail": "Pause the schedule instead of deleting it (history is preserved)."}},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @extend_schema(request=None, responses=ScheduledTaskSerializer, description="Pause the schedule.")
    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        task = self.get_object()
        scheduling_services.set_task_status(
            actor=request.user, task=task, status=ScheduledTask.Status.PAUSED, request=request
        )
        return Response(self.get_serializer(task).data)

    @extend_schema(request=None, responses=ScheduledTaskSerializer, description="Resume the schedule.")
    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        task = self.get_object()
        scheduling_services.set_task_status(
            actor=request.user, task=task, status=ScheduledTask.Status.ACTIVE, request=request
        )
        return Response(self.get_serializer(task).data)


class TaskOccurrenceViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                            viewsets.GenericViewSet):
    """Materialised occurrences. Staff see their own; management sees all."""

    serializer_class = TaskOccurrenceSerializer
    permission_classes = [IsActiveAuthenticated]
    filterset_fields = ["status", "scheduled_task", "assigned_to"]
    ordering_fields = ["scheduled_for"]

    def get_queryset(self):
        qs = TaskOccurrence.objects.select_related(
            "scheduled_task", "assigned_to", "completed_by", "created_job"
        )
        if getattr(self, "swagger_fake_view", False):
            return qs.none()
        user = self.request.user
        if user.role in (Roles.SUPER_ADMIN, Roles.MANAGER):
            return qs
        if user.role == Roles.STAFF:
            return qs.filter(assigned_to=user)
        return qs.none()

    @extend_schema(request=CompleteOccurrenceSerializer, responses=TaskOccurrenceSerializer,
                   description="Mark an occurrence completed (assignee or management).")
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        occurrence = self.get_object()
        serializer = CompleteOccurrenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        scheduling_services.complete_occurrence(
            actor=request.user, occurrence=occurrence,
            notes=serializer.validated_data.get("notes", ""), request=request,
        )
        occurrence.refresh_from_db()
        return Response(self.get_serializer(occurrence).data)


class SchedulerRunViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                          viewsets.GenericViewSet):
    """Scheduler execution log (monitoring)."""

    queryset = SchedulerRun.objects.all()
    serializer_class = SchedulerRunSerializer
    permission_classes = [HasCapability]
    required_capability = "schedules.run_monitor"
    filterset_fields = ["status", "triggered_by"]


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
class NotificationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                          viewsets.GenericViewSet):
    """Strictly the requesting user's notifications."""

    serializer_class = NotificationSerializer
    permission_classes = [IsActiveAuthenticated]
    filterset_fields = ["is_read", "type"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()
        return Notification.objects.filter(user=self.request.user)

    @extend_schema(request=None, responses=NotificationSerializer, description="Mark one notification read.")
    @action(detail=True, methods=["post"], url_path="read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification_services.mark_read(request.user, notification.pk)
        notification.refresh_from_db()
        return Response(self.get_serializer(notification).data)

    @extend_schema(request=None, responses={200: None}, description="Mark all notifications read.")
    @action(detail=False, methods=["post"], url_path="read-all")
    def mark_all_read(self, request):
        updated = notification_services.mark_all_read(request.user)
        return Response({"updated": updated})

    @extend_schema(responses={200: None}, description="Unread notification count.")
    @action(detail=False, methods=["get"])
    def unread_count(self, request):
        count = Notification.objects.filter(user=request.user, is_read=False).count()
        return Response({"unread": count})


# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------
class AuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                      viewsets.GenericViewSet):
    """Read-only audit trail for authorised roles."""

    serializer_class = AuditLogSerializer
    permission_classes = [HasCapability]
    required_capability = "audits.view"
    throttle_classes = [SensitiveScopedThrottle]
    throttle_scope = "sensitive"
    filterset_fields = ["action", "object_type", "actor"]
    search_fields = ["object_repr", "actor_email", "object_id"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        qs = AuditLog.objects.select_related("actor")
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        return qs


# ---------------------------------------------------------------------------
# Dashboard & reports
# ---------------------------------------------------------------------------
class DashboardSummaryView(APIView):
    permission_classes = [IsActiveAuthenticated]

    @extend_schema(
        parameters=[OpenApiParameter("range", int, description="Window in days (7–365, default 30)")],
        responses={200: None},
        description="Aggregated dashboard data scoped to the caller's role.",
    )
    def get(self, request):
        try:
            days = int(request.query_params.get("range", 30))
        except ValueError:
            days = 30
        return Response(dashboard_data(request.user, days=days))


REPORT_HANDLERS = {
    "status": ("reports.view", lambda request, jobs: report_services.jobs_by_status(jobs)),
    "category": ("reports.view", lambda request, jobs: report_services.jobs_by_category(jobs)),
    "priority": ("reports.view", lambda request, jobs: report_services.jobs_by_priority(jobs)),
    "overdue": ("reports.view", lambda request, jobs: report_services.overdue_jobs(jobs)),
    "workload": ("reports.full", lambda request, jobs: report_services.staff_workload()),
    "performance": ("reports.full", lambda request, jobs: report_services.staff_performance(
        request.query_params.get("date_from") or None,
        request.query_params.get("date_to") or None,
    )),
    "completion-time": ("reports.full", lambda request, jobs: report_services.completion_time(jobs)),
    "scheduled": ("reports.full", lambda request, jobs: report_services.scheduled_task_report(
        request.query_params.get("date_from") or None,
        request.query_params.get("date_to") or None,
    )),
    "audit": ("reports.full", lambda request, jobs: report_services.audit_activity(
        request.query_params.get("date_from") or None,
        request.query_params.get("date_to") or None,
    )),
}


class ReportView(APIView):
    permission_classes = [IsActiveAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter("date_from", str), OpenApiParameter("date_to", str),
            OpenApiParameter("staff", int), OpenApiParameter("category", int),
            OpenApiParameter("status", str), OpenApiParameter("priority", str),
        ],
        responses={200: None},
        description=(
            "Report data as JSON. Slugs: status, category, priority, overdue, "
            "workload, performance, completion-time, scheduled, audit."
        ),
    )
    def get(self, request, slug):
        handler = REPORT_HANDLERS.get(slug)
        if handler is None:
            raise Http404
        capability, fn = handler
        if not user_can(request.user, capability):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied
        jobs = report_services.apply_report_filters(
            Job.objects.visible_to(request.user), request.query_params
        )
        return Response({"slug": slug, "data": fn(request, jobs)})
