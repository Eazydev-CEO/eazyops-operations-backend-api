"""Upload validation shared by web forms and API serializers."""
import os

from django.core.exceptions import ValidationError
from django.conf import settings
from django.utils.text import get_valid_filename

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}

# Conservative content-type expectations per extension.
EXPECTED_CONTENT_TYPES = {
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "png": {"image/png"},
    "gif": {"image/gif"},
    "webp": {"image/webp"},
    "pdf": {"application/pdf"},
    "doc": {"application/msword"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "xls": {"application/vnd.ms-excel"},
    "xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    "csv": {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"},
    "txt": {"text/plain"},
    "zip": {"application/zip", "application/x-zip-compressed"},
}


def get_max_upload_bytes():
    # Runtime setting wins over the environment default.
    from apps.core.services import get_setting_int

    mb = get_setting_int("max_upload_mb", getattr(settings, "MAX_UPLOAD_MB", 10))
    return mb * 1024 * 1024


def sanitize_filename(name):
    base = get_valid_filename(os.path.basename(name or "file"))
    return base[:120] or "file"


def validate_upload(uploaded_file, allowed_extensions=None):
    """Validate extension, size and (for images) actual content.

    Raises ValidationError with a user-facing message on any problem.
    """
    allowed = allowed_extensions or settings.ALLOWED_UPLOAD_EXTENSIONS
    name = uploaded_file.name or ""
    ext = os.path.splitext(name)[1].lstrip(".").lower()

    if not ext or ext not in allowed:
        raise ValidationError(
            f"File type '.{ext or '?'}' is not allowed. "
            f"Allowed types: {', '.join(sorted(allowed))}."
        )

    max_bytes = get_max_upload_bytes()
    if uploaded_file.size > max_bytes:
        raise ValidationError(
            f"File is too large ({uploaded_file.size / (1024 * 1024):.1f} MB). "
            f"Maximum size is {max_bytes // (1024 * 1024)} MB."
        )

    declared = (getattr(uploaded_file, "content_type", "") or "").lower()
    expected = EXPECTED_CONTENT_TYPES.get(ext)
    if declared and expected and declared not in expected and not declared.startswith("application/octet-stream"):
        raise ValidationError("File content type does not match its extension.")

    if ext in IMAGE_EXTENSIONS:
        from PIL import Image, UnidentifiedImageError

        try:
            pos = uploaded_file.tell()
            image = Image.open(uploaded_file)
            image.verify()
            uploaded_file.seek(pos)
        except (UnidentifiedImageError, OSError) as exc:
            raise ValidationError("Uploaded image is corrupt or not a real image.") from exc

    return uploaded_file


def validate_avatar(uploaded_file):
    return validate_upload(uploaded_file, allowed_extensions=sorted(IMAGE_EXTENSIONS))
