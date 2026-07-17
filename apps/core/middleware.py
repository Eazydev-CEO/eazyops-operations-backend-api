"""Security-header middleware complementing django.middleware.security."""
from django.conf import settings


class SecurityHeadersMiddleware:
    """Emit CSP and hardening headers on every response.

    HSTS, nosniff, referrer-policy and frame denial are handled by Django's
    SecurityMiddleware / settings; this adds the rest.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.csp = getattr(settings, "CSP_POLICY", "")

    def __call__(self, request):
        response = self.get_response(request)
        if self.csp and "Content-Security-Policy" not in response:
            # Swagger UI ships its own inline bootstrapping script; give the
            # API docs page a slightly relaxed policy, keep the strict one
            # everywhere else.
            if request.path.startswith("/api/docs") or request.path.startswith("/api/redoc"):
                response["Content-Security-Policy"] = (
                    "default-src 'self'; img-src 'self' data: blob:; "
                    "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                    "connect-src 'self'; frame-ancestors 'none'"
                )
            else:
                response["Content-Security-Policy"] = self.csp
        response.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        response.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
        )
        return response
