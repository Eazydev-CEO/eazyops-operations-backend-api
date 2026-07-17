from django.urls import path

from . import views

app_name = "operations"

urlpatterns = [
    path("", views.JobListView.as_view(), name="job_list"),
    path("create/", views.job_create, name="job_create"),
    path("export/", views.jobs_export, name="jobs_export"),
    path("<int:pk>/", views.job_detail, name="job_detail"),
    path("<int:pk>/edit/", views.job_edit, name="job_edit"),
    path("<int:pk>/delete/", views.job_delete, name="job_delete"),
    path("<int:pk>/transition/", views.job_transition, name="job_transition"),
    path("<int:pk>/assign/", views.job_assign, name="job_assign"),
    path("<int:pk>/comment/", views.job_comment, name="job_comment"),
    path("<int:pk>/attachments/upload/", views.job_attachment_upload, name="job_attachment_upload"),
    path("attachments/<int:pk>/download/", views.attachment_download, name="attachment_download"),
    path("attachments/<int:pk>/delete/", views.attachment_delete, name="attachment_delete"),

    # Configuration (custom admin area)
    path("manage/categories/", views.CategoryListView.as_view(), name="manage_categories"),
    path("manage/categories/new/", views.category_edit, name="manage_category_create"),
    path("manage/categories/<int:pk>/edit/", views.category_edit, name="manage_category_edit"),
    path("manage/departments/", views.DepartmentListView.as_view(), name="manage_departments"),
    path("manage/departments/new/", views.department_edit, name="manage_department_create"),
    path("manage/departments/<int:pk>/edit/", views.department_edit, name="manage_department_edit"),
]
