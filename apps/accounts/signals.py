"""Lockout bookkeeping wired to Django's auth signals.

Audit records for login/logout/failure are written by ``apps.audits.signals``;
this module only maintains the FailedLoginAttempt counters the lockout
policy reads.
"""
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver

from . import services


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    email = (credentials or {}).get("username") or (credentials or {}).get("email") or ""
    services.record_failed_login(email, request=request)


@receiver(user_logged_in)
def on_login_succeeded(sender, request, user, **kwargs):
    services.clear_failed_logins(user.email)
