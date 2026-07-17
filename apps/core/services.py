"""Core services: runtime settings with sane registered defaults."""
from django.conf import settings as django_settings
from django.core.cache import cache

from .models import SystemSetting

_CACHE_KEY = "eazyops.system_settings"
_CACHE_TTL = 60

#: key -> (default, description) — shown/editable in the admin area.
SETTINGS_REGISTRY = {
    "job_due_soon_days": (
        str(getattr(django_settings, "JOB_DUE_SOON_DAYS", 1)),
        "Days before the due date when a job counts as 'due soon' and staff are notified.",
    ),
    "max_upload_mb": (
        str(getattr(django_settings, "MAX_UPLOAD_MB", 10)),
        "Maximum attachment upload size in megabytes.",
    ),
    "invitation_expiry_days": (
        str(getattr(django_settings, "INVITATION_EXPIRY_DAYS", 7)),
        "How long invitation links remain valid.",
    ),
    "login_max_failures": (
        str(getattr(django_settings, "LOGIN_MAX_FAILURES", 5)),
        "Failed login attempts allowed inside the lockout window.",
    ),
    "login_lockout_minutes": (
        str(getattr(django_settings, "LOGIN_LOCKOUT_MINUTES", 15)),
        "Lockout window length in minutes after too many failed logins.",
    ),
    "notification_emails_enabled": (
        "true",
        "Master switch for outbound notification emails (per-user opt-out still applies).",
    ),
    "occurrence_missed_grace_hours": (
        "24",
        "Hours after the scheduled time before a pending task occurrence is marked missed.",
    ),
}


def _load_all():
    data = cache.get(_CACHE_KEY)
    if data is None:
        data = dict(SystemSetting.objects.values_list("key", "value"))
        cache.set(_CACHE_KEY, data, _CACHE_TTL)
    return data


def invalidate_settings_cache():
    cache.delete(_CACHE_KEY)


def get_setting(key, default=None):
    """String value of a setting.

    Resolution order: DB row > caller-provided default (which callers
    typically derive from live Django settings) > registry default.
    """
    stored = _load_all().get(key)
    if stored is not None:
        return stored
    if default is not None:
        return default
    if key in SETTINGS_REGISTRY:
        return SETTINGS_REGISTRY[key][0]
    return default


def get_setting_int(key, default=0):
    try:
        return int(get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default


def get_setting_bool(key, default=False):
    raw = get_setting(key, str(default))
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def set_setting(key, value, user=None):
    """Persist a setting; returns (setting, old_value). Caller audits the change."""
    obj, _created = SystemSetting.objects.get_or_create(
        key=key,
        defaults={
            "value": str(value),
            "description": SETTINGS_REGISTRY.get(key, ("", ""))[1],
            "updated_by": user,
        },
    )
    old = None if _created else obj.value
    if not _created:
        obj.value = str(value)
        obj.updated_by = user
        if not obj.description and key in SETTINGS_REGISTRY:
            obj.description = SETTINGS_REGISTRY[key][1]
        obj.save(update_fields=["value", "updated_by", "description", "updated_at"])
    invalidate_settings_cache()
    return obj, old
