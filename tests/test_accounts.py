"""Accounts: login, lockout, invitations, lifecycle, activity tracking."""
import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import FailedLoginAttempt, Invitation, User
from apps.audits.models import Actions, AuditLog
from apps.core.rbac import Roles

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Login / lockout
# ---------------------------------------------------------------------------
class TestLogin:
    def test_login_page_renders(self, client):
        response = client.get("/accounts/login/")
        assert response.status_code == 200
        assert b"Sign in" in response.content

    def test_successful_login_redirects_and_audits(self, client, manager):
        response = client.post(
            "/accounts/login/",
            {"username": manager.email, "password": PASSWORD},
        )
        assert response.status_code == 302
        assert AuditLog.objects.filter(action=Actions.LOGIN, actor=manager).exists()

    def test_failed_login_recorded(self, client, manager):
        client.post("/accounts/login/", {"username": manager.email, "password": "wrong"})
        assert FailedLoginAttempt.objects.filter(email=manager.email).count() == 1
        assert AuditLog.objects.filter(action=Actions.LOGIN_FAILED).exists()

    def test_lockout_after_max_failures(self, client, manager, settings):
        settings.LOGIN_MAX_FAILURES = 3
        for _ in range(3):
            client.post("/accounts/login/", {"username": manager.email, "password": "wrong"})
        response = client.post(
            "/accounts/login/", {"username": manager.email, "password": PASSWORD}
        )
        assert response.status_code == 200  # form re-rendered with error
        assert b"Too many failed attempts" in response.content
        # still locked even with the right password
        assert not response.wsgi_request.user.is_authenticated

    def test_successful_login_clears_failures(self, client, manager):
        client.post("/accounts/login/", {"username": manager.email, "password": "wrong"})
        client.post("/accounts/login/", {"username": manager.email, "password": PASSWORD})
        assert FailedLoginAttempt.objects.filter(email=manager.email).count() == 0

    def test_inactive_user_cannot_login(self, client, manager):
        manager.is_active = False
        manager.save()
        response = client.post(
            "/accounts/login/", {"username": manager.email, "password": PASSWORD}
        )
        assert response.status_code == 200
        assert not response.wsgi_request.user.is_authenticated

    def test_last_activity_updated(self, web_client, manager):
        client = web_client.login_as(manager)
        client.get("/")
        manager.refresh_from_db()
        assert manager.last_activity_at is not None


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------
class TestInvitations:
    def test_create_invitation_sends_email_and_audits(self, admin_user, dept_a):
        invitation = services.create_invitation(
            inviter=admin_user, email="new@test.local", role=Roles.STAFF, department=dept_a
        )
        assert invitation.is_usable
        assert len(mail.outbox) == 1
        assert invitation.get_accept_url() in mail.outbox[0].body
        assert AuditLog.objects.filter(action=Actions.INVITATION_SENT).exists()

    def test_duplicate_pending_invitation_rejected(self, admin_user):
        services.create_invitation(inviter=admin_user, email="dup@test.local", role=Roles.STAFF)
        with pytest.raises(ValidationError):
            services.create_invitation(inviter=admin_user, email="dup@test.local", role=Roles.STAFF)

    def test_existing_user_email_rejected(self, admin_user, manager):
        with pytest.raises(ValidationError):
            services.create_invitation(inviter=admin_user, email=manager.email, role=Roles.STAFF)

    def test_manager_cannot_invite_super_admin(self, manager):
        with pytest.raises(ValidationError):
            services.create_invitation(
                inviter=manager, email="boss@test.local", role=Roles.SUPER_ADMIN
            )

    def test_accept_invitation_creates_user(self, admin_user, dept_a, client):
        invitation = services.create_invitation(
            inviter=admin_user, email="joiner@test.local", role=Roles.STAFF,
            department=dept_a, job_title="Technician",
        )
        response = client.post(
            f"/accounts/invitations/accept/{invitation.token}/",
            {"first_name": "Jo", "last_name": "Iner", "phone": "",
             "password1": "N3w!SecurePass", "password2": "N3w!SecurePass"},
        )
        assert response.status_code == 302
        user = User.objects.get(email="joiner@test.local")
        assert user.role == Roles.STAFF
        assert user.department == dept_a
        invitation.refresh_from_db()
        assert invitation.status == Invitation.Status.ACCEPTED
        assert invitation.created_user == user
        assert AuditLog.objects.filter(action=Actions.INVITATION_ACCEPTED).exists()

    def test_expired_invitation_shows_invalid_page(self, admin_user, client):
        invitation = services.create_invitation(
            inviter=admin_user, email="late@test.local", role=Roles.STAFF
        )
        Invitation.objects.filter(pk=invitation.pk).update(
            expires_at=timezone.now() - timezone.timedelta(hours=1)
        )
        response = client.get(f"/accounts/invitations/accept/{invitation.token}/")
        assert response.status_code == 410

    def test_cancelled_invitation_unusable(self, admin_user, client):
        invitation = services.create_invitation(
            inviter=admin_user, email="gone@test.local", role=Roles.STAFF
        )
        services.cancel_invitation(invitation=invitation, actor=admin_user)
        response = client.get(f"/accounts/invitations/accept/{invitation.token}/")
        assert response.status_code == 410

    def test_registration_requires_invitation(self, client):
        # no self-service signup route exists at all
        assert client.get("/accounts/register/").status_code == 404
        assert client.get("/accounts/signup/").status_code == 404

    def test_invite_view_requires_permission(self, web_client, manager):
        client = web_client.login_as(manager)  # manager lacks users.manage
        assert client.get("/accounts/manage/invitations/new/").status_code == 403


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
class TestUserLifecycle:
    def test_deactivate_preserves_audit_history(self, admin_user, staff_user):
        services.set_user_active(actor=admin_user, user=staff_user, active=False)
        staff_user.refresh_from_db()
        assert staff_user.is_active is False
        entry = AuditLog.objects.filter(action=Actions.USER_DEACTIVATED).first()
        assert entry is not None and entry.object_id == str(staff_user.pk)

    def test_cannot_deactivate_self(self, admin_user):
        with pytest.raises(ValidationError):
            services.set_user_active(actor=admin_user, user=admin_user, active=False)

    def test_cannot_deactivate_last_super_admin(self, admin_user, manager):
        with pytest.raises(ValidationError):
            services.set_user_active(actor=manager, user=admin_user, active=False)

    def test_role_change_audited_with_before_after(self, admin_user, staff_user):
        services.update_user_management_fields(
            actor=admin_user, user=staff_user, cleaned={"role": Roles.MANAGER}
        )
        entry = AuditLog.objects.filter(action=Actions.USER_ROLE_CHANGED).first()
        assert entry.changes["role"] == [Roles.STAFF.value, Roles.MANAGER.value]

    def test_cannot_change_own_role(self, admin_user):
        with pytest.raises(ValidationError):
            services.update_user_management_fields(
                actor=admin_user, user=admin_user, cleaned={"role": Roles.MANAGER}
            )
