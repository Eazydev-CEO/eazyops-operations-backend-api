"""
Scheduling engine.

``run_scheduler_pass`` is the single entry point used by the
``run_scheduler`` management command (cron) and the dashboard "Run now"
button. A DB-backed lock guarantees only one pass executes at a time;
unique constraints on TaskOccurrence make slot materialisation idempotent.
"""
import logging
import socket
import traceback
from datetime import datetime, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.audits.services import Actions, record_audit
from apps.core.rbac import Roles
from apps.core.services import get_setting_int
from apps.notifications.services import NotificationTypes, notify
from apps.operations.models import Job
from apps.operations.services import assign_staff, create_job

from .models import ScheduledTask, SchedulerLock, SchedulerRun, TaskOccurrence

logger = logging.getLogger("eazyops.scheduler")

LOCK_STALE_MINUTES = 10
MAX_CATCHUP_SLOTS = 10  # per task per pass — bounded backfill after downtime


# ---------------------------------------------------------------------------
# Recurrence math
# ---------------------------------------------------------------------------
def next_slot(task, after):
    """First scheduled moment strictly after ``after`` (tz-aware), or None."""
    tz = timezone.get_current_timezone()
    starts_at = task.starts_at
    floor = max(after, starts_at - timedelta(microseconds=1))

    if task.frequency == ScheduledTask.Frequency.ONCE:
        candidate = starts_at if starts_at > after else None

    elif task.frequency == ScheduledTask.Frequency.INTERVAL:
        step = timedelta(minutes=task.interval_minutes or 5)
        if floor < starts_at:
            candidate = starts_at
        else:
            elapsed = floor - starts_at
            steps = int(elapsed / step) + 1
            candidate = starts_at + steps * step

    else:
        local_floor = timezone.localtime(floor, tz)
        candidate_date = local_floor.date()
        for _ in range(0, 62):  # enough to find any monthly slot
            if task.frequency == ScheduledTask.Frequency.WEEKLY and candidate_date.weekday() != task.weekday:
                candidate_date += timedelta(days=1)
                continue
            if task.frequency == ScheduledTask.Frequency.MONTHLY and candidate_date.day != task.day_of_month:
                candidate_date += timedelta(days=1)
                continue
            naive = datetime.combine(candidate_date, task.time_of_day)
            candidate = timezone.make_aware(naive, tz)
            if candidate > floor:
                break
            candidate_date += timedelta(days=1)
        else:  # pragma: no cover
            candidate = None

    if candidate is None:
        return None
    if task.ends_at and candidate > task.ends_at:
        return None
    return candidate


def initialise_next_run(task):
    task.next_run_at = next_slot(task, timezone.now() - timedelta(microseconds=1))
    if task.next_run_at is None and task.frequency == ScheduledTask.Frequency.ONCE:
        # a once-task whose start already passed still deserves one run
        task.next_run_at = task.starts_at
    return task.next_run_at


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------
def acquire_lock():
    SchedulerLock.objects.get_or_create(pk=1)
    stale_before = timezone.now() - timedelta(minutes=LOCK_STALE_MINUTES)
    owner = f"{socket.gethostname()}"[:120]
    acquired = SchedulerLock.objects.filter(pk=1).filter(
        Q(locked_at__isnull=True) | Q(locked_at__lt=stale_before)
    ).update(locked_at=timezone.now(), locked_by=owner)
    return bool(acquired)


def release_lock():
    SchedulerLock.objects.filter(pk=1).update(locked_at=None, locked_by="")


# ---------------------------------------------------------------------------
# Engine pass
# ---------------------------------------------------------------------------
class _RunLog:
    def __init__(self):
        self.lines = []

    def add(self, message):
        stamp = timezone.now().strftime("%H:%M:%S")
        self.lines.append(f"[{stamp}] {message}")
        logger.info(message)

    def text(self):
        return "\n".join(self.lines)


def run_scheduler_pass(trigger=SchedulerRun.Trigger.COMMAND, user=None):
    """Execute one full scheduler pass. Returns the SchedulerRun row.

    Returns None when another pass holds the lock (duplicate-run guard).
    """
    if not acquire_lock():
        logger.warning("Scheduler pass skipped: lock is held by another process.")
        return None

    run = SchedulerRun.objects.create(triggered_by=trigger, triggered_by_user=user)
    log = _RunLog()
    log.add(f"Scheduler pass started (trigger={trigger}).")
    try:
        _process_due_tasks(run, log)
        _mark_missed_occurrences(run, log)
        _mark_overdue_jobs(run, log)
        _send_due_soon_notices(run, log)
        run.status = SchedulerRun.Status.PARTIAL if run.errors_count else SchedulerRun.Status.SUCCESS
    except Exception as exc:  # defensive: a crashed pass must still be recorded
        run.errors_count += 1
        run.status = SchedulerRun.Status.FAILED
        log.add(f"FATAL: {exc}\n{traceback.format_exc()}")
        logger.exception("Scheduler pass failed")
    finally:
        log.add("Scheduler pass finished.")
        run.finished_at = timezone.now()
        run.log = log.text()
        run.save()
        release_lock()

    record_audit(
        actor=user, action=Actions.SCHEDULER_EXECUTED, instance=run,
        metadata={
            "status": run.status,
            "tasks_processed": run.tasks_processed,
            "occurrences_created": run.occurrences_created,
            "jobs_created": run.jobs_created,
            "errors": run.errors_count,
        },
    )
    return run


def _process_due_tasks(run, log):
    now = timezone.now()
    due_ids = list(
        ScheduledTask.objects.filter(
            status=ScheduledTask.Status.ACTIVE, next_run_at__isnull=False, next_run_at__lte=now
        ).values_list("id", flat=True)
    )
    if not due_ids:
        log.add("No scheduled tasks due.")
    for task_id in due_ids:
        try:
            created_occ, created_jobs = _process_single_task(task_id, log)
            run.tasks_processed += 1
            run.occurrences_created += created_occ
            run.jobs_created += created_jobs
        except Exception as exc:
            run.errors_count += 1
            log.add(f"ERROR task#{task_id}: {exc}")
            logger.exception("Scheduled task %s failed", task_id)
            _record_task_failure(task_id, exc)


def _record_task_failure(task_id, exc):
    try:
        task = ScheduledTask.objects.get(pk=task_id)
        task.failure_count += 1
        task.last_error = f"{timezone.now():%Y-%m-%d %H:%M} {exc}"[:2000]
        task.save(update_fields=["failure_count", "last_error", "updated_at"])
        managers = _active_managers()
        notify(
            managers, NotificationTypes.SCHEDULER_FAILURE,
            title=f"Scheduled task failed: {task.name}",
            message=str(exc)[:500],
            url=task.get_absolute_url(),
        )
    except Exception:  # pragma: no cover
        logger.exception("Could not record failure for task %s", task_id)


def _active_managers():
    from django.contrib.auth import get_user_model

    return list(
        get_user_model().objects.filter(
            is_active=True, role__in=[Roles.SUPER_ADMIN, Roles.MANAGER]
        )
    )


@transaction.atomic
def _process_single_task(task_id, log):
    """Materialise every due slot for one task (bounded catch-up)."""
    task = ScheduledTask.objects.select_for_update().get(pk=task_id)
    if task.status != ScheduledTask.Status.ACTIVE:
        return 0, 0

    now = timezone.now()
    created_occurrences = 0
    created_jobs = 0
    slots = 0

    while task.next_run_at is not None and task.next_run_at <= now and slots < MAX_CATCHUP_SLOTS:
        slot = task.next_run_at
        occ, jobs = _materialise_slot(task, slot, log)
        created_occurrences += occ
        created_jobs += jobs

        task.last_run_at = slot
        task.next_run_at = next_slot(task, slot)
        if task.next_run_at is None:
            task.status = ScheduledTask.Status.FINISHED
        slots += 1

    if slots and task.last_error:
        task.last_error = ""  # healthy again
    task.save(update_fields=["last_run_at", "next_run_at", "status", "last_error", "updated_at"])
    if slots:
        log.add(
            f"Task '{task.name}': {slots} slot(s) processed, "
            f"{created_occurrences} occurrence(s), {created_jobs} job(s)."
        )
    return created_occurrences, created_jobs


def _materialise_slot(task, slot, log):
    assignees = task.resolve_assignees()
    created_occurrences = 0
    created_jobs = 0

    job = None
    if task.action == ScheduledTask.Action.CREATE_JOB:
        job = create_job(
            actor=task.created_by,
            data={
                "title": f"{task.name} — {timezone.localtime(slot):%Y-%m-%d}",
                "description": task.description or f"Auto-created by schedule '{task.name}'.",
                "category": task.job_category,
                "priority": task.job_priority,
                "status": Job.Status.OPEN,
                "department": task.job_department,
                "manager": task.job_manager,
                "start_date": timezone.localtime(slot).date(),
                "due_date": timezone.localtime(slot).date() + timedelta(days=task.job_due_days),
            },
        )
        created_jobs += 1
        if assignees:
            assign_staff(actor=task.job_manager or task.created_by, job=job, staff_users=assignees)

    targets = assignees or [None]
    for assignee in targets:
        try:
            with transaction.atomic():
                TaskOccurrence.objects.create(
                    scheduled_task=task,
                    scheduled_for=slot,
                    assigned_to=assignee,
                    created_job=job,
                )
            created_occurrences += 1
        except IntegrityError:
            # Slot already materialised for this assignee — duplicate-run guard.
            log.add(f"Task '{task.name}': slot {slot:%Y-%m-%d %H:%M} already exists, skipped.")
            continue
        if assignee is not None and task.action == ScheduledTask.Action.CREATE_OCCURRENCE:
            notify(
                assignee, NotificationTypes.TASK_DUE,
                title=f"Scheduled task due: {task.name}",
                message=task.description[:300] if task.description else "A recurring task is due.",
                url="/schedule/my-tasks/",
            )
    return created_occurrences, created_jobs


def _mark_missed_occurrences(run, log):
    grace_hours = get_setting_int("occurrence_missed_grace_hours", 24)
    cutoff = timezone.now() - timedelta(hours=grace_hours)
    stale = TaskOccurrence.objects.filter(
        status=TaskOccurrence.Status.PENDING, scheduled_for__lt=cutoff
    ).select_related("scheduled_task", "assigned_to")
    for occurrence in stale:
        occurrence.status = TaskOccurrence.Status.MISSED
        occurrence.save(update_fields=["status", "updated_at"])
        run.occurrences_missed += 1
        notify(
            occurrence.assigned_to, NotificationTypes.TASK_MISSED,
            title=f"Task missed: {occurrence.scheduled_task.name}",
            message=f"The occurrence scheduled for {occurrence.scheduled_for:%Y-%m-%d %H:%M} was not completed.",
            url="/schedule/my-tasks/",
        )
    if run.occurrences_missed:
        managers = _active_managers()
        notify(
            managers, NotificationTypes.TASK_MISSED,
            title=f"{run.occurrences_missed} scheduled task occurrence(s) missed",
            message="Review the scheduling dashboard for details.",
            url="/schedule/",
        )
        log.add(f"Marked {run.occurrences_missed} occurrence(s) as missed.")


def _mark_overdue_jobs(run, log):
    """Notify once per job when it crosses its due date while active."""
    now = timezone.now()
    overdue = Job.objects.overdue().filter(overdue_notified_at__isnull=True).select_related("manager")
    for job in overdue:
        job.overdue_notified_at = now
        job.save(update_fields=["overdue_notified_at", "updated_at"])
        run.jobs_marked_overdue += 1
        recipients = [a.staff for a in job.assignments.select_related("staff")] + [job.manager]
        notify(
            recipients, NotificationTypes.JOB_OVERDUE,
            title=f"{job.number} is overdue",
            message=f"'{job.title}' was due {job.due_date}.",
            url=job.get_absolute_url(),
        )
    if run.jobs_marked_overdue:
        log.add(f"Flagged {run.jobs_marked_overdue} job(s) overdue.")


def _send_due_soon_notices(run, log):
    days = get_setting_int("job_due_soon_days", 1)
    jobs = Job.objects.due_soon(days).filter(due_soon_notified_at__isnull=True).select_related("manager")
    for job in jobs:
        job.due_soon_notified_at = timezone.now()
        job.save(update_fields=["due_soon_notified_at", "updated_at"])
        run.due_soon_notices += 1
        recipients = [a.staff for a in job.assignments.select_related("staff")] + [job.manager]
        notify(
            recipients, NotificationTypes.JOB_DUE_SOON,
            title=f"{job.number} is due soon",
            message=f"'{job.title}' is due {job.due_date}.",
            url=job.get_absolute_url(),
        )
    if run.due_soon_notices:
        log.add(f"Sent due-soon notices for {run.due_soon_notices} job(s).")


# ---------------------------------------------------------------------------
# Task lifecycle (used by web views and API)
# ---------------------------------------------------------------------------
def create_scheduled_task(*, actor, instance, assignees=None, request=None):
    instance.created_by = actor
    instance.full_clean()
    instance.save()
    if assignees is not None:
        instance.assignees.set(assignees)
    initialise_next_run(instance)
    instance.save(update_fields=["next_run_at", "updated_at"])
    record_audit(
        actor=actor, action=Actions.SCHEDULE_CREATED, instance=instance,
        metadata={"frequency": instance.frequency, "action": instance.action},
        request=request,
    )
    return instance


def update_scheduled_task(*, actor, instance, assignees=None, request=None):
    instance.full_clean()
    instance.save()
    if assignees is not None:
        instance.assignees.set(assignees)
    if instance.status == ScheduledTask.Status.ACTIVE:
        initialise_next_run(instance)
        if instance.next_run_at is None and instance.frequency != ScheduledTask.Frequency.ONCE:
            instance.status = ScheduledTask.Status.FINISHED
        instance.save(update_fields=["next_run_at", "status", "updated_at"])
    record_audit(
        actor=actor, action=Actions.SCHEDULE_UPDATED, instance=instance,
        metadata={"frequency": instance.frequency}, request=request,
    )
    return instance


def set_task_status(*, actor, task, status, request=None):
    if status not in (ScheduledTask.Status.ACTIVE, ScheduledTask.Status.PAUSED):
        raise ValidationError("Only pause/resume is supported.")
    if task.status == status:
        return task
    task.status = status
    if status == ScheduledTask.Status.ACTIVE:
        initialise_next_run(task)
        if task.next_run_at is None:
            task.status = ScheduledTask.Status.FINISHED
    task.save()
    action = Actions.SCHEDULE_RESUMED if status == ScheduledTask.Status.ACTIVE else Actions.SCHEDULE_PAUSED
    record_audit(actor=actor, action=action, instance=task, request=request)
    return task


def complete_occurrence(*, actor, occurrence, notes="", request=None):
    if occurrence.status == TaskOccurrence.Status.COMPLETED:
        raise ValidationError("This occurrence is already completed.")
    is_owner = occurrence.assigned_to_id == actor.pk
    if not (is_owner or actor.role in (Roles.SUPER_ADMIN, Roles.MANAGER)):
        raise PermissionDenied("You can only complete your own scheduled tasks.")
    occurrence.status = TaskOccurrence.Status.COMPLETED
    occurrence.completed_at = timezone.now()
    occurrence.completed_by = actor
    if notes:
        occurrence.notes = notes
    occurrence.save(update_fields=["status", "completed_at", "completed_by", "notes", "updated_at"])
    record_audit(
        actor=actor, action=Actions.OCCURRENCE_COMPLETED, instance=occurrence,
        metadata={"task": occurrence.scheduled_task.name}, request=request,
    )
    return occurrence
