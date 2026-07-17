from django import forms

from apps.core.rbac import Roles

from .models import ScheduledTask, TaskOccurrence


class ScheduledTaskForm(forms.ModelForm):
    class Meta:
        model = ScheduledTask
        fields = [
            "name", "description", "action",
            "frequency", "time_of_day", "weekday", "day_of_month",
            "interval_minutes", "starts_at", "ends_at",
            "assignees", "team_department",
            "job_category", "job_department", "job_manager", "job_priority", "job_due_days",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "time_of_day": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, **kwargs):
        from django.contrib.auth import get_user_model

        super().__init__(*args, **kwargs)
        User = get_user_model()
        self.fields["assignees"].queryset = User.objects.filter(is_active=True, role=Roles.STAFF)
        self.fields["assignees"].required = False
        self.fields["job_manager"].queryset = User.objects.filter(
            is_active=True, role__in=[Roles.SUPER_ADMIN, Roles.MANAGER]
        )
        for name in ("time_of_day", "weekday", "day_of_month", "interval_minutes",
                     "ends_at", "team_department", "job_category", "job_department",
                     "job_manager"):
            self.fields[name].required = False


class CompleteOccurrenceForm(forms.Form):
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Completion notes (optional)"}),
    )


class OccurrenceFilterForm(forms.Form):
    status = forms.ChoiceField(
        required=False, choices=[("", "All statuses")] + list(TaskOccurrence.Status.choices)
    )
