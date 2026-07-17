"""Small shared helpers with no model dependencies."""


def client_meta(request):
    """(ip, user_agent) from a request, proxy-aware. Returns (None, "") without one."""
    if request is None:
        return None, ""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    return ip or None, request.META.get("HTTP_USER_AGENT", "")
