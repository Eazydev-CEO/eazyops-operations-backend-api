import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from apps.audits.models import AuditLog
from apps.audits.services import Actions, record_audit
from apps.core.models import Department
from apps.core.rbac import CapabilityRequiredMixin, require_capability

from . import services
from .filters import filter_jobs
from .forms import (
    AssignForm, AttachmentForm, CategoryForm, CommentForm, DepartmentForm,
    JobForm, TransitionForm,
)
from .models import Job, JobAttachment, JobCategory


def _visible_job_or_404(request, pk, prefetch=False):
    qs = Job.objects.visible_to(request.user)
    if prefetch:
        qs = qs.select_related("category", "department", "manager", "reviewed_by", "created_by")
    try:
        return qs.get(pk=pk)
    except Job.DoesNotExist:
        raise Http404


# ---------------------------------------------------------------------------
# List / detail
# ---------------------------------------------------------------------------
class JobListView(LoginRequiredMixin, ListView):
    template_name = "operations/job_list.html"
    context_object_name = "jobs"
    paginate_by = 20

    def get_queryset(self):
        qs = Job.objects.visible_to(self.request.user).select_related(
            "category", "department", "manager"
        ).prefetch_related("assignments__staff", "tags")
        return filter_jobs(qs, self.request.GET)

    def get_context_data(self, **kwargs):
        from django.contrib.auth import get_user_model
        from apps.core.rbac import Roles

        ctx = super().get_context_data(**kwargs)
        ctx["statuses"] = Job.Status.choices
        ctx["priorities"] = Job.Priority.choices
        ctx["categories"] = JobCategory.objects.filter(is_active=True)
        ctx["departments"] = Department.objects.filter(is_active=True)
        ctx["staff_users"] = (
            get_user_model().objects.filter(role=Roles.STAFF, is_active=True)
            if self.request.user.can("jobs.view_all") else []
        )
        keys = ["q", "status", "priority", "category", "department", "staff",
                "tag", "due_from", "due_to", "created_from", "created_to", "sort"]
        ctx["filters"] = {k: self.request.GET.get(k, "") for k in keys}
        querydict = self.request.GET.copy()
        querydict.pop("page", None)
        ctx["querystring"] = querydict.urlencode()
        return ctx


@login_required
def job_detail(request, pk):
    job = _visible_job_or_404(request, pk, prefetch=True)
    user = request.user
    assignments = job.assignments.select_related("staff")
    timeline = AuditLog.objects.filter(
        object_type="operations.job", object_id=str(job.pk)
    ).select_related("actor")[:50]
    context = {
        "job": job,
        "assignments": assignments,
        "comments": job.comments.select_related("author"),
        "attachments": job.attachments.select_related("uploaded_by"),
        "timeline": timeline,
        "comment_form": CommentForm(),
        "attachment_form": AttachmentForm(),
        "assign_form": AssignForm(initial={"staff": [a.staff_id for a in assignments]}),
        "allowed_transitions": services.allowed_transitions_for(user, job),
        "can_edit": services.can_edit_job(user, job),
        "can_progress": services.can_progress_job(user, job),
        "can_assign": user.can("jobs.assign") and job.status not in Job.TERMINAL_STATUSES,
        "can_delete": user.can("jobs.delete"),
        "status_labels": dict(Job.Status.choices),
    }
    return render(request, "operations/job_detail.html", context)


# ---------------------------------------------------------------------------
# Create / edit / delete
# ---------------------------------------------------------------------------
@login_required
def job_create(request):
    require_capability(request.user, "jobs.create")
    if request.method == "POST":
        form = JobForm(request.POST)
        if form.is_valid():
            data = dict(form.cleaned_data)
            data["status"] = form.cleaned_data.get("initial_status") or Job.Status.OPEN
            try:
                job = services.create_job(
                    actor=request.user,
                    data=data,
                    staff=form.cleaned_data.get("staff"),
                    tags_raw=form.cleaned_data.get("tags_raw", ""),
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Job {job.number} created.")
                return redirect(job.get_absolute_url())
    else:
        form = JobForm(initial={"manager": request.user.pk})
    return render(request, "operations/job_form.html", {"form": form, "job": None})


@login_required
def job_edit(request, pk):
    job = _visible_job_or_404(request, pk)
    if not services.can_edit_job(request.user, job):
        raise PermissionDenied
    if request.method == "POST":
        form = JobForm(request.POST, instance=job, editing=True)
        if form.is_valid():
            services.update_job(
                actor=request.user, job=job,
                data=form.cleaned_data,
                tags_raw=form.cleaned_data.get("tags_raw", ""),
                request=request,
            )
            messages.success(request, f"Job {job.number} updated.")
            return redirect(job.get_absolute_url())
    else:
        form = JobForm(instance=job, editing=True)
    return render(request, "operations/job_form.html", {"form": form, "job": job})


@login_required
@require_POST
def job_delete(request, pk):
    job = _visible_job_or_404(request, pk)
    try:
        services.delete_job(actor=request.user, job=job, request=request)
    except PermissionDenied:
        messages.error(request, "Only a Super Admin can delete jobs.")
        return redirect(job.get_absolute_url())
    messages.success(request, f"Job {job.number} deleted.")
    return redirect("operations:job_list")


# ---------------------------------------------------------------------------
# Workflow actions
# ---------------------------------------------------------------------------
@login_required
@require_POST
def job_transition(request, pk):
    job = _visible_job_or_404(request, pk)
    form = TransitionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Invalid status change request.")
        return redirect(job.get_absolute_url())
    try:
        services.transition_job(
            actor=request.user, job=job,
            new_status=form.cleaned_data["status"],
            note=form.cleaned_data.get("note", ""),
            request=request,
        )
    except (ValidationError, PermissionDenied) as exc:
        msg = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        messages.error(request, msg or "You cannot perform this status change.")
    else:
        messages.success(request, f"Status changed to {Job.Status(form.cleaned_data['status']).label}.")
    return redirect(job.get_absolute_url())


@login_required
@require_POST
def job_assign(request, pk):
    job = _visible_job_or_404(request, pk)
    form = AssignForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Invalid staff selection.")
        return redirect(job.get_absolute_url())
    try:
        services.assign_staff(
            actor=request.user, job=job,
            staff_users=list(form.cleaned_data["staff"]),
            request=request,
        )
    except (ValidationError, PermissionDenied) as exc:
        msg = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        messages.error(request, msg)
    else:
        messages.success(request, "Assignments updated.")
    return redirect(job.get_absolute_url())


@login_required
@require_POST
def job_comment(request, pk):
    job = _visible_job_or_404(request, pk)
    form = CommentForm(request.POST)
    if form.is_valid():
        try:
            services.add_comment(
                actor=request.user, job=job,
                body=form.cleaned_data["body"], kind=form.cleaned_data["kind"],
                request=request,
            )
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Comment added.")
    else:
        messages.error(request, "Comment cannot be empty.")
    return redirect(job.get_absolute_url())


@login_required
@require_POST
def job_attachment_upload(request, pk):
    job = _visible_job_or_404(request, pk)
    form = AttachmentForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            services.add_attachment(
                actor=request.user, job=job, uploaded_file=form.cleaned_data["file"],
                request=request,
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        except PermissionDenied as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "File uploaded.")
    else:
        messages.error(request, "Choose a file to upload.")
    return redirect(job.get_absolute_url())


@login_required
def attachment_download(request, pk):
    """Attachments are served through this gate — never raw MEDIA paths."""
    attachment = get_object_or_404(JobAttachment.objects.select_related("job"), pk=pk)
    if not services.can_view_job(request.user, attachment.job):
        raise Http404
    try:
        handle = attachment.file.open("rb")
    except (FileNotFoundError, ValueError):
        raise Http404
    return FileResponse(handle, as_attachment=True, filename=attachment.original_name)


@login_required
@require_POST
def attachment_delete(request, pk):
    attachment = get_object_or_404(JobAttachment.objects.select_related("job"), pk=pk)
    if not services.can_view_job(request.user, attachment.job):
        raise Http404
    job = attachment.job
    try:
        services.delete_attachment(actor=request.user, attachment=attachment, request=request)
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Attachment deleted.")
    return redirect(job.get_absolute_url())


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
@login_required
def jobs_export(request):
    require_capability(request.user, "jobs.export")
    qs = filter_jobs(
        Job.objects.visible_to(request.user).select_related("category", "department", "manager"),
        request.GET,
    ).prefetch_related("assignments__staff")

    response = HttpResponse(content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d-%H%M")
    response["Content-Disposition"] = f'attachment; filename="jobs-{stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Number", "Title", "Category", "Priority", "Status", "Effective status",
        "Department", "Manager", "Assigned staff", "Start date", "Due date",
        "Completed at", "Estimated cost", "Actual cost", "Created at",
    ])
    for job in qs:
        writer.writerow([
            job.number, job.title, job.category.name, job.priority, job.status,
            job.effective_status, job.department.name, job.manager.display_name,
            "; ".join(a.staff.display_name for a in job.assignments.all()),
            job.start_date or "", job.due_date or "",
            job.completed_at.strftime("%Y-%m-%d %H:%M") if job.completed_at else "",
            job.estimated_cost if job.estimated_cost is not None else "",
            job.actual_cost if job.actual_cost is not None else "",
            job.created_at.strftime("%Y-%m-%d %H:%M"),
        ])
    record_audit(
        actor=request.user, action=Actions.EXPORT_GENERATED,
        object_type="operations.job", object_repr="jobs.csv",
        metadata={"rows": qs.count(), "filters": {k: v for k, v in request.GET.items() if v}},
        request=request,
    )
    return response


# ---------------------------------------------------------------------------
# Configuration: categories & departments (custom admin area)
# ---------------------------------------------------------------------------
class CategoryListView(CapabilityRequiredMixin, ListView):
    capability = "categories.manage"
    template_name = "operations/manage/category_list.html"
    context_object_name = "categories"

    def get_queryset(self):
        from django.db.models import Count

        return JobCategory.objects.annotate(job_count=Count("jobs"))


@login_required
def category_edit(request, pk=None):
    require_capability(request.user, "categories.manage")
    category = get_object_or_404(JobCategory, pk=pk) if pk else None
    if request.method == "POST":
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            obj = form.save()
            record_audit(
                actor=request.user, action=Actions.CATEGORY_CHANGED, instance=obj,
                metadata={"created": category is None}, request=request,
            )
            messages.success(request, f"Category '{obj.name}' saved.")
            return redirect("operations:manage_categories")
    else:
        form = CategoryForm(instance=category)
    return render(request, "operations/manage/category_form.html", {"form": form, "category": category})


class DepartmentListView(CapabilityRequiredMixin, ListView):
    capability = "departments.manage"
    template_name = "operations/manage/department_list.html"
    context_object_name = "departments"

    def get_queryset(self):
        from django.db.models import Count

        return Department.objects.annotate(
            job_count=Count("jobs", distinct=True),
            member_count=Count("members", distinct=True),
        )


@login_required
def department_edit(request, pk=None):
    require_capability(request.user, "departments.manage")
    department = get_object_or_404(Department, pk=pk) if pk else None
    if request.method == "POST":
        form = DepartmentForm(request.POST, instance=department)
        if form.is_valid():
            obj = form.save()
            record_audit(
                actor=request.user, action=Actions.DEPARTMENT_CHANGED, instance=obj,
                metadata={"created": department is None}, request=request,
            )
            messages.success(request, f"Department '{obj.name}' saved.")
            return redirect("operations:manage_departments")
    else:
        form = DepartmentForm(instance=department)
    return render(request, "operations/manage/department_form.html", {"form": form, "department": department})
