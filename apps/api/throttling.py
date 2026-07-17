from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle


class AuthRateThrottle(SimpleRateThrottle):
    """Per-IP throttle for credential endpoints (brute-force guard)."""

    scope = "auth"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class AuthUserRateThrottle(SimpleRateThrottle):
    """Per-IP throttle for token refresh/verify."""

    scope = "auth-user"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class SensitiveScopedThrottle(ScopedRateThrottle):
    """Used by audit-log, user-admin and export endpoints (scope: sensitive)."""
