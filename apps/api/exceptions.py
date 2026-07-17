"""Consistent API error envelope.

Every error response has the shape:

    {
        "success": false,
        "error": {
            "code": "validation_error" | "permission_denied" | ...,
            "detail": "Human-readable summary.",
            "fields": {"field_name": ["problem", ...]}   # when applicable
        }
    }

Django-core ValidationError/PermissionDenied raised inside the service
layer are translated too, so web and API share one business-rule source.
"""
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def _normalise_detail(data):
    """Split DRF error detail into (summary, fields)."""
    if isinstance(data, dict):
        fields = {}
        non_field = []
        for key, value in data.items():
            values = value if isinstance(value, list) else [value]
            values = [str(v) for v in values]
            if key in ("non_field_errors", "detail", "__all__"):
                non_field.extend(values)
            else:
                fields[str(key)] = values
        summary = non_field[0] if non_field else (
            "Validation failed." if fields else "Request failed."
        )
        return summary, fields or None
    if isinstance(data, list):
        return (str(data[0]) if data else "Request failed."), None
    return str(data), None


def api_exception_handler(exc, context):
    # Translate service-layer (Django-core) exceptions into DRF equivalents.
    if isinstance(exc, DjangoValidationError):
        detail = getattr(exc, "message_dict", None) or exc.messages
        exc = drf_exceptions.ValidationError(detail)
    elif isinstance(exc, DjangoPermissionDenied):
        exc = drf_exceptions.PermissionDenied(str(exc) or None)
    elif isinstance(exc, Http404):
        exc = drf_exceptions.NotFound()

    response = drf_exception_handler(exc, context)
    if response is None:
        return None  # unhandled -> 500 (no stack trace leaves the server)

    code = getattr(getattr(exc, "detail", None), "code", None) or getattr(
        exc, "default_code", "error"
    )
    summary, fields = _normalise_detail(response.data)
    payload = {"success": False, "error": {"code": str(code), "detail": summary}}
    if fields:
        payload["error"]["fields"] = fields
    wrapped = Response(payload, status=response.status_code)
    # preserve rate-limit / auth headers DRF set on the original response
    for header in ("Retry-After", "WWW-Authenticate"):
        if header in response:
            wrapped[header] = response[header]
    return wrapped
