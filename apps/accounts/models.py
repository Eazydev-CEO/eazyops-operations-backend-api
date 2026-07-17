import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.models import Department, TimeStampedModel
from apps.core.rbac import Roles, user_can

from .managers import UserManager


def invitation_token():
    return secrets.token_urlsafe(32)


class User(AbstractBaseUser, PermissionsMixin):
    """EazyOps account. Email is the login identifier; ``role`` drives RBAC."""

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80, blank=True)
    role = models.CharField(max_length=20, choices=Roles.choices, default=Roles.STAFF, db_index=True)

    # Profile
    phone = models.CharField(max_length=32, blank=True)
    department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="members"
    )
    job_title = models.CharField(max_length=120, blank=True)
    photo = models.ImageField(upload_to="avatars/%Y/", blank=True)
    email_notifications = models.BooleanField(
        default=True, help_text="Receive email copies of important notifications."
    )

    # Lifecycle — accounts are deactivated, never deleted, so audit history survives.
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="Access to the emergency Django admin.")
    date_joined = models.DateTimeField(default=timezone.now)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["first_name", "last_name", "email"]

    def __str__(self):
        return self.get_full_name() or self.email

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name or self.email.split("@")[0]

    @property
    def display_name(self):
        return self.get_full_name() or self.email

    def can(self, capability):
        return user_can(self, capability)

    @property
    def is_super_admin(self):
        return self.role == Roles.SUPER_ADMIN

    @property
    def is_manager(self):
        return self.role == Roles.MANAGER

    @property
    def is_operations_staff(self):
        return self.role == Roles.STAFF

    @property
    def is_viewer(self):
        return self.role == Roles.VIEWER


class Invitation(TimeStampedModel):
    """Admin-created invitation; registration is impossible without one."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACCEPTED = "ACCEPTED", "Accepted"
        EXPIRED = "EXPIRED", "Expired"
        CANCELLED = "CANCELLED", "Cancelled"

    email = models.EmailField(db_index=True)
    role = models.CharField(max_length=20, choices=Roles.choices, default=Roles.STAFF)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL)
    job_title = models.CharField(max_length=120, blank=True)
    token = models.CharField(max_length=64, unique=True, default=invitation_token)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="sent_invitations"
    )
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="invitation"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invitation<{self.email} as {self.role}>"

    def save(self, *args, **kwargs):
        self.email = (self.email or "").lower()
        if not self.expires_at:
            days = getattr(settings, "INVITATION_EXPIRY_DAYS", 7)
            self.expires_at = timezone.now() + timedelta(days=days)
        super().save(*args, **kwargs)

    @property
    def status(self):
        if self.accepted_at:
            return self.Status.ACCEPTED
        if self.cancelled_at:
            return self.Status.CANCELLED
        if timezone.now() >= self.expires_at:
            return self.Status.EXPIRED
        return self.Status.PENDING

    @property
    def is_usable(self):
        return self.status == self.Status.PENDING

    def get_accept_url(self):
        return f"{settings.SITE_URL}/accounts/invitations/accept/{self.token}/"


class FailedLoginAttempt(models.Model):
    """Rolling record backing the login lockout policy (and security reports)."""

    email = models.CharField(max_length=254, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email", "created_at"]),
            models.Index(fields=["ip_address", "created_at"]),
        ]

    def __str__(self):
        return f"FailedLogin<{self.email} @ {self.created_at:%Y-%m-%d %H:%M}>"
