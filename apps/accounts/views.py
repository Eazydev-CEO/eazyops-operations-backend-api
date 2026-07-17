from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from apps.audits.services import Actions, record_audit
from apps.core.models import Department
from apps.core.rbac import CapabilityRequiredMixin, Roles, require_capability

from . import services
from .forms import AcceptInvitationForm, InviteForm, LoginForm, ProfileForm, UserManageForm
from .models import Invitation, User


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    next_page = reverse_lazy("accounts:login")


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:profile")

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(
            actor=self.request.user, action=Actions.PASSWORD_CHANGED,
            instance=self.request.user, request=self.request,
        )
        messages.success(self.request, "Your password has been changed.")
        return response


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/emails/password_reset_email.txt"
    subject_template_name = "accounts/emails/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
@login_required
def profile(request):
    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            changed = list(form.changed_data)
            form.save()
            if changed:
                record_audit(
                    actor=request.user, action=Actions.USER_UPDATED,
                    instance=request.user,
                    metadata={"fields": changed, "self_service": True},
                    request=request,
                )
            messages.success(request, "Profile updated.")
            return redirect("accounts:profile")
        messages.error(request, "Please fix the errors below.")
    else:
        form = ProfileForm(instance=request.user)
    return render(request, "accounts/profile.html", {"form": form})


# ---------------------------------------------------------------------------
# Invitation acceptance (public)
# ---------------------------------------------------------------------------
def accept_invitation(request, token):
    invitation = Invitation.objects.filter(token=token).select_related("department").first()
    if invitation is None or not invitation.is_usable:
        return render(request, "accounts/invitation_invalid.html", status=410)

    if request.method == "POST":
        form = AcceptInvitationForm(request.POST)
        if form.is_valid():
            try:
                user = services.accept_invitation(
                    invitation=invitation,
                    first_name=form.cleaned_data["first_name"],
                    last_name=form.cleaned_data["last_name"],
                    phone=form.cleaned_data.get("phone", ""),
                    password=form.cleaned_data["password1"],
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                auth_login(request, user)
                messages.success(request, f"Welcome aboard, {user.get_short_name()}!")
                return redirect("dashboard:home")
    else:
        form = AcceptInvitationForm()
    return render(request, "accounts/invitation_accept.html", {"form": form, "invitation": invitation})


# ---------------------------------------------------------------------------
# Administration — user management (custom admin area)
# ---------------------------------------------------------------------------
class UserListView(CapabilityRequiredMixin, ListView):
    capability = "users.view"
    template_name = "accounts/manage/user_list.html"
    context_object_name = "users"
    paginate_by = 20

    def get_queryset(self):
        qs = User.objects.select_related("department").order_by("first_name", "last_name")
        q = self.request.GET.get("q", "").strip()
        role = self.request.GET.get("role", "")
        dept = self.request.GET.get("department", "")
        status = self.request.GET.get("status", "")
        if q:
            qs = qs.filter(
                Q(first_name__icontains=q) | Q(last_name__icontains=q)
                | Q(email__icontains=q) | Q(job_title__icontains=q)
            )
        if role in Roles.values:
            qs = qs.filter(role=role)
        if dept.isdigit():
            qs = qs.filter(department_id=dept)
        if status == "active":
            qs = qs.filter(is_active=True)
        elif status == "inactive":
            qs = qs.filter(is_active=False)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["roles"] = Roles.choices
        ctx["departments"] = Department.objects.filter(is_active=True)
        ctx["filters"] = {
            "q": self.request.GET.get("q", ""),
            "role": self.request.GET.get("role", ""),
            "department": self.request.GET.get("department", ""),
            "status": self.request.GET.get("status", ""),
        }
        return ctx


@login_required
def user_edit(request, pk):
    require_capability(request.user, "users.manage")
    target = get_object_or_404(User.objects.select_related("department"), pk=pk)
    if request.method == "POST":
        form = UserManageForm(request.POST, instance=target)
        if form.is_valid():
            try:
                services.update_user_management_fields(
                    actor=request.user, user=target, cleaned=form.cleaned_data, request=request
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"{target.display_name} updated.")
                return redirect("accounts:manage_users")
    else:
        form = UserManageForm(instance=target)
    return render(request, "accounts/manage/user_edit.html", {"form": form, "target": target})


@login_required
@require_POST
def user_set_active(request, pk):
    require_capability(request.user, "users.manage")
    target = get_object_or_404(User, pk=pk)
    active = request.POST.get("active") == "1"
    try:
        services.set_user_active(actor=request.user, user=target, active=active, request=request)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        verb = "reactivated" if active else "deactivated"
        messages.success(request, f"{target.display_name} has been {verb}.")
    return redirect("accounts:manage_users")


class InvitationListView(CapabilityRequiredMixin, ListView):
    capability = "users.manage"
    template_name = "accounts/manage/invitation_list.html"
    context_object_name = "invitations"
    paginate_by = 20

    def get_queryset(self):
        return Invitation.objects.select_related("invited_by", "department", "created_user")


@login_required
def invite_user(request):
    require_capability(request.user, "users.manage")
    if request.method == "POST":
        form = InviteForm(request.POST, inviter=request.user)
        if form.is_valid():
            try:
                invitation = services.create_invitation(
                    inviter=request.user,
                    email=form.cleaned_data["email"],
                    role=form.cleaned_data["role"],
                    department=form.cleaned_data.get("department"),
                    job_title=form.cleaned_data.get("job_title", ""),
                    request=request,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Invitation sent to {invitation.email}.")
                return redirect("accounts:manage_invitations")
    else:
        form = InviteForm(inviter=request.user)
    return render(request, "accounts/manage/invite_form.html", {"form": form})


@login_required
@require_POST
def invitation_resend(request, pk):
    require_capability(request.user, "users.manage")
    invitation = get_object_or_404(Invitation, pk=pk)
    try:
        services.resend_invitation(invitation=invitation, actor=request.user, request=request)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, f"Invitation re-sent to {invitation.email}.")
    return redirect("accounts:manage_invitations")


@login_required
@require_POST
def invitation_cancel(request, pk):
    require_capability(request.user, "users.manage")
    invitation = get_object_or_404(Invitation, pk=pk)
    try:
        services.cancel_invitation(invitation=invitation, actor=request.user, request=request)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, f"Invitation to {invitation.email} cancelled.")
    return redirect("accounts:manage_invitations")
