from django import forms

from apps.core.models import Department
from apps.core.rbac import Roles

from .models import Job, JobCategory, JobComment

User = None  # resolved lazily to avoid import order issues


def _user_model():
    global User
    if User is None:
        from django.contrib.auth import get_user_model

        User = get_user_model()
    return User


class JobForm(forms.ModelForm):
    tags_raw = forms.CharField(
        label="Tags", required=False,
        help_text="Comma-separated, e.g. hvac, quarterly, site-a",
    )
    staff = forms.ModelMultipleChoiceField(
        label="Assigned staff", queryset=None, required=False,
        help_text="Operations staff who will work this job.",
    )
    initial_status = forms.ChoiceField(
        label="Create as",
        choices=[(Job.Status.DRAFT, "Draft"), (Job.Status.OPEN, "Open (published)")],
        initial=Job.Status.OPEN, required=False,
    )

    class Meta:
        model = Job
        fields = [
            "title", "description", "category", "priority", "department",
            "manager", "start_date", "due_date", "estimated_cost", "actual_cost",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        self.editing = kwargs.pop("editing", False)
        super().__init__(*args, **kwargs)
        UserModel = _user_model()
        self.fields["category"].queryset = JobCategory.objects.filter(is_active=True)
        self.fields["department"].queryset = Department.objects.filter(is_active=True)
        self.fields["manager"].queryset = UserModel.objects.filter(
            is_active=True, role__in=[Roles.SUPER_ADMIN, Roles.MANAGER]
        )
        self.fields["staff"].queryset = UserModel.objects.filter(
            is_active=True, role=Roles.STAFF
        )
        if self.editing:
            self.fields.pop("initial_status")
            self.fields.pop("staff")
            if self.instance.pk:
                self.fields["tags_raw"].initial = ", ".join(
                    self.instance.tags.values_list("name", flat=True)
                )

    def clean(self):
        cleaned = super().clean()
        start, due = cleaned.get("start_date"), cleaned.get("due_date")
        if start and due and due < start:
            self.add_error("due_date", "Due date cannot be before the start date.")
        for field in ("estimated_cost", "actual_cost"):
            value = cleaned.get(field)
            if value is not None and value < 0:
                self.add_error(field, "Cost cannot be negative.")
        return cleaned


class CommentForm(forms.ModelForm):
    class Meta:
        model = JobComment
        fields = ["kind", "body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 3, "placeholder": "Add a comment or progress update…"})}


class AttachmentForm(forms.Form):
    file = forms.FileField(label="File")


class TransitionForm(forms.Form):
    status = forms.ChoiceField(choices=Job.Status.choices)
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class AssignForm(forms.Form):
    staff = forms.ModelMultipleChoiceField(queryset=None, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        UserModel = _user_model()
        self.fields["staff"].queryset = UserModel.objects.filter(is_active=True, role=Roles.STAFF)


class CategoryForm(forms.ModelForm):
    class Meta:
        model = JobCategory
        fields = ["name", "description", "color", "is_active"]
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name", "kind", "description", "contact_email", "is_active"]
