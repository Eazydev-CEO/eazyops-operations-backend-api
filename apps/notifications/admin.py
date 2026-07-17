from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "type", "title", "is_read", "email_sent")
    list_filter = ("type", "is_read", "email_sent")
    search_fields = ("title", "user__email")
    readonly_fields = ("created_at",)
