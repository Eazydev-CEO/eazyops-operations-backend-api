from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import FailedLoginAttempt, Invitation, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "role", "department", "is_active", "last_login")
    list_filter = ("role", "is_active", "department")
    search_fields = ("email", "first_name", "last_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("first_name", "last_name", "phone", "photo",
                                 "department", "job_title", "email_notifications")}),
        ("Access", {"fields": ("role", "is_active", "is_staff", "is_superuser")}),
        ("Dates", {"fields": ("last_login", "date_joined", "last_activity_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "password1", "password2", "role")}),
    )
    readonly_fields = ("last_login", "date_joined", "last_activity_at")


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "role", "invited_by", "expires_at", "status")
    search_fields = ("email",)
    readonly_fields = ("token", "accepted_at", "cancelled_at", "created_user")


@admin.register(FailedLoginAttempt)
class FailedLoginAttemptAdmin(admin.ModelAdmin):
    list_display = ("email", "ip_address", "created_at")
    search_fields = ("email", "ip_address")
    readonly_fields = ("email", "ip_address", "user_agent", "created_at")

    def has_add_permission(self, request):
        return False
