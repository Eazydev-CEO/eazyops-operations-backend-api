"""Error pages and a lightweight health endpoint."""
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone


def error_403(request, exception=None):
    return render(request, "errors/403.html", status=403)


def error_404(request, exception=None):
    return render(request, "errors/404.html", status=404)


def error_500(request):
    return render(request, "errors/500.html", status=500)


def health_ping(request):
    """Unauthenticated liveness probe for load balancers / uptime checks.

    Deliberately minimal: no versions, no hostnames, no configuration.
    """
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        db_ok = False
    status = 200 if db_ok else 503
    return JsonResponse(
        {"status": "ok" if db_ok else "degraded", "time": timezone.now().isoformat()},
        status=status,
    )
