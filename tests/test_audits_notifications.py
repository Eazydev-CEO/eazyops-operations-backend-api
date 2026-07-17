"""Audit trail immutability & capture; notification fanout and scoping."""
import pytest
from django.core import mail
from django.test import RequestFactory

from apps.audits.models import Actions, AuditLog
from apps.audits.services import record_audit
from apps.core.services import set_setting
from apps.notifications import services as notif
from apps.notifications.models import Notification, NotificationTypes

pytestmark = pytest.mark.django_db


class TestAuditImmutability:
    def test_update_via_save_rejected(self, manager):
        entry = record_audit(actor=manager, action=Actions.LOGIN, instance=manager)
        entry.action = Actions.LOGOUT
        with pytest.raises(PermissionError):
            entry.save()

    def test_delete_rejected(self, manager):
        entry = record_audit(actor=manager, action=Actions.LOGIN, instance=manager)
        with pytest.raises(PermissionError):
            entry.delete()

    def test_queryset_update_rejected(self, manager):
        record_audit(actor=manager, action=Actions.LOGIN, instance=manager)
        with pytest.raises(PermissionError):
            AuditLog.objects.all().update(action=Actions.LOGOUT)

    def test_queryset_delete_rejected(self, manager):
        record_audit(actor=manager, action=Actions.LOGIN, instance=manager)
        with pytest.raises(PermissionError):
            AuditLog.objects.all().delete()


class TestAuditCapture:
    def test_request_meta_captured(self, manager):
        request = RequestFactory().get(
            "/", HTTP_USER_AGENT="TestAgent/1.0", HTTP_X_FORWARDED_FOR="203.0.113.9, 10.0.0.1"
        )
        entry = record_audit(actor=manager, action=Actions.LOGIN, instance=manager, request=request)
        assert entry.ip_address == "203.0.113.9"
        assert entry.user_agent == "TestAgent/1.0"
        assert entry.actor_email == manager.email

    def test_changes_serialised(self, manager, job):
        entry = record_audit(
            actor=manager, action=Actions.JOB_UPDATED, instance=job,
            changes={"priority": ["LOW", "HIGH"], "category": [None, job.category]},
        )
        assert entry.changes["priority"] == ["LOW", "HIGH"]
        assert entry.changes["category"][1] == str(job.category)

    def test_actor_survives_user_reference(self, manager):
        entry = record_audit(actor=manager, action=Actions.LOGIN, instance=manager)
        email = manager.email
        # even if actor FK were nulled, the email stays denormalised
        assert entry.actor_email == email

    def test_system_actions_allowed_without_actor(self):
        entry = record_audit(action=Actions.SCHEDULER_EXECUTED, object_type="scheduling.run")
        assert entry.actor is None
        assert str(entry)  # renders without crashing

    def test_audit_view_requires_capability(self, web_client, staff_user, manager):
        client = web_client.login_as(staff_user)
        assert client.get("/audits/").status_code == 403

    def test_audit_view_filters(self, web_client, manager):
        record_audit(actor=manager, action=Actions.LOGIN, instance=manager)
        record_audit(actor=manager, action=Actions.JOB_CREATED,
                     object_type="operations.job", object_id="1",
                     object_repr="JOB-FILTER-PROBE")
        client = web_client.login_as(manager)
        response = client.get("/audits/?action=LOGIN")
        content = response.content.decode()
        assert "JOB-FILTER-PROBE" not in content  # filtered out of the table
        actions = {e.action for e in response.context["entries"]}
        assert actions == {Actions.LOGIN}


class TestNotifications:
    def test_notify_multiple_dedupes(self, staff_user):
        created = notif.notify(
            [staff_user, staff_user], NotificationTypes.SYSTEM, title="Hello", message="x"
        )
        assert len(created) == 1

    def test_inactive_users_skipped(self, staff_user):
        staff_user.is_active = False
        staff_user.save()
        assert notif.notify(staff_user, NotificationTypes.SYSTEM, title="Hi") == []

    def test_email_sent_for_emailed_types(self, staff_user):
        notif.notify(staff_user, NotificationTypes.JOB_ASSIGNED, title="Assigned", message="m")
        assert len(mail.outbox) == 1
        record = Notification.objects.get(user=staff_user)
        assert record.email_sent is True

    def test_user_optout_respected(self, staff_user):
        staff_user.email_notifications = False
        staff_user.save()
        notif.notify(staff_user, NotificationTypes.JOB_ASSIGNED, title="Assigned")
        assert len(mail.outbox) == 0
        assert Notification.objects.get(user=staff_user).email_sent is False

    def test_master_switch_disables_email(self, staff_user):
        set_setting("notification_emails_enabled", "false")
        notif.notify(staff_user, NotificationTypes.JOB_ASSIGNED, title="Assigned")
        assert len(mail.outbox) == 0

    def test_in_app_only_types_skip_email(self, staff_user):
        notif.notify(staff_user, NotificationTypes.JOB_DUE_SOON, title="Due soon")
        assert len(mail.outbox) == 0

    def test_mark_read_scoped_to_owner(self, staff_user, manager):
        [n] = notif.notify(staff_user, NotificationTypes.SYSTEM, title="Private")
        assert notif.mark_read(manager, n.pk) is False
        assert notif.mark_read(staff_user, n.pk) is True
        n.refresh_from_db()
        assert n.is_read and n.read_at is not None

    def test_mark_all_read(self, staff_user):
        for i in range(3):
            notif.notify(staff_user, NotificationTypes.SYSTEM, title=f"n{i}")
        assert notif.mark_all_read(staff_user) == 3

    def test_feed_endpoint_returns_own_only(self, web_client, staff_user, manager):
        notif.notify(staff_user, NotificationTypes.SYSTEM, title="mine")
        notif.notify(manager, NotificationTypes.SYSTEM, title="theirs")
        client = web_client.login_as(staff_user)
        data = client.get("/notifications/feed/").json()
        titles = {item["title"] for item in data["items"]}
        assert titles == {"mine"}
        assert data["unread"] == 1

    def test_history_page_scoped(self, web_client, staff_user, manager):
        notif.notify(manager, NotificationTypes.SYSTEM, title="managers-only-item")
        client = web_client.login_as(staff_user)
        assert "managers-only-item" not in client.get("/notifications/").content.decode()

    def test_open_redirect_blocked_on_mark_read(self, web_client, staff_user):
        [n] = notif.notify(staff_user, NotificationTypes.SYSTEM, title="x")
        client = web_client.login_as(staff_user)
        response = client.post(f"/notifications/{n.pk}/read/", {"next": "https://evil.example"})
        assert response.status_code == 302
        assert response["Location"].startswith("/notifications/")
