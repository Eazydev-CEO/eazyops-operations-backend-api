from django.contrib import admin

from .models import ScheduledTask, SchedulerLock, SchedulerRun, TaskOccurrence


@admin.register(ScheduledTask)
class ScheduledTaskAdmin(admin.ModelAdmin):
    list_display = ("name", "frequency", "action", "status", "next_run_at", "last_run_at", "failure_count")
    list_filter = ("frequency", "action", "status")
    search_fields = ("name",)
    filter_horizontal = ("assignees",)


@admin.register(TaskOccurrence)
class TaskOccurrenceAdmin(admin.ModelAdmin):
    list_display = ("scheduled_task", "scheduled_for", "assigned_to", "status", "completed_at")
    list_filter = ("status",)
    search_fields = ("scheduled_task__name",)


@admin.register(SchedulerRun)
class SchedulerRunAdmin(admin.ModelAdmin):
    list_display = ("started_at", "finished_at", "status", "triggered_by",
                    "tasks_processed", "occurrences_created", "jobs_created", "errors_count")
    readonly_fields = [f.name for f in SchedulerRun._meta.fields]

    def has_add_permission(self, request):
        return False


admin.site.register(SchedulerLock)
