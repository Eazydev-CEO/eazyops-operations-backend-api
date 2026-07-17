"""
Seed realistic demo data (development only).

    python manage.py seed_demo            # add demo data
    python manage.py seed_demo --flush    # wipe demo objects first
    python manage.py seed_demo --run-scheduler   # also execute one scheduler pass

Safety: refuses to run when DEBUG=False unless --force is passed, so demo
data can never land in production by accident. Jobs are created through the
real service layer, so the audit trail, notifications and status machine
behave exactly as they would in live usage.
"""
import random
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Department
from apps.core.rbac import Roles
from apps.notifications.models import Notification
from apps.operations import services as job_services
from apps.operations.models import Job, JobCategory
from apps.scheduling import services as sched_services
from apps.scheduling.models import ScheduledTask, SchedulerRun, TaskOccurrence

DEMO_PASSWORD = "DemoPass123!"

rng = random.Random(42)


class Command(BaseCommand):
    help = "Create optional, realistic demo data for local evaluation (never in production)."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true",
                            help="Delete demo jobs/schedules/users first (audit history is kept).")
        parser.add_argument("--force", action="store_true",
                            help="Allow seeding even when DEBUG=False. Use with care.")
        parser.add_argument("--run-scheduler", action="store_true",
                            help="Execute one scheduler pass after seeding.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to seed demo data with DEBUG=False. Pass --force if you really mean it."
            )

        with transaction.atomic():
            if options["flush"]:
                self._flush()
            departments = self._departments()
            categories = self._categories()
            users = self._users(departments)
            self._jobs(users, departments, categories)
            self._schedules(users, departments, categories)

        if options["run_scheduler"]:
            run = sched_services.run_scheduler_pass(trigger=SchedulerRun.Trigger.COMMAND)
            if run:
                self.stdout.write(self.style.SUCCESS(f"Scheduler pass #{run.pk}: {run.status}"))

        self.stdout.write(self.style.SUCCESS("\nDemo data ready. Accounts (password for all: "
                                             f"{DEMO_PASSWORD}):"))
        for email, role in [
            ("admin@eazyops.local", "Super Admin"),
            ("manager@eazyops.local", "Operations Manager"),
            ("staff1@eazyops.local", "Operations Staff"),
            ("staff2@eazyops.local", "Operations Staff"),
            ("staff3@eazyops.local", "Operations Staff"),
            ("staff4@eazyops.local", "Operations Staff"),
            ("viewer@eazyops.local", "Viewer / Client (Acme Logistics)"),
        ]:
            self.stdout.write(f"  {email:32s} {role}")

    # ------------------------------------------------------------------
    def _flush(self):
        self.stdout.write("Flushing previous demo data (audit log is immutable and kept)…")
        TaskOccurrence.objects.all().delete()
        ScheduledTask.objects.all().delete()
        SchedulerRun.objects.all().delete()
        Job.objects.all().delete()
        Notification.objects.all().delete()
        User.objects.filter(email__endswith="@eazyops.local").exclude(
            is_superuser=True, email="admin@eazyops.local"
        ).delete()
        JobCategory.objects.all().delete()
        Department.objects.all().delete()

    def _departments(self):
        specs = [
            ("Facilities", Department.Kind.INTERNAL, "Building maintenance and facilities team."),
            ("IT Operations", Department.Kind.INTERNAL, "Infrastructure, networks and devices."),
            ("Acme Logistics", Department.Kind.CLIENT, "Contracted warehouse & fleet client."),
            ("Northwind Retail", Department.Kind.CLIENT, "Retail chain — store maintenance contract."),
        ]
        result = {}
        for name, kind, description in specs:
            dept, _ = Department.objects.get_or_create(
                name=name, defaults={"kind": kind, "description": description,
                                     "contact_email": f"contact@{name.split()[0].lower()}.example"}
            )
            result[name] = dept
        return result

    def _categories(self):
        specs = [
            ("Maintenance", "#f59e0b", "Preventive and corrective maintenance work."),
            ("Inspection", "#38bdf8", "Safety and compliance inspections."),
            ("Installation", "#6366f1", "New equipment installs and upgrades."),
            ("Cleaning", "#22c55e", "Deep cleaning and sanitation."),
            ("IT Support", "#a855f7", "Hardware, network and software jobs."),
            ("Delivery", "#ef4444", "Time-critical transport and delivery."),
        ]
        result = {}
        for name, color, description in specs:
            cat, _ = JobCategory.objects.get_or_create(
                name=name, defaults={"color": color, "description": description}
            )
            result[name] = cat
        return result

    def _user(self, email, role, first, last, dept=None, title="", superuser=False):
        user = User.objects.filter(email=email).first()
        if user:
            return user
        kwargs = dict(first_name=first, last_name=last, role=role,
                      department=dept, job_title=title, phone=f"+1-555-{rng.randint(1000, 9999)}")
        if superuser:
            return User.objects.create_superuser(email, DEMO_PASSWORD, **kwargs)
        return User.objects.create_user(email, DEMO_PASSWORD, **kwargs)

    def _users(self, departments):
        return {
            "admin": self._user("admin@eazyops.local", Roles.SUPER_ADMIN, "Ada", "Okafor",
                                title="Platform Administrator", superuser=True),
            "manager": self._user("manager@eazyops.local", Roles.MANAGER, "Malik", "Johnson",
                                  departments["Facilities"], "Operations Manager"),
            "staff1": self._user("staff1@eazyops.local", Roles.STAFF, "Sofia", "Reyes",
                                 departments["Facilities"], "Field Technician"),
            "staff2": self._user("staff2@eazyops.local", Roles.STAFF, "Tunde", "Adeyemi",
                                 departments["Facilities"], "Maintenance Technician"),
            "staff3": self._user("staff3@eazyops.local", Roles.STAFF, "Priya", "Sharma",
                                 departments["IT Operations"], "IT Support Engineer"),
            "staff4": self._user("staff4@eazyops.local", Roles.STAFF, "Ethan", "Brooks",
                                 departments["Facilities"], "Cleaning Supervisor"),
            "viewer": self._user("viewer@eazyops.local", Roles.VIEWER, "Grace", "Lee",
                                 departments["Acme Logistics"], "Client Coordinator"),
        }

    # ------------------------------------------------------------------
    def _jobs(self, users, departments, categories):
        if Job.objects.exists():
            self.stdout.write("Jobs already present — skipping job seed (use --flush to reset).")
            return
        manager, admin = users["manager"], users["admin"]
        staff = [users["staff1"], users["staff2"], users["staff3"], users["staff4"]]
        today = timezone.localdate()

        def mk(title, category, dept, priority, *, staff_users=(), desc="", due_in=None,
               start_ago=0, estimated=None, tags=""):
            data = {
                "title": title,
                "description": desc or f"{title}. Scope agreed with {dept.name}.",
                "category": categories[category],
                "priority": priority,
                "status": Job.Status.OPEN,
                "department": dept,
                "manager": manager,
                "start_date": today - timedelta(days=start_ago),
                "due_date": (today + timedelta(days=due_in)) if due_in is not None else None,
                "estimated_cost": estimated,
            }
            job = job_services.create_job(actor=manager, data=data, tags_raw=tags)
            if staff_users:
                job_services.assign_staff(actor=manager, job=job, staff_users=list(staff_users))
            return job

        def progress(job, actor):
            job_services.transition_job(actor=actor, job=job, new_status=Job.Status.IN_PROGRESS)

        def complete(job, actor, days_ago_completed, actual=None, note="Verified on site."):
            job_services.transition_job(actor=actor, job=job, new_status=Job.Status.AWAITING_REVIEW)
            job_services.transition_job(actor=manager, job=job, new_status=Job.Status.COMPLETED, note=note)
            completed_at = timezone.now() - timedelta(days=days_ago_completed)
            Job.objects.filter(pk=job.pk).update(
                completed_at=completed_at,
                created_at=completed_at - timedelta(days=rng.randint(3, 12)),
                actual_cost=actual,
            )

        # Drafts
        for title, cat in [("Draft: replace lobby lighting with LED", "Installation"),
                           ("Draft: Q3 pest control contract scope", "Maintenance"),
                           ("Draft: new starter laptop provisioning batch", "IT Support")]:
            job = job_services.create_job(actor=manager, data={
                "title": title, "description": "Scope pending approval.",
                "category": categories[cat], "priority": Job.Priority.LOW,
                "status": Job.Status.DRAFT, "department": departments["Facilities"],
                "manager": manager, "start_date": None, "due_date": None,
            })

        # Open (unassigned)
        mk("Quarterly HVAC filter replacement — Building A", "Maintenance",
           departments["Facilities"], Job.Priority.MEDIUM, due_in=6, estimated=450, tags="hvac, quarterly")
        mk("Fire extinguisher compliance inspection", "Inspection",
           departments["Northwind Retail"], Job.Priority.HIGH, due_in=3, estimated=300, tags="safety, compliance")
        mk("Warehouse dock door seal replacement", "Maintenance",
           departments["Acme Logistics"], Job.Priority.MEDIUM, due_in=10, estimated=1200, tags="warehouse")
        mk("Client office deep clean — floor 4", "Cleaning",
           departments["Northwind Retail"], Job.Priority.LOW, due_in=14, estimated=600)

        # Assigned
        mk("Server room temperature sensor install", "Installation",
           departments["IT Operations"], Job.Priority.HIGH,
           staff_users=[users["staff3"]], due_in=5, estimated=900, tags="server-room, sensors")
        mk("Rooftop drainage inspection after storm", "Inspection",
           departments["Facilities"], Job.Priority.URGENT,
           staff_users=[users["staff1"], users["staff2"]], due_in=1, estimated=250, tags="roof, storm")
        mk("Forklift battery bay ventilation check", "Inspection",
           departments["Acme Logistics"], Job.Priority.MEDIUM,
           staff_users=[users["staff2"]], due_in=7, estimated=180)
        mk("Meeting room AV refresh — HQ", "Installation",
           departments["IT Operations"], Job.Priority.LOW,
           staff_users=[users["staff3"]], due_in=20, estimated=3200, tags="av, hq")

        # In progress (2 overdue)
        j = mk("Elevator B annual service", "Maintenance", departments["Facilities"],
               Job.Priority.HIGH, staff_users=[users["staff1"]], due_in=4,
               start_ago=3, estimated=2100, tags="elevator, annual")
        progress(j, users["staff1"])
        j = mk("Loading bay lighting fault repair", "Maintenance", departments["Acme Logistics"],
               Job.Priority.URGENT, staff_users=[users["staff2"]], due_in=-2,
               start_ago=6, estimated=500, tags="electrical")
        progress(j, users["staff2"])
        j = mk("Store 14 POS network drop replacement", "IT Support", departments["Northwind Retail"],
               Job.Priority.HIGH, staff_users=[users["staff3"]], due_in=-1,
               start_ago=5, estimated=750, tags="network, pos")
        progress(j, users["staff3"])
        j = mk("Stairwell repaint — north tower", "Maintenance", departments["Facilities"],
               Job.Priority.LOW, staff_users=[users["staff4"]], due_in=12,
               start_ago=2, estimated=1400)
        progress(j, users["staff4"])
        j = mk("Cold storage door gasket replacement", "Maintenance", departments["Acme Logistics"],
               Job.Priority.MEDIUM, staff_users=[users["staff2"]], due_in=8,
               start_ago=1, estimated=650, tags="cold-storage")
        progress(j, users["staff2"])

        # Awaiting review
        j = mk("Emergency exit signage audit", "Inspection", departments["Facilities"],
               Job.Priority.MEDIUM, staff_users=[users["staff1"]], due_in=2,
               start_ago=4, estimated=200, tags="safety")
        progress(j, users["staff1"])
        job_services.transition_job(actor=users["staff1"], job=j, new_status=Job.Status.AWAITING_REVIEW)
        job_services.add_comment(actor=users["staff1"], job=j,
                                 body="All floors audited, three signs replaced. Photos attached offline.",
                                 kind="PROGRESS")
        j = mk("UPS battery health check — data closet", "IT Support", departments["IT Operations"],
               Job.Priority.HIGH, staff_users=[users["staff3"]], due_in=1,
               start_ago=2, estimated=350)
        progress(j, users["staff3"])
        job_services.transition_job(actor=users["staff3"], job=j, new_status=Job.Status.AWAITING_REVIEW)

        # Completed (spread across past weeks for the trend chart)
        specs = [
            ("Generator monthly load test", "Maintenance", departments["Facilities"],
             [users["staff2"]], 2, 900, 940),
            ("Office 365 tenant security review", "IT Support", departments["IT Operations"],
             [users["staff3"]], 9, 1200, 1150),
            ("Parking lot line repaint", "Maintenance", departments["Facilities"],
             [users["staff4"]], 16, 2400, 2600),
            ("Client site CCTV camera swap", "Installation", departments["Acme Logistics"],
             [users["staff1"]], 23, 1800, 1750),
            ("Air quality certification sampling", "Inspection", departments["Northwind Retail"],
             [users["staff1"], users["staff2"]], 30, 700, 700),
        ]
        for title, cat, dept, assignees, days_ago, est, actual in specs:
            j = mk(title, cat, dept, Job.Priority.MEDIUM, staff_users=assignees,
                   due_in=None, start_ago=days_ago + 5, estimated=est)
            Job.objects.filter(pk=j.pk).update(due_date=today - timedelta(days=days_ago - 2))
            j.refresh_from_db()
            progress(j, assignees[0])
            complete(j, assignees[0], days_ago, actual=actual)

        # Cancelled
        for title in ["Duplicate: dock door seal replacement", "Postponed: atrium plant wall install"]:
            j = mk(title, "Maintenance", departments["Facilities"], Job.Priority.LOW, due_in=9)
            job_services.transition_job(actor=manager, job=j, new_status=Job.Status.CANCELLED,
                                        note="No longer required.")

        # A couple of comments from the manager for texture
        sample = Job.objects.filter(status=Job.Status.IN_PROGRESS).first()
        if sample:
            job_services.add_comment(actor=manager, job=sample,
                                     body="Client asked for completion before Friday — please prioritise.")
        self.stdout.write(f"  jobs: {Job.objects.count()}")

    # ------------------------------------------------------------------
    def _schedules(self, users, departments, categories):
        if ScheduledTask.objects.exists():
            self.stdout.write("Scheduled tasks already present — skipping (use --flush to reset).")
            return
        manager = users["manager"]
        now = timezone.now()

        def mk(**kwargs):
            assignees = kwargs.pop("assignees", [])
            task = ScheduledTask(**kwargs)
            sched_services.create_scheduled_task(actor=manager, instance=task, assignees=assignees)
            return task

        mk(name="Daily boiler room walkround",
           description="Check pressure gauges, note anomalies, confirm no leaks.",
           action=ScheduledTask.Action.CREATE_OCCURRENCE,
           frequency=ScheduledTask.Frequency.DAILY,
           time_of_day="08:00", starts_at=now - timedelta(days=1),
           assignees=[users["staff1"], users["staff2"]])

        mk(name="Weekly fire safety walk",
           description="Full-building fire safety walk with checklist.",
           action=ScheduledTask.Action.CREATE_OCCURRENCE,
           frequency=ScheduledTask.Frequency.WEEKLY,
           weekday=0, time_of_day="09:00", starts_at=now - timedelta(days=7),
           team_department=departments["Facilities"])

        mk(name="Monthly generator service job",
           description="Creates a full work order for the monthly generator service.",
           action=ScheduledTask.Action.CREATE_JOB,
           frequency=ScheduledTask.Frequency.MONTHLY,
           day_of_month=1, time_of_day="07:30", starts_at=now - timedelta(days=10),
           job_category=categories["Maintenance"],
           job_department=departments["Facilities"],
           job_manager=manager, job_priority="HIGH", job_due_days=5,
           assignees=[users["staff2"]])

        mk(name="Network backup verification",
           description="Verify overnight backup jobs completed and spot-check restores.",
           action=ScheduledTask.Action.CREATE_OCCURRENCE,
           frequency=ScheduledTask.Frequency.INTERVAL,
           interval_minutes=360, starts_at=now - timedelta(hours=2),
           assignees=[users["staff3"]])

        self.stdout.write(f"  scheduled tasks: {ScheduledTask.objects.count()}")
