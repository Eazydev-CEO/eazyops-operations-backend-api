"""
Production settings — hardened defaults.

Required environment variables (see ENVIRONMENT.md):
    SECRET_KEY, ALLOWED_HOSTS, DATABASE_URL, CSRF_TRUSTED_ORIGINS,
    EMAIL_* (for outbound mail), SITE_URL
"""
from .base import *  # noqa: F401,F403
from .base import env, env_bool, env_int

DEBUG = False

if env("SECRET_KEY") in (None, "", "change-me-in-production"):
    raise RuntimeError("SECRET_KEY must be set to a strong unique value in production.")

# --- TLS / cookies / HSTS -------------------------------------------------
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# --- Error reporting ------------------------------------------------------
ADMINS = [("Ops", email) for email in env("ADMIN_EMAILS", "").split(",") if email]
SERVER_EMAIL = env("SERVER_EMAIL", "eazyops-errors@localhost")

LOGGING["root"]["level"] = "WARNING"  # noqa: F405
LOGGING["loggers"]["eazyops"]["level"] = "INFO"  # noqa: F405
