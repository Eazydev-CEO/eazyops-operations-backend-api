from django.views.generic import ListView

from apps.core.rbac import CapabilityRequiredMixin

from .models import Actions, AuditLog


class AuditLogListView(CapabilityRequiredMixin, ListView):
    """Searchable, filterable, paginated audit viewer (authorised roles only)."""

    capability = "audits.view"
    template_name = "audits/audit_list.html"
    context_object_name = "entries"
    paginate_by = 25

    def get_queryset(self):
        qs = AuditLog.objects.select_related("actor")
        params = self.request.GET
        q = params.get("q", "").strip()
        action = params.get("action", "")
        object_type = params.get("object_type", "").strip()
        actor = params.get("actor", "").strip()
        date_from = params.get("date_from", "")
        date_to = params.get("date_to", "")

        if q:
            from django.db.models import Q

            qs = qs.filter(
                Q(object_repr__icontains=q) | Q(actor_email__icontains=q)
                | Q(object_id=q) | Q(object_type__icontains=q)
            )
        if action in Actions.values:
            qs = qs.filter(action=action)
        if object_type:
            qs = qs.filter(object_type__icontains=object_type)
        if actor:
            qs = qs.filter(actor_email__icontains=actor)
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["actions"] = Actions.choices
        ctx["filters"] = {
            "q": self.request.GET.get("q", ""),
            "action": self.request.GET.get("action", ""),
            "object_type": self.request.GET.get("object_type", ""),
            "actor": self.request.GET.get("actor", ""),
            "date_from": self.request.GET.get("date_from", ""),
            "date_to": self.request.GET.get("date_to", ""),
        }
        querydict = self.request.GET.copy()
        querydict.pop("page", None)
        ctx["querystring"] = querydict.urlencode()
        return ctx
