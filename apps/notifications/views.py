from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from . import services
from .models import Notification


class NotificationListView(LoginRequiredMixin, ListView):
    """Notification history — strictly the requesting user's own rows."""

    template_name = "notifications/list.html"
    context_object_name = "notifications"
    paginate_by = 20

    def get_queryset(self):
        qs = Notification.objects.filter(user=self.request.user)
        show = self.request.GET.get("show", "")
        if show == "unread":
            qs = qs.filter(is_read=False)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["show"] = self.request.GET.get("show", "")
        ctx["unread_count"] = Notification.objects.filter(
            user=self.request.user, is_read=False
        ).count()
        return ctx


@login_required
def feed(request):
    """AJAX feed for the topbar bell: unread count + latest ten."""
    items = list(
        Notification.objects.filter(user=request.user)[:10].values(
            "id", "type", "title", "message", "url", "is_read", "created_at"
        )
    )
    for item in items:
        item["created_at"] = item["created_at"].strftime("%Y-%m-%d %H:%M")
    unread = Notification.objects.filter(user=request.user, is_read=False).count()
    return JsonResponse({"unread": unread, "items": items})


@login_required
@require_POST
def mark_read(request, pk):
    ok = services.mark_read(request.user, pk)
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": ok})
    next_url = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("notifications:list")


@login_required
@require_POST
def mark_all_read(request):
    count = services.mark_all_read(request.user)
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "updated": count})
    return redirect("notifications:list")
