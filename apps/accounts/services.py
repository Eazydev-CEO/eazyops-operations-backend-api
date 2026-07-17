"""Account workflows: lockout policy, invitations, lifecycle changes.

All mutations here write audit records; callers pass ``request`` when
available so IP/user-agent land in the trail.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from apps.audits.services import Actions, record_audit
from apps.core.rbac import Roles
from apps.core.services import get_setting_int
from apps.core.utils import client_meta

from .models import FailedLoginAttempt, Invitation, User

logger = logging.getLogger("eazyops.accounts")


# ---------------------------------------------------------------------------
# Login throttling
# ---------------------------------------------------------------------------
def _lockout_policy():
    max_failures = get_setting_int("login_max_failures", settings.LOGIN_MAX_FAILURES)
    window = get_setting_int("login_lockout_minutes", settings.LOGIN_LOCKOUT_MINUTES)
    return max_failures, timedelta(minutes=window)


def is_locked_out(email=None, ip_address=None):
    """True when either the account or the source IP exceeded the failure cap."""
    max_failures, window = _lockout_policy()
    since = timezone.now() - window
    if email:
        count = FailedLoginAttempt.objects.filter(
            email=email.lower(), created_at__gte=since
        ).count()
        if count >= max_failures:
            return True
    if ip_address:
        # IPs get 3x headroom so a shared office NAT doesn't lock everyone
        # out because of one user, while still stopping spray attacks.
        count = FailedLoginAttempt.objects.filter(
            ip_address=ip_address, created_at__gte=since
        ).count()
        if count >= max_failures * 3:
            return True
    return False


def record_failed_login(email, request=None):
    ip, agent = client_meta(request)
    FailedLoginAttempt.objects.create(
        email=(email or "").lower(), ip_address=ip, user_agent=agent[:300]
    )


def clear_failed_logins(email):
    FailedLoginAttempt.objects.filter(email=(email or "").lower()).delete()


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------
@transaction.atomic
def create_invitation(*, inviter, email, role, department=None, job_title="", request=None):
    email = email.lower().strip()
    if User.objects.filter(email=email).exists():
        raise ValidationError("A user with this email already exists.")
    existing = Invitation.objects.filter(email=email, accepted_at__isnull=True, cancelled_at__isnull=True)
    for inv in existing:
        if inv.is_usable:
            raise ValidationError("A pending invitation for this email already exists.")
    if role == Roles.SUPER_ADMIN and not inviter.is_super_admin:
        raise ValidationError("Only a Super Admin can invite another Super Admin.")

    days = get_setting_int("invitation_expiry_days", settings.INVITATION_EXPIRY_DAYS)
    invitation = Invitation.objects.create(
        email=email,
        role=role,
        department=department,
        job_title=job_title,
        invited_by=inviter,
        expires_at=timezone.now() + timedelta(days=days),
    )
    record_audit(
        actor=inviter, action=Actions.INVITATION_SENT, instance=invitation,
        metadata={"email": email, "role": role}, request=request,
    )
    send_invitation_email(invitation)
    return invitation


def send_invitation_email(invitation):
    subject = "You have been invited to EazyOps"
    inviter = invitation.invited_by.display_name if invitation.invited_by else "The operations team"
    body = (
        f"Hello,\n\n"
        f"{inviter} invited you to join {settings.SITE_NAME} as "
        f"{Roles(invitation.role).label}.\n\n"
        f"Create your account here (link expires {invitation.expires_at:%Y-%m-%d %H:%M} UTC):\n"
        f"{invitation.get_accept_url()}\n\n"
        f"If you were not expecting this invitation you can ignore this email."
    )
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [invitation.email])
    except Exception:  # pragma: no cover - SMTP problems must never break the flow
        logger.exception("Failed to send invitation email to %s", invitation.email)


@transaction.atomic
def accept_invitation(*, invitation, first_name, last_name, password, phone="", request=None):
    if not invitation.is_usable:
        raise ValidationError("This invitation is no longer valid.")
    if User.objects.filter(email=invitation.email).exists():
        raise ValidationError("An account with this email already exists.")

    user = User.objects.create_user(
        email=invitation.email,
        password=password,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        phone=phone.strip(),
        role=invitation.role,
        department=invitation.department,
        job_title=invitation.job_title,
    )
    invitation.accepted_at = timezone.now()
    invitation.created_user = user
    invitation.save(update_fields=["accepted_at", "created_user", "updated_at"])

    record_audit(
        actor=user, action=Actions.INVITATION_ACCEPTED, instance=invitation,
        metadata={"email": user.email}, request=request,
    )
    record_audit(
        actor=invitation.invited_by, action=Actions.USER_CREATED, instance=user,
        metadata={"via": "invitation", "role": user.role}, request=request,
    )

    from apps.notifications.services import notify, NotificationTypes

    if invitation.invited_by and invitation.invited_by.is_active:
        notify(
            invitation.invited_by,
            NotificationTypes.INVITATION_ACCEPTED,
            title="Invitation accepted",
            message=f"{user.display_name} ({user.email}) joined as {Roles(user.role).label}.",
            url="/accounts/manage/users/",
        )
    notify(
        user,
        NotificationTypes.WELCOME,
        title=f"Welcome to {settings.SITE_NAME}",
        message="Your account is ready. Review your profile and check your assigned work.",
        url="/accounts/profile/",
        send_email=False,
    )
    return user


def resend_invitation(*, invitation, actor, request=None):
    if invitation.accepted_at or invitation.cancelled_at:
        raise ValidationError("Only pending invitations can be resent.")
    days = get_setting_int("invitation_expiry_days", settings.INVITATION_EXPIRY_DAYS)
    invitation.expires_at = timezone.now() + timedelta(days=days)
    invitation.save(update_fields=["expires_at", "updated_at"])
    send_invitation_email(invitation)
    record_audit(
        actor=actor, action=Actions.INVITATION_SENT, instance=invitation,
        metadata={"email": invitation.email, "resend": True}, request=request,
    )
    return invitation


def cancel_invitation(*, invitation, actor, request=None):
    if invitation.accepted_at:
        raise ValidationError("Accepted invitations cannot be cancelled.")
    invitation.cancelled_at = timezone.now()
    invitation.save(update_fields=["cancelled_at", "updated_at"])
    record_audit(
        actor=actor, action=Actions.INVITATION_CANCELLED, instance=invitation,
        metadata={"email": invitation.email}, request=request,
    )


# ---------------------------------------------------------------------------
# User lifecycle
# ---------------------------------------------------------------------------
def set_user_active(*, actor, user, active, request=None):
    """Activate/deactivate without deleting anything (audit history survives)."""
    if user.pk == actor.pk:
        raise ValidationError("You cannot change the active state of your own account.")
    if not active and user.is_super_admin:
        remaining = User.objects.filter(
            role=Roles.SUPER_ADMIN, is_active=True
        ).exclude(pk=user.pk).count()
        if remaining == 0:
            raise ValidationError("At least one active Super Admin must remain.")
    if user.is_active == active:
        return user
    user.is_active = active
    user.save(update_fields=["is_active"])
    record_audit(
        actor=actor,
        action=Actions.USER_ACTIVATED if active else Actions.USER_DEACTIVATED,
        instance=user,
        changes={"is_active": [not active, active]},
        request=request,
    )
    return user


def update_user_management_fields(*, actor, user, cleaned, request=None):
    """Apply role/department/title/phone edits from the admin area with audit."""
    tracked = ["role", "department", "job_title", "phone", "first_name", "last_name"]
    changes = {}
    for field in tracked:
        if field not in cleaned:
            continue
        old = getattr(user, field)
        new = cleaned[field]
        if old != new:
            display = lambda v: getattr(v, "name", v)  # departments -> name
            changes[field] = [display(old), display(new)]
            setattr(user, field, new)

    if "role" in changes:
        if user.pk == actor.pk:
            raise ValidationError("You cannot change your own role.")
        old_role = changes["role"][0]
        if old_role == Roles.SUPER_ADMIN:
            remaining = User.objects.filter(
                role=Roles.SUPER_ADMIN, is_active=True
            ).exclude(pk=user.pk).count()
            if remaining == 0:
                raise ValidationError("At least one active Super Admin must remain.")

    if not changes:
        return user
    user.save()
    action = Actions.USER_ROLE_CHANGED if "role" in changes else Actions.USER_UPDATED
    record_audit(actor=actor, action=action, instance=user, changes=changes, request=request)
    return user
