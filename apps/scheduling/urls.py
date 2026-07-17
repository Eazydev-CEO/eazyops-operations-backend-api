from django.urls import path

from . import views

app_name = "scheduling"

urlpatterns = [
    path("", views.ScheduledTaskListView.as_view(), name="task_list"),
    path("create/", views.task_create, name="task_create"),
    path("<int:pk>/", views.task_detail, name="task_detail"),
    path("<int:pk>/edit/", views.task_edit, name="task_edit"),
    path("<int:pk>/status/", views.task_set_status, name="task_set_status"),
    path("my-tasks/", views.MyTasksView.as_view(), name="my_tasks"),
    path("occurrences/<int:pk>/complete/", views.occurrence_complete, name="occurrence_complete"),
    path("runs/", views.SchedulerRunListView.as_view(), name="runs"),
    path("runs/<int:pk>/", views.run_detail, name="run_detail"),
    path("runs/now/", views.run_now, name="run_now"),
]
