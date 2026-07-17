import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.core.models import Department
from apps.core.rbac import Roles
from apps.operations import services as job_services
from apps.operations.models import Job, JobCategory

User = get_user_model()

PASSWORD = "Str0ng!TestPass"


@pytest.fixture(autouse=True)
def _clean_cache():
    """LocMem cache persists across tests; clear it so cached SystemSettings
    and throttle counters never leak between test cases."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def dept_a(db):
    return Department.objects.create(name="Facilities", kind=Department.Kind.INTERNAL)


@pytest.fixture
def dept_b(db):
    return Department.objects.create(name="Acme Client", kind=Department.Kind.CLIENT)


@pytest.fixture
def category(db):
    return JobCategory.objects.create(name="Maintenance", color="#f59e0b")


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser("admin@test.local", PASSWORD, first_name="Ada", last_name="Admin")


@pytest.fixture
def manager(db, dept_a):
    return User.objects.create_user(
        "manager@test.local", PASSWORD, first_name="Mia", last_name="Manager",
        role=Roles.MANAGER, department=dept_a,
    )


@pytest.fixture
def staff_user(db, dept_a):
    return User.objects.create_user(
        "staff@test.local", PASSWORD, first_name="Sam", last_name="Staff",
        role=Roles.STAFF, department=dept_a,
    )


@pytest.fixture
def staff_user2(db, dept_a):
    return User.objects.create_user(
        "staff2@test.local", PASSWORD, first_name="Sue", last_name="Second",
        role=Roles.STAFF, department=dept_a,
    )


@pytest.fixture
def viewer_user(db, dept_a):
    return User.objects.create_user(
        "viewer@test.local", PASSWORD, first_name="Vic", last_name="Viewer",
        role=Roles.VIEWER, department=dept_a,
    )


@pytest.fixture
def viewer_other_dept(db, dept_b):
    return User.objects.create_user(
        "viewer2@test.local", PASSWORD, first_name="Vera", last_name="Other",
        role=Roles.VIEWER, department=dept_b,
    )


def make_job(manager, department, category, *, title="Fix the pump", status=Job.Status.OPEN, **extra):
    data = {
        "title": title,
        "description": "Test job",
        "category": category,
        "priority": extra.pop("priority", Job.Priority.MEDIUM),
        "status": status,
        "department": department,
        "manager": manager,
        "start_date": extra.pop("start_date", None),
        "due_date": extra.pop("due_date", None),
        "estimated_cost": extra.pop("estimated_cost", None),
    }
    return job_services.create_job(actor=manager, data=data, **extra)


@pytest.fixture
def job(db, manager, dept_a, category):
    return make_job(manager, dept_a, category)


@pytest.fixture
def assigned_job(db, job, manager, staff_user):
    job_services.assign_staff(actor=manager, job=job, staff_users=[staff_user])
    job.refresh_from_db()
    return job


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def web_client(client):
    """Django test client with a helper to sign in as a given user."""
    def login_as(user):
        assert client.login(username=user.email, password=PASSWORD)
        return client
    client.login_as = login_as
    return client
