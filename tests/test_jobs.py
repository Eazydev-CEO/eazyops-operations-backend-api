"""Jobs: numbering, status machine, permission boundaries, uploads, export."""
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.audits.models import Actions, AuditLog
from apps.core.services import set_setting
from apps.notifications.models import Notification, NotificationTypes
from apps.operations import services
from apps.operations.models import Job

from .conftest import make_job

pytestmark = pytest.mark.django_db

def _png_bytes():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buf, format="PNG")
    return buf.getvalue()


PNG_BYTES = _png_bytes()


class TestJobNumbers:
    def test_format(self, job):
        year = timezone.now().year
        assert job.number.startswith(f"JOB-{year}-")
        assert len(job.number.split("-")[-1]) == 5

    def test_sequential_and_unique(self, manager, dept_a, category):
        jobs = [make_job(manager, dept_a, category, title=f"Job {i}") for i in range(5)]
        numbers = [j.number for j in jobs]
        assert len(set(numbers)) == 5
        suffixes = [int(n.split("-")[-1]) for n in numbers]
        assert suffixes == sorted(suffixes)


class TestStatusMachine:
    def test_full_happy_path(self, manager, staff_user, dept_a, category):
        job = make_job(manager, dept_a, category, status=Job.Status.DRAFT)
        services.transition_job(actor=manager, job=job, new_status=Job.Status.OPEN)
        services.assign_staff(actor=manager, job=job, staff_users=[staff_user])
        job.refresh_from_db()
        assert job.status == Job.Status.ASSIGNED
        services.transition_job(actor=staff_user, job=job, new_status=Job.Status.IN_PROGRESS)
        services.transition_job(actor=staff_user, job=job, new_status=Job.Status.AWAITING_REVIEW)
        job.refresh_from_db()
        assert job.submitted_for_review_at is not None
        services.transition_job(actor=manager, job=job, new_status=Job.Status.COMPLETED, note="Good")
        job.refresh_from_db()
        assert job.status == Job.Status.COMPLETED
        assert job.completed_at is not None
        assert job.reviewed_by == manager

    @pytest.mark.parametrize("target", [Job.Status.COMPLETED, Job.Status.AWAITING_REVIEW,
                                        Job.Status.IN_PROGRESS])
    def test_invalid_transitions_from_open_rejected(self, job, manager, target):
        with pytest.raises(ValidationError):
            services.transition_job(actor=manager, job=job, new_status=target)

    def test_cannot_transition_to_same_status(self, job, manager):
        with pytest.raises(ValidationError):
            services.transition_job(actor=manager, job=job, new_status=Job.Status.OPEN)

    def test_overdue_is_derived_not_settable(self, assigned_job, manager):
        with pytest.raises(ValidationError):
            services.transition_job(actor=manager, job=assigned_job, new_status=Job.Status.OVERDUE)

    def test_assigned_requires_assignment_action(self, job, manager):
        with pytest.raises(ValidationError):
            services.transition_job(actor=manager, job=job, new_status=Job.Status.ASSIGNED)

    def test_terminal_statuses_frozen(self, manager, staff_user, dept_a, category):
        job = make_job(manager, dept_a, category)
        services.transition_job(actor=manager, job=job, new_status=Job.Status.CANCELLED)
        with pytest.raises(ValidationError):
            services.transition_job(actor=manager, job=job, new_status=Job.Status.OPEN)

    def test_reject_flow_notifies_staff(self, assigned_job, manager, staff_user):
        services.transition_job(actor=staff_user, job=assigned_job, new_status=Job.Status.IN_PROGRESS)
        services.transition_job(actor=staff_user, job=assigned_job, new_status=Job.Status.AWAITING_REVIEW)
        services.transition_job(
            actor=manager, job=assigned_job, new_status=Job.Status.IN_PROGRESS, note="Redo section 2"
        )
        assigned_job.refresh_from_db()
        assert assigned_job.review_note == "Redo section 2"
        assert Notification.objects.filter(
            user=staff_user, type=NotificationTypes.JOB_REJECTED
        ).exists()

    def test_overdue_derivation(self, manager, staff_user, dept_a, category):
        job = make_job(manager, dept_a, category,
                       due_date=timezone.localdate() - timezone.timedelta(days=2))
        assert job.is_overdue
        assert job.effective_status == Job.Status.OVERDUE
        assert Job.objects.overdue().filter(pk=job.pk).exists()

    def test_every_transition_audited(self, manager, dept_a, category):
        job = make_job(manager, dept_a, category, status=Job.Status.DRAFT)
        services.transition_job(actor=manager, job=job, new_status=Job.Status.OPEN)
        entries = AuditLog.objects.filter(
            action=Actions.JOB_STATUS_CHANGED, object_id=str(job.pk)
        )
        assert entries.count() == 1
        assert entries.first().changes["status"] == ["DRAFT", "OPEN"]


class TestPermissionBoundaries:
    def test_staff_cannot_transition_unassigned_job(self, job, staff_user, manager, staff_user2):
        services.assign_staff(actor=manager, job=job, staff_users=[staff_user2])
        job.refresh_from_db()
        with pytest.raises(PermissionDenied):
            services.transition_job(actor=staff_user, job=job, new_status=Job.Status.IN_PROGRESS)

    def test_staff_cannot_approve_own_work(self, assigned_job, staff_user):
        services.transition_job(actor=staff_user, job=assigned_job, new_status=Job.Status.IN_PROGRESS)
        services.transition_job(actor=staff_user, job=assigned_job, new_status=Job.Status.AWAITING_REVIEW)
        with pytest.raises(PermissionDenied):
            services.transition_job(actor=staff_user, job=assigned_job, new_status=Job.Status.COMPLETED)

    def test_staff_cannot_cancel(self, assigned_job, staff_user):
        with pytest.raises(PermissionDenied):
            services.transition_job(actor=staff_user, job=assigned_job, new_status=Job.Status.CANCELLED)

    def test_viewer_sees_only_own_department_jobs(self, job, viewer_user, viewer_other_dept):
        visible_own = Job.objects.visible_to(viewer_user)
        visible_other = Job.objects.visible_to(viewer_other_dept)
        assert job in visible_own
        assert job not in visible_other

    def test_viewer_cannot_see_drafts(self, manager, dept_a, category, viewer_user):
        draft = make_job(manager, dept_a, category, status=Job.Status.DRAFT)
        assert draft not in Job.objects.visible_to(viewer_user)

    def test_staff_sees_only_assigned(self, job, staff_user):
        assert job not in Job.objects.visible_to(staff_user)

    def test_idor_on_detail_returns_404(self, web_client, job, viewer_other_dept):
        client = web_client.login_as(viewer_other_dept)
        assert client.get(f"/jobs/{job.pk}/").status_code == 404

    def test_delete_requires_super_admin_and_audits_attempt(self, job, manager):
        with pytest.raises(PermissionDenied):
            services.delete_job(actor=manager, job=job)
        assert AuditLog.objects.filter(action=Actions.JOB_DELETE_ATTEMPTED).exists()
        assert Job.objects.filter(pk=job.pk).exists()

    def test_super_admin_delete_audited(self, job, admin_user):
        services.delete_job(actor=admin_user, job=job)
        assert not Job.objects.filter(pk=job.pk).exists()
        assert AuditLog.objects.filter(action=Actions.JOB_DELETED).exists()

    def test_manager_cannot_edit_completed_job(self, manager, staff_user, dept_a, category):
        job = make_job(manager, dept_a, category)
        services.assign_staff(actor=manager, job=job, staff_users=[staff_user])
        services.transition_job(actor=staff_user, job=job, new_status=Job.Status.IN_PROGRESS)
        services.transition_job(actor=staff_user, job=job, new_status=Job.Status.AWAITING_REVIEW)
        services.transition_job(actor=manager, job=job, new_status=Job.Status.COMPLETED)
        assert services.can_edit_job(manager, job) is False


class TestAssignment:
    def test_assignment_notifies_and_flips_status(self, job, manager, staff_user):
        services.assign_staff(actor=manager, job=job, staff_users=[staff_user])
        job.refresh_from_db()
        assert job.status == Job.Status.ASSIGNED
        assert Notification.objects.filter(
            user=staff_user, type=NotificationTypes.JOB_ASSIGNED
        ).exists()

    def test_replace_set_notifies_removed(self, job, manager, staff_user, staff_user2):
        services.assign_staff(actor=manager, job=job, staff_users=[staff_user])
        services.assign_staff(actor=manager, job=job, staff_users=[staff_user2])
        assert Notification.objects.filter(
            user=staff_user, type=NotificationTypes.JOB_REASSIGNED
        ).exists()
        assert set(job.assignments.values_list("staff__email", flat=True)) == {staff_user2.email}

    def test_unassign_all_reopens(self, assigned_job, manager):
        services.assign_staff(actor=manager, job=assigned_job, staff_users=[])
        assigned_job.refresh_from_db()
        assert assigned_job.status == Job.Status.OPEN

    def test_only_active_staff_assignable(self, job, manager, viewer_user):
        with pytest.raises(ValidationError):
            services.assign_staff(actor=manager, job=job, staff_users=[viewer_user])

    def test_staff_cannot_assign(self, job, staff_user):
        with pytest.raises(PermissionDenied):
            services.assign_staff(actor=staff_user, job=job, staff_users=[staff_user])

    def test_cannot_reassign_terminal_job(self, job, manager, staff_user):
        services.transition_job(actor=manager, job=job, new_status=Job.Status.CANCELLED)
        with pytest.raises(ValidationError):
            services.assign_staff(actor=manager, job=job, staff_users=[staff_user])


class TestCommentsAndAttachments:
    def test_assigned_staff_can_comment(self, assigned_job, staff_user):
        comment = services.add_comment(actor=staff_user, job=assigned_job, body="On my way")
        assert comment.author == staff_user
        assert AuditLog.objects.filter(action=Actions.JOB_COMMENTED).exists()

    def test_unassigned_staff_cannot_comment(self, job, staff_user):
        with pytest.raises(PermissionDenied):
            services.add_comment(actor=staff_user, job=job, body="hi")

    def test_empty_comment_rejected(self, assigned_job, staff_user):
        with pytest.raises(ValidationError):
            services.add_comment(actor=staff_user, job=assigned_job, body="   ")

    def test_valid_image_upload(self, assigned_job, staff_user):
        upload = SimpleUploadedFile("evidence.png", PNG_BYTES, content_type="image/png")
        attachment = services.add_attachment(actor=staff_user, job=assigned_job, uploaded_file=upload)
        assert attachment.original_name == "evidence.png"
        assert attachment.size == len(PNG_BYTES)
        assert AuditLog.objects.filter(action=Actions.JOB_ATTACHMENT_ADDED).exists()

    def test_disallowed_extension_rejected(self, assigned_job, staff_user):
        upload = SimpleUploadedFile("malware.exe", b"MZ\x90\x00", content_type="application/octet-stream")
        with pytest.raises(ValidationError):
            services.add_attachment(actor=staff_user, job=assigned_job, uploaded_file=upload)

    def test_oversized_upload_rejected(self, assigned_job, staff_user):
        set_setting("max_upload_mb", "0")
        upload = SimpleUploadedFile("photo.png", PNG_BYTES, content_type="image/png")
        with pytest.raises(ValidationError):
            services.add_attachment(actor=staff_user, job=assigned_job, uploaded_file=upload)

    def test_fake_image_rejected(self, assigned_job, staff_user):
        upload = SimpleUploadedFile("fake.png", b"this is not a png", content_type="image/png")
        with pytest.raises(ValidationError):
            services.add_attachment(actor=staff_user, job=assigned_job, uploaded_file=upload)

    def test_filename_sanitised(self, assigned_job, staff_user):
        upload = SimpleUploadedFile("..\\..\\evil name.png", PNG_BYTES, content_type="image/png")
        attachment = services.add_attachment(actor=staff_user, job=assigned_job, uploaded_file=upload)
        assert ".." not in attachment.original_name
        assert "/" not in attachment.original_name and "\\" not in attachment.original_name

    def test_download_denied_across_departments(self, web_client, assigned_job, staff_user,
                                                viewer_other_dept):
        upload = SimpleUploadedFile("doc.png", PNG_BYTES, content_type="image/png")
        attachment = services.add_attachment(actor=staff_user, job=assigned_job, uploaded_file=upload)
        client = web_client.login_as(viewer_other_dept)
        assert client.get(f"/jobs/attachments/{attachment.pk}/download/").status_code == 404

    def test_download_allowed_same_department_viewer(self, web_client, assigned_job, staff_user,
                                                     viewer_user):
        upload = SimpleUploadedFile("doc.png", PNG_BYTES, content_type="image/png")
        attachment = services.add_attachment(actor=staff_user, job=assigned_job, uploaded_file=upload)
        client = web_client.login_as(viewer_user)
        assert client.get(f"/jobs/attachments/{attachment.pk}/download/").status_code == 200


class TestListFiltersAndExport:
    @pytest.fixture
    def corpus(self, manager, staff_user, dept_a, dept_b, category):
        low = make_job(manager, dept_a, category, title="Alpha low", priority=Job.Priority.LOW)
        urgent = make_job(manager, dept_b, category, title="Beta urgent",
                          priority=Job.Priority.URGENT,
                          due_date=timezone.localdate() - timezone.timedelta(days=1))
        assigned = make_job(manager, dept_a, category, title="Gamma assigned")
        services.assign_staff(actor=manager, job=assigned, staff_users=[staff_user])
        return low, urgent, assigned

    def test_filter_by_status_overdue(self, web_client, manager, corpus):
        client = web_client.login_as(manager)
        response = client.get("/jobs/?status=OVERDUE")
        content = response.content.decode()
        assert "Beta urgent" in content
        assert "Alpha low" not in content

    def test_filter_by_priority_and_search(self, web_client, manager, corpus):
        client = web_client.login_as(manager)
        content = client.get("/jobs/?priority=URGENT").content.decode()
        assert "Beta urgent" in content and "Alpha low" not in content
        content = client.get("/jobs/?q=Gamma").content.decode()
        assert "Gamma assigned" in content and "Beta urgent" not in content

    def test_filter_by_date_range(self, web_client, manager, corpus):
        client = web_client.login_as(manager)
        tomorrow = (timezone.localdate() + timezone.timedelta(days=1)).isoformat()
        content = client.get(f"/jobs/?created_from={tomorrow}").content.decode()
        assert "Alpha low" not in content

    def test_pagination(self, web_client, manager, dept_a, category):
        for i in range(25):
            make_job(manager, dept_a, category, title=f"Bulk {i}")
        client = web_client.login_as(manager)
        response = client.get("/jobs/")
        assert response.context["page_obj"].paginator.num_pages == 2

    def test_csv_export_content_and_audit(self, web_client, manager, corpus):
        client = web_client.login_as(manager)
        response = client.get("/jobs/export/?status=OVERDUE")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        body = response.content.decode()
        assert "Beta urgent" in body and "Alpha low" not in body
        assert AuditLog.objects.filter(action=Actions.EXPORT_GENERATED).exists()

    def test_csv_export_denied_for_staff(self, web_client, staff_user, corpus):
        client = web_client.login_as(staff_user)
        assert client.get("/jobs/export/").status_code == 403

    def test_job_update_audits_changes(self, job, manager):
        services.update_job(actor=manager, job=job, data={"title": "Renamed job"})
        entry = AuditLog.objects.filter(action=Actions.JOB_UPDATED, object_id=str(job.pk)).first()
        assert entry.changes["title"] == ["Fix the pump", "Renamed job"]
