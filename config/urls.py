"""EazyOps root URL configuration."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # Django admin kept as a restricted emergency backend only.
    path("django-admin/", admin.site.urls),

    # Web application
    path("", include("apps.dashboard.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("jobs/", include("apps.operations.urls")),
    path("schedule/", include("apps.scheduling.urls")),
    path("notifications/", include("apps.notifications.urls")),
    path("audits/", include("apps.audits.urls")),
    path("reports/", include("apps.reports.urls")),
    path("core/", include("apps.core.urls")),

    # Versioned REST API + documentation
    path("api/", include("apps.api.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler403 = "apps.core.views.error_403"
handler404 = "apps.core.views.error_404"
handler500 = "apps.core.views.error_500"

admin.site.site_header = "EazyOps — Emergency Backend"
admin.site.site_title = "EazyOps Admin"
admin.site.index_title = "Restricted administration (use the EazyOps dashboard for daily work)"
