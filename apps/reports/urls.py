from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.index, name="index"),
    path("status/", views.jobs_by_status, name="status"),
    path("category/", views.jobs_by_category, name="category"),
    path("priority/", views.jobs_by_priority, name="priority"),
    path("overdue/", views.overdue_report, name="overdue"),
    path("workload/", views.workload_report, name="workload"),
    path("performance/", views.performance_report, name="performance"),
    path("completion-time/", views.completion_time_report, name="completion_time"),
    path("scheduled/", views.scheduled_report, name="scheduled"),
    path("audit/", views.audit_report, name="audit"),
]
