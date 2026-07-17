"""Development settings — SQLite, console email, relaxed security."""
from .base import *  # noqa: F401,F403
from .base import env_bool

DEBUG = env_bool("DEBUG", True)

# Whitenoise manifest storage requires collectstatic; use plain storage in dev.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

INTERNAL_IPS = ["127.0.0.1"]

# Faster feedback while iterating on emails locally
EMAIL_SUBJECT_PREFIX = "[EazyOps DEV] "
