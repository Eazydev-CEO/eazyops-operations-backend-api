"""Audit recording — the single entry point used across the platform."""
import logging

from apps.core.utils import client_meta

from .models import Actions, AuditLog

__all__ = ["Actions", "record_audit"]

logger = logging.getLogger("eazyops.audits")


def _serialise(value):
    """Make before/after values JSON-safe without leaking sensitive blobs."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)[:200]


def record_audit(*, actor=None, action, instance=None, object_type="", object_id="",
                 object_repr="", changes=None, metadata=None, request=None,
                 ip_address=None, user_agent=""):
    """Insert one immutable audit record. Never raises into business flows."""
    try:
        if instance is not None:
            object_type = object_type or instance._meta.label_lower
            object_id = object_id or str(getattr(instance, "pk", "") or "")
            object_repr = object_repr or str(instance)[:200]
        if request is not None and (ip_address is None and not user_agent):
            ip_address, user_agent = client_meta(request)

        clean_changes = None
        if changes:
            clean_changes = {
                str(field): [_serialise(pair[0]), _serialise(pair[1])]
                for field, pair in changes.items()
            }

        actor = actor if getattr(actor, "pk", None) else None
        return AuditLog.objects.create(
            actor=actor,
            actor_email=getattr(actor, "email", "") or "",
            action=action,
            object_type=object_type,
            object_id=object_id,
            object_repr=object_repr,
            changes=clean_changes,
            metadata=metadata or None,
            ip_address=ip_address,
            user_agent=(user_agent or "")[:300],
        )
    except Exception:  # pragma: no cover — auditing must never break the request
        logger.exception("Failed to write audit record for action=%s", action)
        return None


def diff_fields(instance, cleaned_data, fields):
    """Compute a {field: [before, after]} dict for changed model fields."""
    changes = {}
    for field in fields:
        if field not in cleaned_data:
            continue
        old, new = getattr(instance, field), cleaned_data[field]
        if old != new:
            changes[field] = [old, new]
    return changes
