from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from apps.core.models import Department
from apps.core.rbac import Roles
from apps.core.validators import validate_avatar

from . import services
from .models import User


class LoginForm(AuthenticationForm):
    """Standard auth form + lockout gate checked *before* authenticate()."""

    username = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email",
                                       "placeholder": "you@company.com"}),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Invalid email address or password.",
        "locked_out": "Too many failed attempts. Try again in a few minutes.",
    }

    def clean(self):
        email = (self.cleaned_data.get("username") or "").lower()
        ip, _ = services.client_meta(self.request)
        if email and services.is_locked_out(email=email, ip_address=ip):
            raise ValidationError(self.error_messages["locked_out"], code="locked_out")
        return super().clean()


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "phone", "photo", "email_notifications"]

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo and hasattr(photo, "content_type"):  # only validate fresh uploads
            validate_avatar(photo)
        return photo


class InviteForm(forms.Form):
    email = forms.EmailField()
    role = forms.ChoiceField(choices=Roles.choices, initial=Roles.STAFF)
    department = forms.ModelChoiceField(
        queryset=Department.objects.filter(is_active=True), required=False
    )
    job_title = forms.CharField(max_length=120, required=False)

    def __init__(self, *args, inviter=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.inviter = inviter
        if inviter is not None and not inviter.is_super_admin:
            self.fields["role"].choices = [
                (value, label) for value, label in Roles.choices if value != Roles.SUPER_ADMIN
            ]

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise ValidationError("A user with this email already exists.")
        return email


class AcceptInvitationForm(forms.Form):
    first_name = forms.CharField(max_length=80)
    last_name = forms.CharField(max_length=80)
    phone = forms.CharField(max_length=32, required=False)
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        elif p1:
            validate_password(p1)
        return cleaned


class UserManageForm(forms.ModelForm):
    """Admin-area edit form (Super Admin only)."""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "role", "department", "job_title", "phone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = Department.objects.filter(is_active=True)
        self.fields["department"].required = False
