"""Module-level choice aliases for drf-spectacular's ENUM_NAME_OVERRIDES.

import_string cannot traverse nested TextChoices classes
(e.g. ``…models.Job.Status.choices``), so the schema settings point here.
"""
from apps.operations.models import Job
from apps.scheduling.models import ScheduledTask, SchedulerRun, TaskOccurrence

JOB_STATUS_CHOICES = Job.Status.choices
JOB_PRIORITY_CHOICES = Job.Priority.choices
SCHEDULED_TASK_STATUS_CHOICES = ScheduledTask.Status.choices
TASK_OCCURRENCE_STATUS_CHOICES = TaskOccurrence.Status.choices
SCHEDULER_RUN_STATUS_CHOICES = SchedulerRun.Status.choices
