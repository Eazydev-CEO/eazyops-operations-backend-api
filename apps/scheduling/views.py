from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from apps.core.rbac import CapabilityRequiredMixin, Roles, require_capability

from . import services
from .forms import CompleteOccurrenceForm, ScheduledTaskForm
from .models import ScheduledTask, SchedulerRun, TaskOccurrence


def _tasks_visible_to(user):
    qs = ScheduledTask.objects.select_related("team_department", "created_by")
    if user.role in (Roles.SUPER_ADMIN, Roles.MANAGER):
        return qs
    if user.role == Roles.STAFF:
        return qs.filter(
            Q(assignees=user) | Q(team_department_id=user.department_id, team_department__isnull=False)
        ).distinct()
    return qs.none()


class ScheduledTaskListView(CapabilityRequiredMixin, ListView):
    capability = "schedules.view"
    template_name = "scheduling/task_list.html"
    context_object_name = "tasks"
    paginate_by = 20

    def get_queryset(self):
        qs = _tasks_visible_to(self.request.user).annotate(
            pending_count=Count("occurrences", filter=Q(occurrences__status=TaskOccurrence.Status.PENDING)),
        )
        status = self.request.GET.get("status", "")
        frequency = self.request.GET.get("frequency", "")
        q = self.request.GET.get("q", "").strip()
        if status in ScheduledTask.Status.values:
            qs = qs.filter(status=status)
        if frequency in ScheduledTask.Frequency.values:
            qs = qs.filter(frequency=frequency)
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
        return qs.order_by("status", "next_run_at", "name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["statuses"] = ScheduledTask.Status.choices
        ctx["frequencies"] = ScheduledTask.Frequency.choices
        ctx["filters"] = {
            "status": self.request.GET.get("status", ""),
            "frequency": self.request.GET.get("frequency", ""),
            "q": self.request.GET.get("q", ""),
        }
        ctx["failed_tasks"] = (
            ScheduledTask.objects.filter(failure_count__gt=0).count()
            if self.request.user.can("schedules.manage") else 0
        )
        return ctx


@login_required
def task_detail(request, pk):
    task = _tasks_visible_to(request.user).filter(pk=pk).first()
    if task is None:
        raise Http404
    occurrences = task.occurrences.select_related("assigned_to", "created_job", "completed_by")[:100]
    return render(request, "scheduling/task_detail.html", {
        "task": task,
        "occurrences": occurrences,
        "can_manage": request.user.can("schedules.manage"),
        "complete_form": CompleteOccurrenceForm(),
    })


@login_required
def task_create(request):
    require_capability(request.user, "schedules.manage")
    if request.method == "POST":
        form = ScheduledTaskForm(request.POST)
        if form.is_valid():
            try:
                task = services.create_scheduled_task(
                    actor=request.user,
                    instance=form.save(commit=False),
                    assignees=form.cleaned_data.get("assignees"),
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Scheduled task '{task.name}' created.")
                return redirect(task.get_absolute_url())
    else:
        form = ScheduledTaskForm()
    return render(request, "scheduling/task_form.html", {"form": form, "task": None})


@login_required
def task_edit(request, pk):
    require_capability(request.user, "schedules.manage")
    task = get_object_or_404(ScheduledTask, pk=pk)
    if request.method == "POST":
        form = ScheduledTaskForm(request.POST, instance=task)
        if form.is_valid():
            try:
                services.update_scheduled_task(
                    actor=request.user,
                    instance=form.save(commit=False),
                    assignees=form.cleaned_data.get("assignees"),
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Scheduled task '{task.name}' updated.")
                return redirect(task.get_absolute_url())
    else:
        form = ScheduledTaskForm(instance=task)
    return render(request, "scheduling/task_form.html", {"form": form, "task": task})


@login_required
@require_POST
def task_set_status(request, pk):
    require_capability(request.user, "schedules.manage")
    task = get_object_or_404(ScheduledTask, pk=pk)
    status = request.POST.get("status", "")
    try:
        services.set_task_status(actor=request.user, task=task, status=status, request=request)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, f"'{task.name}' is now {task.get_status_display()}.")
    return redirect(task.get_absolute_url())


class MyTasksView(LoginRequiredMixin, ListView):
    """Occurrences assigned to the requesting user."""

    template_name = "scheduling/my_tasks.html"
    context_object_name = "occurrences"
    paginate_by = 20

    def get_queryset(self):
        qs = TaskOccurrence.objects.filter(
            assigned_to=self.request.user
        ).select_related("scheduled_task", "created_job")
        status = self.request.GET.get("status", "")
        if status in TaskOccurrence.Status.values:
            qs = qs.filter(status=status)
        else:
            qs = qs.filter(status=TaskOccurrence.Status.PENDING)
        return qs.order_by("scheduled_for")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["statuses"] = TaskOccurrence.Status.choices
        ctx["current_status"] = self.request.GET.get("status", TaskOccurrence.Status.PENDING)
        ctx["complete_form"] = CompleteOccurrenceForm()
        return ctx


@login_required
@require_POST
def occurrence_complete(request, pk):
    occurrence = get_object_or_404(
        TaskOccurrence.objects.select_related("scheduled_task"), pk=pk
    )
    # Visibility: assignee, or scheduling managers
    if not (
        occurrence.assigned_to_id == request.user.pk
        or request.user.role in (Roles.SUPER_ADMIN, Roles.MANAGER)
    ):
        raise Http404
    form = CompleteOccurrenceForm(request.POST)
    form.is_valid()
    try:
        services.complete_occurrence(
            actor=request.user, occurrence=occurrence,
            notes=form.cleaned_data.get("notes", ""), request=request,
        )
    except (ValidationError, PermissionDenied) as exc:
        msg = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        messages.error(request, msg)
    else:
        messages.success(request, "Task marked as completed.")
    next_url = request.POST.get("next", "")
    if next_url == "detail":
        return redirect(occurrence.scheduled_task.get_absolute_url())
    return redirect("scheduling:my_tasks")


class SchedulerRunListView(CapabilityRequiredMixin, ListView):
    """Scheduler execution monitor (managers and super admins)."""

    capability = "schedules.run_monitor"
    template_name = "scheduling/run_list.html"
    context_object_name = "runs"
    paginate_by = 20

    def get_queryset(self):
        return SchedulerRun.objects.select_related("triggered_by_user")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["failed_tasks"] = ScheduledTask.objects.filter(failure_count__gt=0).order_by("-updated_at")[:10]
        return ctx


@login_required
def run_detail(request, pk):
    require_capability(request.user, "schedules.run_monitor")
    run = get_object_or_404(SchedulerRun, pk=pk)
    return render(request, "scheduling/run_detail.html", {"run": run})


@login_required
@require_POST
def run_now(request):
    require_capability(request.user, "schedules.run_monitor")
    run = services.run_scheduler_pass(trigger=SchedulerRun.Trigger.MANUAL, user=request.user)
    if run is None:
        messages.warning(request, "A scheduler pass is already running — try again shortly.")
    else:
        messages.success(
            request,
            f"Scheduler run #{run.pk} finished: {run.tasks_processed} task(s), "
            f"{run.occurrences_created} occurrence(s), {run.errors_count} error(s).",
        )
    return redirect("scheduling:runs")
