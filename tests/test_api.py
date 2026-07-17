"""REST API v1: auth, scoping, permissions, pagination, filters, envelope."""
import pytest
from django.utils import timezone

from apps.audits.models import Actions, AuditLog
from apps.notifications import services as notif
from apps.notifications.models import NotificationTypes
from apps.operations import services as job_services
from apps.operations.models import Job

from .conftest import PASSWORD, make_job

pytestmark = pytest.mark.django_db

V1 = "/api/v1"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
class TestJWTAuth:
    def test_obtain_refresh_and_use(self, api_client, manager):
        response = api_client.post(
            f"{V1}/auth/token/", {"email": manager.email, "password": PASSWORD}
        )
        assert response.status_code == 200
        tokens = response.json()
        assert "access" in tokens and "refresh" in tokens
        assert AuditLog.objects.filter(action=Actions.API_TOKEN_ISSUED).exists()

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        me = api_client.get(f"{V1}/me/")
        assert me.status_code == 200
        assert me.json()["email"] == manager.email

        refreshed = api_client.post(f"{V1}/auth/token/refresh/", {"refresh": tokens["refresh"]})
        assert refreshed.status_code == 200
        assert "access" in refreshed.json()

    def test_bad_credentials_return_consistent_envelope(self, api_client, manager):
        response = api_client.post(
            f"{V1}/auth/token/", {"email": manager.email, "password": "nope"}
        )
        assert response.status_code == 401
        body = response.json()
        assert body["success"] is False
        assert "detail" in body["error"] and "code" in body["error"]

    def test_lockout_applies_to_api(self, api_client, manager, settings):
        settings.LOGIN_MAX_FAILURES = 2
        for _ in range(2):
            api_client.post(f"{V1}/auth/token/", {"email": manager.email, "password": "no"})
        response = api_client.post(
            f"{V1}/auth/token/", {"email": manager.email, "password": PASSWORD}
        )
        assert response.status_code == 401
        assert "Too many failed attempts" in response.json()["error"]["detail"]

    def test_anonymous_rejected(self, api_client):
        assert api_client.get(f"{V1}/jobs/").status_code == 401

    def test_me_patch_profile(self, api_client, staff_user):
        api_client.force_authenticate(staff_user)
        response = api_client.patch(f"{V1}/me/", {"phone": "+1-555-0000"})
        assert response.status_code == 200
        staff_user.refresh_from_db()
        assert staff_user.phone == "+1-555-0000"

    def test_me_cannot_escalate_role(self, api_client, staff_user):
        api_client.force_authenticate(staff_user)
        api_client.patch(f"{V1}/me/", {"role": "SUPER_ADMIN"})
        staff_user.refresh_from_db()
        assert staff_user.role == "STAFF"


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
class TestJobsAPI:
    def test_role_scoped_listing(self, api_client, job, manager, staff_user, viewer_other_dept):
        api_client.force_authenticate(manager)
        assert api_client.get(f"{V1}/jobs/").json()["count"] == 1
        api_client.force_authenticate(staff_user)  # not assigned
        assert api_client.get(f"{V1}/jobs/").json()["count"] == 0
        api_client.force_authenticate(viewer_other_dept)  # other department
        assert api_client.get(f"{V1}/jobs/").json()["count"] == 0

    def test_detail_idor_returns_404(self, api_client, job, viewer_other_dept):
        api_client.force_authenticate(viewer_other_dept)
        assert api_client.get(f"{V1}/jobs/{job.pk}/").status_code == 404

    def test_create_requires_capability(self, api_client, staff_user, manager, dept_a, category):
        payload = {
            "title": "API created job", "category": category.pk, "department": dept_a.pk,
            "manager": manager.pk, "priority": "HIGH",
        }
        api_client.force_authenticate(staff_user)
        assert api_client.post(f"{V1}/jobs/", payload).status_code == 403
        api_client.force_authenticate(manager)
        response = api_client.post(f"{V1}/jobs/", payload)
        assert response.status_code == 201
        body = response.json()
        assert body["number"].startswith("JOB-")
        assert AuditLog.objects.filter(action=Actions.JOB_CREATED).exists()

    def test_create_with_staff_and_tags(self, api_client, manager, staff_user, dept_a, category):
        api_client.force_authenticate(manager)
        response = api_client.post(f"{V1}/jobs/", {
            "title": "With crew", "category": category.pk, "department": dept_a.pk,
            "manager": manager.pk, "staff": [staff_user.pk], "tags": ["hvac", "urgent-fix"],
        })
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "ASSIGNED"
        assert sorted(body["tags"]) == ["hvac", "urgent-fix"]

    def test_validation_error_envelope(self, api_client, manager, dept_a, category):
        api_client.force_authenticate(manager)
        response = api_client.post(f"{V1}/jobs/", {
            "title": "Bad dates", "category": category.pk, "department": dept_a.pk,
            "manager": manager.pk, "start_date": "2026-07-10", "due_date": "2026-07-01",
        })
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "due_date" in body["error"]["fields"]

    def test_transition_endpoint(self, api_client, assigned_job, staff_user):
        api_client.force_authenticate(staff_user)
        response = api_client.post(
            f"{V1}/jobs/{assigned_job.pk}/transition/", {"status": "IN_PROGRESS"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "IN_PROGRESS"

    def test_invalid_transition_400(self, api_client, job, manager):
        api_client.force_authenticate(manager)
        response = api_client.post(f"{V1}/jobs/{job.pk}/transition/", {"status": "COMPLETED"})
        assert response.status_code == 400
        assert "Invalid transition" in response.json()["error"]["detail"]

    def test_staff_transition_permission_enforced(self, api_client, assigned_job, staff_user2):
        api_client.force_authenticate(staff_user2)  # sees nothing -> 404 (no leak)
        response = api_client.post(
            f"{V1}/jobs/{assigned_job.pk}/transition/", {"status": "IN_PROGRESS"}
        )
        assert response.status_code == 404

    def test_assign_action(self, api_client, job, manager, staff_user):
        api_client.force_authenticate(manager)
        response = api_client.post(f"{V1}/jobs/{job.pk}/assign/", {"staff": [staff_user.pk]})
        assert response.status_code == 200
        assert response.json()["status"] == "ASSIGNED"

    def test_delete_super_admin_only(self, api_client, job, manager, admin_user):
        api_client.force_authenticate(manager)
        assert api_client.delete(f"{V1}/jobs/{job.pk}/").status_code == 403
        api_client.force_authenticate(admin_user)
        assert api_client.delete(f"{V1}/jobs/{job.pk}/").status_code == 204

    def test_filters_and_ordering(self, api_client, manager, staff_user, dept_a, dept_b, category):
        make_job(manager, dept_a, category, title="Low prio", priority=Job.Priority.LOW)
        make_job(manager, dept_b, category, title="Urgent late", priority=Job.Priority.URGENT,
                 due_date=timezone.localdate() - timezone.timedelta(days=1))
        api_client.force_authenticate(manager)
        assert api_client.get(f"{V1}/jobs/?priority=URGENT").json()["count"] == 1
        assert api_client.get(f"{V1}/jobs/?status=OVERDUE").json()["count"] == 1
        assert api_client.get(f"{V1}/jobs/?department={dept_b.pk}").json()["count"] == 1
        assert api_client.get(f"{V1}/jobs/?search=Low").json()["count"] == 1
        results = api_client.get(f"{V1}/jobs/?ordering=title").json()["results"]
        titles = [r["title"] for r in results]
        assert titles == sorted(titles)

    def test_pagination_contract(self, api_client, manager, dept_a, category):
        for i in range(25):
            make_job(manager, dept_a, category, title=f"pg {i}")
        api_client.force_authenticate(manager)
        body = api_client.get(f"{V1}/jobs/?page_size=10").json()
        assert body["count"] == 25
        assert len(body["results"]) == 10
        assert body["next"] is not None
        body2 = api_client.get(f"{V1}/jobs/?page_size=10&page=3").json()
        assert len(body2["results"]) == 5

    def test_timeline_action(self, api_client, assigned_job, manager):
        api_client.force_authenticate(manager)
        response = api_client.get(f"{V1}/jobs/{assigned_job.pk}/timeline/")
        assert response.status_code == 200
        actions = [entry["action"] for entry in response.json()]
        assert Actions.JOB_CREATED in actions


class TestCommentsAttachmentsAPI:
    def test_comment_create_and_scope(self, api_client, assigned_job, staff_user, viewer_other_dept):
        api_client.force_authenticate(staff_user)
        response = api_client.post(
            f"{V1}/job-comments/", {"job": assigned_job.pk, "body": "Working on it", "kind": "PROGRESS"}
        )
        assert response.status_code == 201
        api_client.force_authenticate(viewer_other_dept)
        listing = api_client.get(f"{V1}/job-comments/?job={assigned_job.pk}").json()
        assert listing["count"] == 0  # other department sees nothing

    def test_attachment_upload_validation(self, api_client, assigned_job, staff_user):
        from django.core.files.uploadedfile import SimpleUploadedFile

        api_client.force_authenticate(staff_user)
        bad = SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream")
        response = api_client.post(
            f"{V1}/job-attachments/", {"job": assigned_job.pk, "file": bad}, format="multipart"
        )
        assert response.status_code == 400
        assert "file" in response.json()["error"]["fields"]


class TestAdminResourcesAPI:
    def test_users_read_requires_management(self, api_client, staff_user, manager):
        api_client.force_authenticate(staff_user)
        assert api_client.get(f"{V1}/users/").status_code == 403
        api_client.force_authenticate(manager)
        assert api_client.get(f"{V1}/users/").status_code == 200

    def test_user_write_requires_super_admin(self, api_client, manager, admin_user, staff_user):
        api_client.force_authenticate(manager)
        assert api_client.patch(
            f"{V1}/users/{staff_user.pk}/", {"job_title": "X"}
        ).status_code == 403
        api_client.force_authenticate(admin_user)
        response = api_client.patch(f"{V1}/users/{staff_user.pk}/", {"job_title": "Senior Tech"})
        assert response.status_code == 200
        staff_user.refresh_from_db()
        assert staff_user.job_title == "Senior Tech"

    def test_deactivate_action(self, api_client, admin_user, staff_user):
        api_client.force_authenticate(admin_user)
        response = api_client.post(f"{V1}/users/{staff_user.pk}/deactivate/")
        assert response.status_code == 200
        staff_user.refresh_from_db()
        assert staff_user.is_active is False

    def test_invitation_create_via_api(self, api_client, admin_user, manager):
        api_client.force_authenticate(manager)
        assert api_client.post(
            f"{V1}/invitations/", {"email": "x@test.local", "role": "STAFF"}
        ).status_code == 403
        api_client.force_authenticate(admin_user)
        response = api_client.post(
            f"{V1}/invitations/", {"email": "x@test.local", "role": "STAFF"}
        )
        assert response.status_code == 201
        assert response.json()["status"] == "PENDING"

    def test_roles_matrix(self, api_client, manager):
        api_client.force_authenticate(manager)
        body = api_client.get(f"{V1}/roles/").json()
        keys = {row["key"] for row in body}
        assert keys == {"SUPER_ADMIN", "MANAGER", "STAFF", "VIEWER"}

    def test_audit_logs_restricted_and_filterable(self, api_client, staff_user, manager):
        notif.notify(manager, NotificationTypes.SYSTEM, title="noise")
        api_client.force_authenticate(staff_user)
        assert api_client.get(f"{V1}/audit-logs/").status_code == 403
        api_client.force_authenticate(manager)
        response = api_client.get(f"{V1}/audit-logs/?action=LOGIN")
        assert response.status_code == 200

    def test_scheduled_tasks_manage_permission(self, api_client, staff_user, manager, dept_a):
        payload = {
            "name": "API schedule", "action": "CREATE_OCCURRENCE", "frequency": "DAILY",
            "time_of_day": "08:00", "starts_at": timezone.now().isoformat(),
        }
        api_client.force_authenticate(staff_user)
        assert api_client.post(f"{V1}/scheduled-tasks/", payload).status_code == 403
        api_client.force_authenticate(manager)
        response = api_client.post(f"{V1}/scheduled-tasks/", payload)
        assert response.status_code == 201
        assert response.json()["next_run_at"] is not None

    def test_occurrence_complete_via_api(self, api_client, manager, staff_user):
        from apps.scheduling.models import TaskOccurrence
        from .test_scheduling import make_task

        task = make_task(manager, assignees=[staff_user])
        occurrence = TaskOccurrence.objects.create(
            scheduled_task=task, assigned_to=staff_user, scheduled_for=timezone.now()
        )
        api_client.force_authenticate(staff_user)
        response = api_client.post(f"{V1}/occurrences/{occurrence.pk}/complete/", {"notes": "ok"})
        assert response.status_code == 200
        assert response.json()["status"] == "COMPLETED"

    def test_notifications_own_only_and_actions(self, api_client, staff_user, manager):
        notif.notify(staff_user, NotificationTypes.SYSTEM, title="mine")
        notif.notify(manager, NotificationTypes.SYSTEM, title="theirs")
        api_client.force_authenticate(staff_user)
        body = api_client.get(f"{V1}/notifications/").json()
        assert body["count"] == 1
        nid = body["results"][0]["id"]
        assert api_client.post(f"{V1}/notifications/{nid}/read/").json()["is_read"] is True
        assert api_client.get(f"{V1}/notifications/unread_count/").json()["unread"] == 0

    def test_dashboard_summary(self, api_client, manager, job):
        api_client.force_authenticate(manager)
        body = api_client.get(f"{V1}/dashboard/summary/?range=7").json()
        assert body["stats"]["total_jobs"] == 1
        assert body["days"] == 7

    def test_reports_endpoint_permissions(self, api_client, staff_user, viewer_user, manager, job):
        api_client.force_authenticate(staff_user)
        assert api_client.get(f"{V1}/reports/status/").status_code == 403
        api_client.force_authenticate(viewer_user)
        assert api_client.get(f"{V1}/reports/status/").status_code == 200
        assert api_client.get(f"{V1}/reports/workload/").status_code == 403
        api_client.force_authenticate(manager)
        body = api_client.get(f"{V1}/reports/status/").json()
        open_row = next(r for r in body["data"] if r["key"] == "OPEN")
        assert open_row["count"] == 1

    def test_unknown_report_404(self, api_client, manager):
        api_client.force_authenticate(manager)
        assert api_client.get(f"{V1}/reports/nope/").status_code == 404


class TestThrottling:
    def test_auth_endpoint_throttled(self, api_client, manager, monkeypatch):
        # DRF binds THROTTLE_RATES at import time; patch the class attribute.
        from apps.api.throttling import AuthRateThrottle

        monkeypatch.setattr(AuthRateThrottle, "THROTTLE_RATES", {"auth": "2/min"})
        for _ in range(2):
            api_client.post(f"{V1}/auth/token/", {"email": manager.email, "password": "no"})
        response = api_client.post(
            f"{V1}/auth/token/", {"email": manager.email, "password": "no"}
        )
        assert response.status_code == 429
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "throttled"
