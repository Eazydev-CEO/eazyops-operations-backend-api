"""
Role-based access control for EazyOps.

Roles are stored on ``accounts.User.role``. Every sensitive view, API
endpoint and service checks a *capability* — a named action mapped to the
set of roles allowed to perform it. Object-level rules (e.g. "staff can
only update jobs assigned to them") live next to the models they protect
(see ``apps.operations.services``) and build on these primitives.

Templates may additionally hide UI, but hiding is never the enforcement.
"""
from django.core.exceptions import PermissionDenied
from django.db import models

# NOTE: keep this module free of django.contrib.auth imports at module
# level — it is imported from accounts.models while the app registry is
# still populating, and auth.forms resolves get_user_model() on import.


class Roles(models.TextChoices):
    SUPER_ADMIN = "SUPER_ADMIN", "Super Admin"
    MANAGER = "MANAGER", "Operations Manager"
    STAFF = "STAFF", "Operations Staff"
    VIEWER = "VIEWER", "Viewer / Client"


ADMIN = {Roles.SUPER_ADMIN}
MANAGEMENT = {Roles.SUPER_ADMIN, Roles.MANAGER}
OPERATIONAL = {Roles.SUPER_ADMIN, Roles.MANAGER, Roles.STAFF}
EVERYONE = {Roles.SUPER_ADMIN, Roles.MANAGER, Roles.STAFF, Roles.VIEWER}

#: capability -> roles allowed. Single source of truth for web + API.
CAPABILITIES = {
    # Accounts / user administration
    "users.view": MANAGEMENT,
    "users.manage": ADMIN,            # invite, edit role, activate/deactivate
    "roles.view": MANAGEMENT,
    # Jobs / work orders
    "jobs.view_all": MANAGEMENT,
    "jobs.create": MANAGEMENT,
    "jobs.edit_any": MANAGEMENT,
    "jobs.delete": ADMIN,
    "jobs.assign": MANAGEMENT,
    "jobs.review": MANAGEMENT,        # approve / reject completed work
    "jobs.export": MANAGEMENT,
    "jobs.update_assigned": OPERATIONAL,  # progress notes, evidence, transitions
    # Configuration
    "categories.manage": MANAGEMENT,
    "departments.manage": ADMIN,
    "settings.manage": ADMIN,
    # Scheduling
    "schedules.view": OPERATIONAL,
    "schedules.manage": MANAGEMENT,
    "schedules.run_monitor": MANAGEMENT,
    # Audit trail
    "audits.view": MANAGEMENT,
    # Reports (viewers get a department-scoped subset, enforced in queries)
    "reports.view": {Roles.SUPER_ADMIN, Roles.MANAGER, Roles.VIEWER},
    "reports.full": MANAGEMENT,
    # System administration
    "system.health": ADMIN,
    "notifications.monitor": ADMIN,
    "api.overview": ADMIN,
}


def role_allows(role, capability):
    allowed = CAPABILITIES.get(capability)
    if allowed is None:
        raise KeyError(f"Unknown capability {capability!r}")
    return role in allowed


def user_can(user, capability):
    """True when an active, authenticated user's role grants the capability."""
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    return role_allows(user.role, capability)


def require_capability(user, capability):
    if not user_can(user, capability):
        raise PermissionDenied(f"Capability {capability!r} required.")


def _redirect_to_login(request):
    from django.conf import settings
    from django.contrib.auth.views import redirect_to_login

    return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)


class CapabilityRequiredMixin:
    """CBV guard: set ``capability = "jobs.create"`` (or a tuple: any-of)."""

    capability = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _redirect_to_login(request)
        caps = self.capability if isinstance(self.capability, (list, tuple)) else [self.capability]
        if not any(user_can(request.user, cap) for cap in caps if cap):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class RoleRequiredMixin:
    """CBV guard: set ``allowed_roles = {Roles.SUPER_ADMIN, ...}``."""

    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _redirect_to_login(request)
        if self.allowed_roles and request.user.role not in self.allowed_roles:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
