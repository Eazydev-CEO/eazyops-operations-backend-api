"""Auth activity lands in the audit trail automatically."""
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .services import Actions, record_audit


@receiver(user_logged_in)
def audit_login(sender, request, user, **kwargs):
    record_audit(actor=user, action=Actions.LOGIN, instance=user, request=request)


@receiver(user_logged_out)
def audit_logout(sender, request, user, **kwargs):
    if user is not None and getattr(user, "pk", None):
        record_audit(actor=user, action=Actions.LOGOUT, instance=user, request=request)


@receiver(user_login_failed)
def audit_login_failed(sender, credentials, request=None, **kwargs):
    email = (credentials or {}).get("username") or (credentials or {}).get("email") or ""
    record_audit(
        actor=None,
        action=Actions.LOGIN_FAILED,
        object_type="accounts.user",
        object_repr=email[:200],
        metadata={"email": email},
        request=request,
    )
