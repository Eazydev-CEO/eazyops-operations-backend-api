"""DRF permissions bound to the same capability map as the web views."""
from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.core.rbac import user_can


class IsActiveAuthenticated(BasePermission):
    message = "Authentication required."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active)


class HasCapability(BasePermission):
    """Checks ``view.required_capability``.

    Accepts a string, or a dict mapping DRF actions (plus the special keys
    "read"/"write") to capability names. ``None`` values mean
    "authentication only".
    """

    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.is_active):
            return False
        spec = getattr(view, "required_capability", None)
        if spec is None:
            return True
        if isinstance(spec, str):
            return user_can(user, spec)
        action = getattr(view, "action", None)
        capability = spec.get(action, spec.get(
            "read" if request.method in SAFE_METHODS else "write"
        ))
        if capability is None:
            return True
        return user_can(user, capability)
