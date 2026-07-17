from django.contrib import admin

from .models import Job, JobAssignment, JobAttachment, JobCategory, JobComment, JobSequence, Tag


class JobAssignmentInline(admin.TabularInline):
    model = JobAssignment
    extra = 0


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("number", "title", "status", "priority", "category", "department", "manager", "due_date")
    list_filter = ("status", "priority", "category", "department")
    search_fields = ("number", "title", "description")
    readonly_fields = ("number", "completed_at", "submitted_for_review_at", "reviewed_by", "reviewed_at")
    inlines = [JobAssignmentInline]
    date_hierarchy = "created_at"


@admin.register(JobCategory)
class JobCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "color", "is_active")
    search_fields = ("name",)


@admin.register(JobComment)
class JobCommentAdmin(admin.ModelAdmin):
    list_display = ("job", "author", "kind", "created_at")
    search_fields = ("job__number", "body")


@admin.register(JobAttachment)
class JobAttachmentAdmin(admin.ModelAdmin):
    list_display = ("job", "original_name", "size", "uploaded_by", "created_at")
    search_fields = ("job__number", "original_name")


admin.site.register(Tag)
admin.site.register(JobSequence)
