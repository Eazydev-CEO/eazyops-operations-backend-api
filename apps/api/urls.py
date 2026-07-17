from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from . import auth, views

app_name = "api"

router = DefaultRouter()
router.register("users", views.UserViewSet, basename="user")
router.register("invitations", views.InvitationViewSet, basename="invitation")
router.register("departments", views.DepartmentViewSet, basename="department")
router.register("categories", views.CategoryViewSet, basename="category")
router.register("jobs", views.JobViewSet, basename="job")
router.register("job-comments", views.JobCommentViewSet, basename="job-comment")
router.register("job-attachments", views.JobAttachmentViewSet, basename="job-attachment")
router.register("assignments", views.JobAssignmentViewSet, basename="assignment")
router.register("scheduled-tasks", views.ScheduledTaskViewSet, basename="scheduled-task")
router.register("occurrences", views.TaskOccurrenceViewSet, basename="occurrence")
router.register("scheduler-runs", views.SchedulerRunViewSet, basename="scheduler-run")
router.register("notifications", views.NotificationViewSet, basename="notification")
router.register("audit-logs", views.AuditLogViewSet, basename="audit-log")

v1_patterns = [
    # Authentication
    path("auth/token/", auth.ObtainTokenView.as_view(), name="token_obtain"),
    path("auth/token/refresh/", auth.RefreshTokenView.as_view(), name="token_refresh"),
    path("auth/token/verify/", auth.VerifyTokenView.as_view(), name="token_verify"),
    # Profile, roles, dashboard, reports
    path("me/", views.MeView.as_view(), name="me"),
    path("roles/", views.RoleListView.as_view(), name="roles"),
    path("dashboard/summary/", views.DashboardSummaryView.as_view(), name="dashboard_summary"),
    path("reports/<slug:slug>/", views.ReportView.as_view(), name="report"),
    # Resources
    path("", include(router.urls)),
]

urlpatterns = [
    path("v1/", include((v1_patterns, "v1"))),
    # OpenAPI schema + interactive documentation. These live outside the
    # versioned namespace, so NamespaceVersioning must not apply to them.
    path("schema/", SpectacularAPIView.as_view(api_version="v1", versioning_class=None), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="api:schema", versioning_class=None), name="docs"),
    path("redoc/", SpectacularRedocView.as_view(url_name="api:schema", versioning_class=None), name="redoc"),
]
