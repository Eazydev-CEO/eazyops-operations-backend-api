"""
Run one scheduler pass. Designed for cron (or Windows Task Scheduler).

Examples:
    python manage.py run_scheduler
    */5 * * * *  cd /srv/eazyops && .venv/bin/python manage.py run_scheduler >> /var/log/eazyops/scheduler.log 2>&1

The pass is safe to schedule aggressively:
  * a DB lock skips overlapping passes,
  * unique constraints keep slot materialisation idempotent,
  * catch-up after downtime is bounded per task.
"""
from django.core.management.base import BaseCommand

from apps.scheduling.models import SchedulerRun
from apps.scheduling.services import run_scheduler_pass


class Command(BaseCommand):
    help = "Execute one EazyOps scheduler pass (recurring tasks, overdue/due-soon sweeps)."

    def handle(self, *args, **options):
        run = run_scheduler_pass(trigger=SchedulerRun.Trigger.COMMAND)
        if run is None:
            self.stdout.write(self.style.WARNING(
                "Skipped: another scheduler pass currently holds the lock."
            ))
            return
        summary = (
            f"Run #{run.pk} [{run.status}] — tasks={run.tasks_processed} "
            f"occurrences={run.occurrences_created} jobs={run.jobs_created} "
            f"missed={run.occurrences_missed} overdue={run.jobs_marked_overdue} "
            f"due_soon={run.due_soon_notices} errors={run.errors_count}"
        )
        style = self.style.SUCCESS if run.status == SchedulerRun.Status.SUCCESS else self.style.WARNING
        self.stdout.write(style(summary))
        if run.errors_count:
            self.stderr.write(run.log)
