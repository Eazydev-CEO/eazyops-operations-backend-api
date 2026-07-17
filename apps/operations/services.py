"""
Operations service layer — every job mutation flows through here so the
status machine, permission boundaries, audit trail and notifications stay
consistent across web views and the REST API.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audits.services import Actions, record_audit
from apps.core.rbac import Roles
from apps.core.validators import sanitize_filename, validate_upload
from apps.notifications.services import NotificationTypes, notify

from .models import Job, JobAssignment, JobAttachment, JobComment, JobSequence, Tag

Status = Job.Status

#: Valid transitions. OVERDUE is a derived overlay, never a stored state.
TRANSITIONS = {
    Status.DRAFT: {Status.OPEN, Status.CANCELLED},
    Status.OPEN: {Status.ASSIGNED, Status.CANCELLED},
    Status.ASSIGNED: {Status.IN_PROGRESS, Status.OPEN, Status.CANCELLED},
    Status.IN_PROGRESS: {Status.AWAITING_REVIEW, Status.CANCELLED},
    Status.AWAITING_REVIEW: {Status.COMPLETED, Status.IN_PROGRESS, Status.CANCELLED},
    Status.COMPLETED: set(),
    Status.CANCELLED: set(),
}

#: Transitions assigned staff may perform on their own jobs.
STAFF_TRANSITIONS = {
    (Status.ASSIGNED, Status.IN_PROGRESS),
    (Status.IN_PROGRESS, Status.AWAITING_REVIEW),
}

#: Transitions that constitute a management review decision.
REVIEW_TRANSITIONS = {
    (Status.AWAITING_REVIEW, Status.COMPLETED),
    (Status.AWAITING_REVIEW, Status.IN_PROGRESS),
}


# ---------------------------------------------------------------------------
# Access checks (object level)
# ---------------------------------------------------------------------------
def can_view_job(user, job):
    return Job.objects.visible_to(user).filter(pk=job.pk).exists()


def is_assigned(user, job):
    return job.assignments.filter(staff=user).exists()


def can_edit_job(user, job):
    """Full metadata edits: managers on live jobs, super admins always."""
    if user.role == Roles.SUPER_ADMIN:
        return True
    if user.role == Roles.MANAGER:
        return job.status not in Job.TERMINAL_STATUSES
    return False


def can_progress_job(user, job):
    """Comments / evidence / progress: managers, or staff assigned to the job."""
    if user.role in (Roles.SUPER_ADMIN, Roles.MANAGER):
        return True
    return user.role == Roles.STAFF and is_assigned(user, job)


def allowed_transitions_for(user, job):
    """Transitions this user may trigger from the job's current state."""
    current = Status(job.status)
    result = []
    for target in TRANSITIONS.get(current, set()):
        pair = (current, target)
        if user.role in (Roles.SUPER_ADMIN, Roles.MANAGER):
            if target == Status.ASSIGNED:
                continue  # only via assignment
            result.append(target)
        elif user.role == Roles.STAFF and pair in STAFF_TRANSITIONS and is_assigned(user, job):
            result.append(target)
    return result


# ---------------------------------------------------------------------------
# Job numbers
# ---------------------------------------------------------------------------
def generate_job_number():
    """Sequential, human-friendly reference: JOB-<year>-<00001>.

    Uses a per-year counter row under select_for_update (row lock on
    PostgreSQL; whole-transaction lock on SQLite) plus a retry loop as a
    belt-and-braces guard against unique collisions.
    """
    year = timezone.now().year
    with transaction.atomic():
        seq, _ = JobSequence.objects.select_for_update().get_or_create(year=year)
        seq.last_value += 1
        seq.save(update_fields=["last_value"])
        return f"JOB-{year}-{seq.last_value:05d}"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def parse_tags(raw):
    names = {t.strip()[:40] for t in (raw or "").split(",") if t.strip()}
    return [Tag.objects.get_or_create(name=name)[0] for name in sorted(names)]


@transaction.atomic
def create_job(*, actor, data, staff=None, tags_raw="", request=None):
    """Create a job (status DRAFT or OPEN) and optionally assign staff."""
    status = data.get("status") or Status.DRAFT
    if status not in (Status.DRAFT, Status.OPEN):
        raise ValidationError("New jobs can only start as Draft or Open.")

    for attempt in range(3):
        try:
            job = Job.objects.create(
                number=generate_job_number(),
                title=data["title"],
                description=data.get("description", ""),
                category=data["category"],
                priority=data.get("priority") or Job.Priority.MEDIUM,
                status=status,
                department=data["department"],
                manager=data["manager"],
                start_date=data.get("start_date"),
                due_date=data.get("due_date"),
                estimated_cost=data.get("estimated_cost"),
                actual_cost=data.get("actual_cost"),
                created_by=actor,
            )
            break
        except IntegrityError:  # pragma: no cover — collision retry
            if attempt == 2:
                raise
    job.tags.set(parse_tags(tags_raw))

    record_audit(
        actor=actor, action=Actions.JOB_CREATED, instance=job,
        metadata={"status": job.status, "priority": job.priority}, request=request,
    )
    if staff:
        assign_staff(actor=actor, job=job, staff_users=list(staff), request=request)
    return job


TRACKED_FIELDS = [
    "title", "description", "category", "priority", "department", "manager",
    "start_date", "due_date", "estimated_cost", "actual_cost",
]


@transaction.atomic
def update_job(*, actor, job, data, tags_raw=None, request=None):
    if not can_edit_job(actor, job):
        raise PermissionDenied("You cannot edit this job.")
    changes = {}
    for field in TRACKED_FIELDS:
        if field not in data:
            continue
        old, new = getattr(job, field), data[field]
        if old != new:
            changes[field] = [str(old) if old is not None else None,
                              str(new) if new is not None else None]
            setattr(job, field, new)
    if tags_raw is not None:
        new_tags = parse_tags(tags_raw)
        old_names = sorted(job.tags.values_list("name", flat=True))
        new_names = sorted(t.name for t in new_tags)
        if old_names != new_names:
            changes["tags"] = [", ".join(old_names), ", ".join(new_names)]
            job.tags.set(new_tags)
    if changes:
        job.save()
        record_audit(
            actor=actor, action=Actions.JOB_UPDATED, instance=job,
            changes=changes, request=request,
        )
    return job


def delete_job(*, actor, job, request=None):
    """Hard delete — Super Admin only; every attempt is audited."""
    if actor.role != Roles.SUPER_ADMIN:
        record_audit(
            actor=actor, action=Actions.JOB_DELETE_ATTEMPTED, instance=job,
            metadata={"allowed": False}, request=request,
        )
        raise PermissionDenied("Only a Super Admin can delete jobs.")
    number, pk = job.number, job.pk
    record_audit(
        actor=actor, action=Actions.JOB_DELETED,
        object_type="operations.job", object_id=str(pk), object_repr=str(job),
        metadata={"number": number, "status": job.status}, request=request,
    )
    job.delete()


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------
@transaction.atomic
def assign_staff(*, actor, job, staff_users, request=None):
    """Replace the job's staff set with ``staff_users`` (managers only).

    Newly added staff are notified; removed staff are notified; the job
    moves OPEN -> ASSIGNED automatically (and back if emptied).
    """
    if not actor.can("jobs.assign"):
        raise PermissionDenied("You cannot assign staff to jobs.")
    if job.status in Job.TERMINAL_STATUSES:
        raise ValidationError("Completed or cancelled jobs cannot be reassigned.")
    for user in staff_users:
        if user.role != Roles.STAFF or not user.is_active:
            raise ValidationError(f"{user.display_name} is not an active operations staff member.")

    current = {a.staff_id: a for a in job.assignments.select_related("staff")}
    target_ids = {u.pk for u in staff_users}
    added = [u for u in staff_users if u.pk not in current]
    removed = [a.staff for a in current.values() if a.staff_id not in target_ids]

    if not added and not removed:
        return job

    had_assignees = bool(current)
    for user in added:
        JobAssignment.objects.create(job=job, staff=user, assigned_by=actor)
    if removed:
        job.assignments.filter(staff__in=removed).delete()

    if added:
        record_audit(
            actor=actor, action=Actions.JOB_ASSIGNED, instance=job,
            metadata={"added": [u.email for u in added]}, request=request,
        )
    if removed:
        record_audit(
            actor=actor, action=Actions.JOB_UNASSIGNED, instance=job,
            metadata={"removed": [u.email for u in removed]}, request=request,
        )

    # Status side effects
    remaining = job.assignments.exists()
    if job.status == Status.OPEN and remaining:
        _set_status(job, Status.ASSIGNED, actor, request, note="Auto: staff assigned")
    elif job.status == Status.ASSIGNED and not remaining:
        _set_status(job, Status.OPEN, actor, request, note="Auto: all staff unassigned")

    assigned_type = NotificationTypes.JOB_REASSIGNED if had_assignees else NotificationTypes.JOB_ASSIGNED
    notify(
        added, assigned_type,
        title=f"You have been assigned to {job.number}",
        message=f"{job.title} (priority {job.get_priority_display()}, due {job.due_date or 'n/a'}).",
        url=job.get_absolute_url(),
    )
    notify(
        removed, NotificationTypes.JOB_REASSIGNED,
        title=f"You have been unassigned from {job.number}",
        message=f"You are no longer assigned to '{job.title}'.",
        url=job.get_absolute_url(),
    )
    return job


# ---------------------------------------------------------------------------
# Status machine
# ---------------------------------------------------------------------------
def _set_status(job, new_status, actor, request, note=""):
    old = job.status
    job.status = new_status
    job.save(update_fields=["status", "updated_at"])
    record_audit(
        actor=actor, action=Actions.JOB_STATUS_CHANGED, instance=job,
        changes={"status": [old, new_status]},
        metadata={"note": note} if note else None,
        request=request,
    )


@transaction.atomic
def transition_job(*, actor, job, new_status, note="", request=None):
    """Validated, permission-checked state change with side effects."""
    try:
        new_status = Status(new_status)
    except ValueError:
        raise ValidationError(f"Unknown status '{new_status}'.")
    current = Status(job.status)

    if new_status == current:
        raise ValidationError("The job is already in that status.")
    if new_status == Status.OVERDUE:
        raise ValidationError("Overdue is derived automatically from the due date.")
    if new_status not in TRANSITIONS.get(current, set()):
        raise ValidationError(
            f"Invalid transition: {current.label} → {new_status.label}."
        )
    if new_status == Status.ASSIGNED:
        raise ValidationError("Assign staff to move a job into Assigned.")

    pair = (current, new_status)
    if actor.role in (Roles.SUPER_ADMIN, Roles.MANAGER):
        pass  # management may perform any valid transition
    elif actor.role == Roles.STAFF:
        if pair not in STAFF_TRANSITIONS or not is_assigned(actor, job):
            raise PermissionDenied("You cannot perform this status change.")
    else:
        raise PermissionDenied("You cannot perform this status change.")

    now = timezone.now()
    update_fields = ["status", "updated_at"]
    job.status = new_status

    if pair == (Status.IN_PROGRESS, Status.AWAITING_REVIEW):
        job.submitted_for_review_at = now
        update_fields.append("submitted_for_review_at")
    if pair in REVIEW_TRANSITIONS:
        job.reviewed_by = actor
        job.reviewed_at = now
        job.review_note = note or ""
        update_fields += ["reviewed_by", "reviewed_at", "review_note"]
    if new_status == Status.COMPLETED:
        job.completed_at = now
        update_fields.append("completed_at")

    job.save(update_fields=update_fields)
    record_audit(
        actor=actor, action=Actions.JOB_STATUS_CHANGED, instance=job,
        changes={"status": [current.value, new_status.value]},
        metadata={"note": note} if note else None,
        request=request,
    )

    assignees = [a.staff for a in job.assignments.select_related("staff")]
    if pair == (Status.IN_PROGRESS, Status.AWAITING_REVIEW):
        notify(
            job.manager, NotificationTypes.JOB_SUBMITTED,
            title=f"{job.number} submitted for review",
            message=f"{actor.display_name} submitted '{job.title}' for review.",
            url=job.get_absolute_url(),
        )
    elif pair == (Status.AWAITING_REVIEW, Status.COMPLETED):
        notify(
            assignees, NotificationTypes.JOB_APPROVED,
            title=f"{job.number} approved and completed",
            message=(note or f"'{job.title}' was reviewed and approved by {actor.display_name}."),
            url=job.get_absolute_url(),
        )
    elif pair == (Status.AWAITING_REVIEW, Status.IN_PROGRESS):
        notify(
            assignees, NotificationTypes.JOB_REJECTED,
            title=f"{job.number} was returned after review",
            message=(note or f"'{job.title}' needs more work."),
            url=job.get_absolute_url(),
        )
    return job


# ---------------------------------------------------------------------------
# Comments & attachments
# ---------------------------------------------------------------------------
def add_comment(*, actor, job, body, kind=JobComment.Kind.COMMENT, request=None):
    if not can_progress_job(actor, job):
        raise PermissionDenied("You cannot comment on this job.")
    body = (body or "").strip()
    if not body:
        raise ValidationError("Comment cannot be empty.")
    comment = JobComment.objects.create(job=job, author=actor, kind=kind, body=body)
    record_audit(
        actor=actor, action=Actions.JOB_COMMENTED, instance=job,
        metadata={"kind": kind, "comment_id": comment.pk}, request=request,
    )
    return comment


def add_attachment(*, actor, job, uploaded_file, request=None):
    if not can_progress_job(actor, job):
        raise PermissionDenied("You cannot upload files to this job.")
    validate_upload(uploaded_file)
    safe_name = sanitize_filename(uploaded_file.name)
    uploaded_file.name = safe_name
    attachment = JobAttachment.objects.create(
        job=job,
        uploaded_by=actor,
        file=uploaded_file,
        original_name=safe_name,
        size=uploaded_file.size,
        content_type=getattr(uploaded_file, "content_type", "") or "",
    )
    record_audit(
        actor=actor, action=Actions.JOB_ATTACHMENT_ADDED, instance=job,
        metadata={"file": safe_name, "size": uploaded_file.size}, request=request,
    )
    return attachment


def delete_attachment(*, actor, attachment, request=None):
    job = attachment.job
    is_owner = attachment.uploaded_by_id == actor.pk
    if not (actor.role in (Roles.SUPER_ADMIN, Roles.MANAGER) or is_owner):
        raise PermissionDenied("You cannot delete this attachment.")
    name = attachment.original_name
    attachment.file.delete(save=False)
    attachment.delete()
    record_audit(
        actor=actor, action=Actions.JOB_ATTACHMENT_DELETED, instance=job,
        metadata={"file": name}, request=request,
    )
