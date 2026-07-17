"""Report correctness plus platform security posture."""
import pytest
from django.utils import timezone

from apps.operations import services as job_services
from apps.operations.models import Job
from apps.reports import services as reports
from apps.scheduling.models import TaskOccurrence

from .conftest import make_job
from .test_scheduling import make_task

pytestmark = pytest.mark.django_db


class TestReportCorrectness:
    def test_jobs_by_status_counts(self, manager, dept_a, category):
        make_job(manager, dept_a, category, title="a")
        make_job(manager, dept_a, category, title="b")
        overdue = make_job(manager, dept_a, category, title="late",
                           due_date=timezone.localdate() - timezone.timedelta(days=3))
        rows = {r["key"]: r["count"] for r in reports.jobs_by_status(Job.objects.all())}
        assert rows["OPEN"] == 3          # stored status
        assert rows["OVERDUE"] == 1       # derived overlay
        assert rows["COMPLETED"] == 0

    def test_staff_performance_math(self, manager, staff_user, dept_a, category):
        job = make_job(manager, dept_a, category,
                       start_date=timezone.localdate() - timezone.timedelta(days=10),
                       due_date=timezone.localdate() + timezone.timedelta(days=5))
        job_services.assign_staff(actor=manager, job=job, staff_users=[staff_user])
        job_services.transition_job(actor=staff_user, job=job, new_status=Job.Status.IN_PROGRESS)
        job_services.transition_job(actor=staff_user, job=job, new_status=Job.Status.AWAITING_REVIEW)
        job_services.transition_job(actor=manager, job=job, new_status=Job.Status.COMPLETED)
        rows = reports.staff_performance()
        row = next(r for r in rows if r["id"] == staff_user.pk)
        assert row["completed"] == 1
        assert row["avg_days"] == 10.0
        assert row["on_time_rate"] == 100

    def test_completion_time_grouping(self, manager, staff_user, dept_a, category):
        job = make_job(manager, dept_a, category,
                       start_date=timezone.localdate() - timezone.timedelta(days=4))
        job_services.assign_staff(actor=manager, job=job, staff_users=[staff_user])
        job_services.transition_job(actor=staff_user, job=job, new_status=Job.Status.IN_PROGRESS)
        job_services.transition_job(actor=staff_user, job=job, new_status=Job.Status.AWAITING_REVIEW)
        job_services.transition_job(actor=manager, job=job, new_status=Job.Status.COMPLETED)
        data = reports.completion_time(Job.objects.all())
        assert data["total_completed"] == 1
        assert data["overall_avg"] == 4.0
        assert data["by_category"][0]["label"] == category.name

    def test_scheduled_task_report_rates(self, manager, staff_user):
        task = make_task(manager, assignees=[staff_user])
        now = timezone.now()
        TaskOccurrence.objects.create(scheduled_task=task, assigned_to=staff_user,
                                      scheduled_for=now, status=TaskOccurrence.Status.COMPLETED)
        TaskOccurrence.objects.create(scheduled_task=task, assigned_to=staff_user,
                                      scheduled_for=now - timezone.timedelta(days=1),
                                      status=TaskOccurrence.Status.MISSED)
        TaskOccurrence.objects.create(scheduled_task=task, assigned_to=staff_user,
                                      scheduled_for=now + timezone.timedelta(days=1))
        [row] = reports.scheduled_task_report()
        assert (row["total"], row["completed"], row["missed"], row["pending"]) == (3, 1, 1, 1)
        assert row["completion_rate"] == 50

    def test_viewer_report_scoped_to_department(self, web_client, manager, viewer_user,
                                                dept_a, dept_b, category):
        make_job(manager, dept_a, category, title="visible")
        make_job(manager, dept_b, category, title="hidden")
        client = web_client.login_as(viewer_user)
        response = client.get("/reports/status/")
        assert response.status_code == 200
        rows = {r["key"]: r["count"] for r in response.context["rows"]}
        assert rows["OPEN"] == 1  # only dept_a job counted

    def test_report_csv_export(self, web_client, manager, dept_a, category):
        make_job(manager, dept_a, category)
        client = web_client.login_as(manager)
        response = client.get("/reports/status/?export=csv")
        assert response["Content-Type"] == "text/csv"
        assert b"Open,1" in response.content


class TestSecurityPosture:
    PROTECTED_PAGES = ["/", "/jobs/", "/schedule/", "/notifications/", "/reports/",
                       "/audits/", "/accounts/manage/users/", "/manage/settings/"]

    def test_anonymous_redirected_to_login(self, client):
        for page in self.PROTECTED_PAGES:
            response = client.get(page)
            assert response.status_code == 302, page
            assert "/accounts/login/" in response["Location"], page

    def test_security_headers_present(self, client):
        response = client.get("/accounts/login/")
        assert "Content-Security-Policy" in response
        assert "default-src 'self'" in response["Content-Security-Policy"]
        assert response["X-Content-Type-Options"] == "nosniff"
        assert response["X-Frame-Options"] == "DENY"
        assert "Permissions-Policy" in response
        assert response["Referrer-Policy"] == "strict-origin-when-cross-origin"

    def test_django_admin_requires_staff(self, web_client, staff_user):
        client = web_client.login_as(staff_user)
        response = client.get("/django-admin/")
        assert response.status_code == 302  # bounced to admin login

    def test_custom_404_page(self, client):
        response = client.get("/definitely/not/a/page/")
        assert response.status_code == 404

    def test_health_endpoint_public_and_minimal(self, client):
        response = client.get("/core/health/")
        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {"status", "time"}

    def test_csrf_enforced_on_posts(self, manager):
        from django.test import Client

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(manager)
        response = csrf_client.post("/notifications/read-all/")
        assert response.status_code == 403

    def test_api_schema_available(self, web_client, manager):
        client = web_client.login_as(manager)
        assert client.get("/api/docs/").status_code == 200
        assert client.get("/api/schema/").status_code == 200

    def test_production_settings_hardened(self):
        """prod settings module enforces secure cookies, HSTS and DEBUG off."""
        import importlib
        import os

        os.environ["SECRET_KEY"] = "a-real-production-secret-for-tests-only"
        try:
            prod = importlib.import_module("config.settings.prod")
            assert prod.DEBUG is False
            assert prod.SESSION_COOKIE_SECURE is True
            assert prod.CSRF_COOKIE_SECURE is True
            assert prod.SECURE_HSTS_SECONDS >= 31536000
            assert prod.SECURE_SSL_REDIRECT is True
        finally:
            os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"

    def test_viewer_cannot_reach_any_admin_area(self, web_client, viewer_user):
        client = web_client.login_as(viewer_user)
        for page in ["/accounts/manage/users/", "/accounts/manage/invitations/",
                     "/jobs/manage/categories/", "/jobs/manage/departments/",
                     "/manage/settings/", "/manage/health/", "/manage/api/",
                     "/schedule/runs/", "/audits/"]:
            assert client.get(page).status_code == 403, page
