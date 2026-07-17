"""Scheduling engine: recurrence math, materialisation, dedupe, locking."""
from datetime import datetime, time, timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.utils import timezone

from apps.audits.models import Actions, AuditLog
from apps.notifications.models import Notification, NotificationTypes
from apps.operations.models import Job
from apps.scheduling import services
from apps.scheduling.models import (
    ScheduledTask, SchedulerLock, SchedulerRun, TaskOccurrence,
)

pytestmark = pytest.mark.django_db


def make_task(creator, *, frequency=ScheduledTask.Frequency.DAILY, assignees=(), **kwargs):
    defaults = dict(
        name="Boiler walkround",
        action=ScheduledTask.Action.CREATE_OCCURRENCE,
        frequency=frequency,
        starts_at=timezone.now() - timedelta(days=1),
    )
    if frequency in (ScheduledTask.Frequency.DAILY, ScheduledTask.Frequency.WEEKLY,
                     ScheduledTask.Frequency.MONTHLY):
        defaults["time_of_day"] = time(8, 0)
    if frequency == ScheduledTask.Frequency.WEEKLY:
        defaults["weekday"] = 0
    if frequency == ScheduledTask.Frequency.MONTHLY:
        defaults["day_of_month"] = 1
    if frequency == ScheduledTask.Frequency.INTERVAL:
        defaults["interval_minutes"] = 60
    defaults.update(kwargs)
    task = ScheduledTask(**defaults)
    return services.create_scheduled_task(actor=creator, instance=task, assignees=list(assignees))


class TestRecurrenceMath:
    # NOTE: recurrence tests pin starts_at explicitly — slots can never
    # precede a task's start, so relying on the default ("yesterday")
    # would make the expected values drift with the wall clock.

    def test_daily_next_slot(self, manager):
        task = make_task(manager, starts_at=timezone.make_aware(datetime(2026, 1, 1)))
        after = timezone.make_aware(datetime(2026, 7, 5, 9, 0))
        slot = services.next_slot(task, after)
        assert slot == timezone.make_aware(datetime(2026, 7, 6, 8, 0))

    def test_daily_same_day_when_before_time(self, manager):
        task = make_task(manager, starts_at=timezone.make_aware(datetime(2026, 1, 1)))
        after = timezone.make_aware(datetime(2026, 7, 5, 6, 0))
        assert services.next_slot(task, after) == timezone.make_aware(datetime(2026, 7, 5, 8, 0))

    def test_weekly_lands_on_weekday(self, manager):
        task = make_task(manager, frequency=ScheduledTask.Frequency.WEEKLY, weekday=2,
                         starts_at=timezone.make_aware(datetime(2026, 1, 1)))  # Wednesday
        after = timezone.make_aware(datetime(2026, 7, 5, 12, 0))  # a Sunday
        slot = services.next_slot(task, after)
        assert slot == timezone.make_aware(datetime(2026, 7, 8, 8, 0))
        assert slot.weekday() == 2

    def test_monthly_lands_on_day(self, manager):
        task = make_task(manager, frequency=ScheduledTask.Frequency.MONTHLY, day_of_month=15,
                         starts_at=timezone.make_aware(datetime(2026, 1, 1)))
        after = timezone.make_aware(datetime(2026, 7, 20, 0, 0))
        slot = services.next_slot(task, after)
        assert slot == timezone.make_aware(datetime(2026, 8, 15, 8, 0))

    def test_interval_steps_from_anchor(self, manager):
        anchor = timezone.make_aware(datetime(2026, 7, 5, 0, 0))
        task = make_task(manager, frequency=ScheduledTask.Frequency.INTERVAL,
                         interval_minutes=90, starts_at=anchor)
        after = anchor + timedelta(minutes=100)
        assert services.next_slot(task, after) == anchor + timedelta(minutes=180)

    def test_once_only_fires_once(self, manager):
        starts = timezone.now() + timedelta(hours=1)
        task = make_task(manager, frequency=ScheduledTask.Frequency.ONCE, starts_at=starts,
                         time_of_day=None)
        assert task.next_run_at == starts
        assert services.next_slot(task, starts) is None

    def test_ends_at_terminates_series(self, manager):
        task = make_task(manager, ends_at=timezone.now() + timedelta(hours=1))
        far_future = timezone.now() + timedelta(days=30)
        assert services.next_slot(task, far_future) is None

    def test_model_validation_rules(self, manager):
        with pytest.raises(ValidationError):
            make_task(manager, frequency=ScheduledTask.Frequency.WEEKLY, weekday=None)
        with pytest.raises(ValidationError):
            make_task(manager, frequency=ScheduledTask.Frequency.MONTHLY, day_of_month=31)
        with pytest.raises(ValidationError):
            make_task(manager, frequency=ScheduledTask.Frequency.INTERVAL, interval_minutes=1)


class TestEnginePass:
    def test_due_task_materialises_occurrence_per_assignee(self, manager, staff_user, staff_user2):
        task = make_task(manager, assignees=[staff_user, staff_user2])
        ScheduledTask.objects.filter(pk=task.pk).update(
            next_run_at=timezone.now() - timedelta(minutes=5)
        )
        run = services.run_scheduler_pass()
        assert run.status == SchedulerRun.Status.SUCCESS
        assert task.occurrences.count() == 2
        assert Notification.objects.filter(
            user=staff_user, type=NotificationTypes.TASK_DUE
        ).exists()
        task.refresh_from_db()
        assert task.next_run_at > timezone.now()
        assert task.last_run_at is not None

    def test_duplicate_runs_prevented(self, manager, staff_user):
        task = make_task(manager, assignees=[staff_user])
        due = timezone.now() - timedelta(minutes=5)
        ScheduledTask.objects.filter(pk=task.pk).update(next_run_at=due)
        services.run_scheduler_pass()
        count_first = task.occurrences.count()
        # simulate a crashed/duplicate trigger for the same slot
        ScheduledTask.objects.filter(pk=task.pk).update(next_run_at=due)
        services.run_scheduler_pass()
        assert task.occurrences.count() == count_first  # unique constraint held

    def test_team_department_resolves_members(self, manager, staff_user, staff_user2, dept_a):
        task = make_task(manager, team_department=dept_a)
        ScheduledTask.objects.filter(pk=task.pk).update(
            next_run_at=timezone.now() - timedelta(minutes=1)
        )
        services.run_scheduler_pass()
        assignees = set(task.occurrences.values_list("assigned_to__email", flat=True))
        assert assignees == {staff_user.email, staff_user2.email}

    def test_create_job_action(self, manager, staff_user, dept_a, category):
        task = make_task(
            manager,
            action=ScheduledTask.Action.CREATE_JOB,
            assignees=[staff_user],
            job_category=category, job_department=dept_a, job_manager=manager,
            job_priority=Job.Priority.HIGH, job_due_days=3,
        )
        ScheduledTask.objects.filter(pk=task.pk).update(
            next_run_at=timezone.now() - timedelta(minutes=1)
        )
        run = services.run_scheduler_pass()
        assert run.jobs_created == 1
        job = Job.objects.latest("id")
        assert job.category == category
        assert job.status == Job.Status.ASSIGNED
        assert list(job.staff.all()) == [staff_user]
        occurrence = task.occurrences.first()
        assert occurrence.created_job == job

    def test_catchup_is_bounded(self, manager, staff_user):
        task = make_task(manager, frequency=ScheduledTask.Frequency.INTERVAL,
                         interval_minutes=5, assignees=[staff_user],
                         starts_at=timezone.now() - timedelta(days=2))
        ScheduledTask.objects.filter(pk=task.pk).update(
            next_run_at=timezone.now() - timedelta(days=2)
        )
        services.run_scheduler_pass()
        assert task.occurrences.count() <= services.MAX_CATCHUP_SLOTS

    def test_once_task_finishes(self, manager, staff_user):
        task = make_task(manager, frequency=ScheduledTask.Frequency.ONCE,
                         starts_at=timezone.now() - timedelta(minutes=10),
                         time_of_day=None, assignees=[staff_user])
        services.run_scheduler_pass()
        task.refresh_from_db()
        assert task.status == ScheduledTask.Status.FINISHED
        assert task.occurrences.count() == 1
        services.run_scheduler_pass()
        assert task.occurrences.count() == 1

    def test_paused_task_skipped(self, manager, staff_user):
        task = make_task(manager, assignees=[staff_user])
        ScheduledTask.objects.filter(pk=task.pk).update(
            next_run_at=timezone.now() - timedelta(minutes=1),
            status=ScheduledTask.Status.PAUSED,
        )
        services.run_scheduler_pass()
        assert task.occurrences.count() == 0

    def test_missed_occurrences_marked_and_notified(self, manager, staff_user):
        task = make_task(manager, assignees=[staff_user])
        TaskOccurrence.objects.create(
            scheduled_task=task, assigned_to=staff_user,
            scheduled_for=timezone.now() - timedelta(days=3),
        )
        run = services.run_scheduler_pass()
        assert run.occurrences_missed == 1
        assert TaskOccurrence.objects.filter(status=TaskOccurrence.Status.MISSED).count() == 1
        assert Notification.objects.filter(
            user=staff_user, type=NotificationTypes.TASK_MISSED
        ).exists()

    def test_overdue_jobs_flagged_once(self, manager, staff_user, dept_a, category):
        from .conftest import make_job
        from apps.operations import services as job_services

        job = make_job(manager, dept_a, category,
                       due_date=timezone.localdate() - timedelta(days=1))
        job_services.assign_staff(actor=manager, job=job, staff_users=[staff_user])
        run1 = services.run_scheduler_pass()
        assert run1.jobs_marked_overdue == 1
        assert Notification.objects.filter(
            user=staff_user, type=NotificationTypes.JOB_OVERDUE
        ).count() == 1
        run2 = services.run_scheduler_pass()
        assert run2.jobs_marked_overdue == 0  # not re-notified

    def test_run_is_audited_and_logged(self, manager):
        run = services.run_scheduler_pass()
        assert AuditLog.objects.filter(action=Actions.SCHEDULER_EXECUTED).exists()
        assert "Scheduler pass started" in run.log
        assert run.finished_at is not None

    def test_lock_prevents_concurrent_pass(self, manager):
        assert services.acquire_lock() is True
        assert services.run_scheduler_pass() is None  # lock held -> skipped
        services.release_lock()
        assert services.run_scheduler_pass() is not None

    def test_stale_lock_reclaimed(self, manager):
        services.acquire_lock()
        SchedulerLock.objects.filter(pk=1).update(
            locked_at=timezone.now() - timedelta(minutes=services.LOCK_STALE_MINUTES + 5)
        )
        assert services.acquire_lock() is True
        services.release_lock()

    def test_management_command_runs(self, manager, capsys):
        call_command("run_scheduler")
        assert SchedulerRun.objects.count() == 1

    def test_failing_task_records_error_and_notifies_managers(self, manager, staff_user, monkeypatch):
        task = make_task(manager, assignees=[staff_user])
        ScheduledTask.objects.filter(pk=task.pk).update(
            next_run_at=timezone.now() - timedelta(minutes=1)
        )

        def boom(task_id, log):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(services, "_process_single_task", boom)
        run = services.run_scheduler_pass()
        assert run.errors_count == 1
        assert run.status == SchedulerRun.Status.PARTIAL
        task.refresh_from_db()
        assert task.failure_count == 1
        assert "simulated failure" in task.last_error
        assert Notification.objects.filter(
            user=manager, type=NotificationTypes.SCHEDULER_FAILURE
        ).exists()


class TestOccurrenceCompletion:
    def test_assignee_completes(self, manager, staff_user):
        task = make_task(manager, assignees=[staff_user])
        occurrence = TaskOccurrence.objects.create(
            scheduled_task=task, assigned_to=staff_user, scheduled_for=timezone.now()
        )
        services.complete_occurrence(actor=staff_user, occurrence=occurrence, notes="done")
        occurrence.refresh_from_db()
        assert occurrence.status == TaskOccurrence.Status.COMPLETED
        assert occurrence.completed_by == staff_user
        assert AuditLog.objects.filter(action=Actions.OCCURRENCE_COMPLETED).exists()

    def test_other_staff_cannot_complete(self, manager, staff_user, staff_user2):
        task = make_task(manager, assignees=[staff_user])
        occurrence = TaskOccurrence.objects.create(
            scheduled_task=task, assigned_to=staff_user, scheduled_for=timezone.now()
        )
        with pytest.raises(PermissionDenied):
            services.complete_occurrence(actor=staff_user2, occurrence=occurrence)

    def test_double_completion_rejected(self, manager, staff_user):
        task = make_task(manager, assignees=[staff_user])
        occurrence = TaskOccurrence.objects.create(
            scheduled_task=task, assigned_to=staff_user, scheduled_for=timezone.now()
        )
        services.complete_occurrence(actor=staff_user, occurrence=occurrence)
        with pytest.raises(ValidationError):
            services.complete_occurrence(actor=staff_user, occurrence=occurrence)

    def test_pause_resume_audited(self, manager, staff_user):
        task = make_task(manager, assignees=[staff_user])
        services.set_task_status(actor=manager, task=task, status=ScheduledTask.Status.PAUSED)
        assert AuditLog.objects.filter(action=Actions.SCHEDULE_PAUSED).exists()
        services.set_task_status(actor=manager, task=task, status=ScheduledTask.Status.ACTIVE)
        task.refresh_from_db()
        assert task.status == ScheduledTask.Status.ACTIVE
        assert task.next_run_at is not None
