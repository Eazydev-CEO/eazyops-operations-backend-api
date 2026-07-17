from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Department(TimeStampedModel):
    """An internal department or an external client organisation.

    Jobs are raised for a department; Viewer/Client users are scoped to
    the department they belong to (IDOR boundary for read access).
    """

    class Kind(models.TextChoices):
        INTERNAL = "INTERNAL", "Internal department"
        CLIENT = "CLIENT", "Client / external"

    name = models.CharField(max_length=120, unique=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.INTERNAL)
    description = models.TextField(blank=True)
    contact_email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SystemSetting(TimeStampedModel):
    """Editable runtime settings managed from the custom admin area."""

    key = models.CharField(max_length=100, unique=True)
    value = models.CharField(max_length=500)
    description = models.CharField(max_length=255, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return f"{self.key}={self.value}"
