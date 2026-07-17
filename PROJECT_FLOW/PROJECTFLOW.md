# PROJECTFLOW — EazyOps Operations Backend & API

Living document: how the system flows end-to-end, and the build progress log.

## 1. Product flow (user journey)

```
Super Admin seeds platform (createsuperuser / seed_demo)
        │
        ├─► Configure: departments & clients · job categories · system settings
        │
        ├─► Invite team (email token, expiry, role pre-assigned)   [Invitations]
        │       └─ invitee sets name+password → account created → audited
        │
        ├─► Manager creates JOB (auto number JOB-YYYY-NNNNN)       [Jobs]
        │     Draft ──publish──► Open ──assign staff──► Assigned (auto)
        │       │                                          │
        │       │                              staff: Start work
        │       │                                          ▼
        │       │                                     In Progress ──staff: submit──► Awaiting Review
        │       │                                          ▲                            │
        │       │                                          └────manager: reject─────────┤
        │       │                                                manager: approve       ▼
        │       └────────────────cancel (manager+)──────────► Cancelled     Completed (costs, review note)
        │
        │     due date passes while active ⇒ effective status OVERDUE (derived,
        │     scheduler notifies assignees+manager once, dashboards/report filters pick it up)
        │
        ├─► Staff work their queue: My Jobs · comments/progress · evidence uploads
        ├─► Viewer/Client (read-only, scoped to own department's non-draft jobs)
        │
        ├─► Recurring work via SCHEDULED TASKS                     [Scheduling]
        │     template (once/daily/weekly/monthly/interval, staff/team,
        │     checklist-occurrence or full-job action)
        │       └─ cron: manage.py run_scheduler → occurrences per assignee
        │          (+notifications), auto job creation, missed sweep,
        │          overdue/due-soon sweeps — all logged per run
        │
        └─► Observe: Dashboard KPIs + charts (AJAX range) · Reports (+CSV)
             · Notifications (bell/email) · Audit Trail · System Health
```

## 2. Scheduler pass (one execution)

```
run_scheduler (cron / "Run now" button)
  ├─ acquire SchedulerLock row (atomic UPDATE; stale after 10 min)
  │    └─ held? → skip pass entirely (duplicate-run guard #1)
  ├─ SchedulerRun row created (status RUNNING, log accumulates)
  ├─ for each ACTIVE task with next_run_at <= now:
  │     select_for_update(task)
  │     while next_run_at due and slots < MAX_CATCHUP_SLOTS(10):
  │        CREATE_JOB? → create_job(...) via operations service (+assign staff)
  │        occurrence per resolved assignee (or one unassigned)
  │           └─ UniqueConstraint(task, slot, assignee) → IntegrityError
  │              swallowed = duplicate-run guard #2 (idempotent replay)
  │        TASK_DUE notifications
  │        last_run_at = slot; next_run_at = next_slot(...)  (None ⇒ FINISHED)
  │     failure? → failure_count++, last_error, SCHEDULER_FAILURE → managers
  ├─ pending occurrences older than grace (default 24h) → MISSED (+notify)
  ├─ active jobs past due, not yet flagged → overdue_notified_at (+notify once)
  ├─ jobs due within threshold (default 1d) → due-soon notice (once)
  └─ finalise SchedulerRun (SUCCESS/PARTIAL/FAILED, counters, log),
     release lock, record SCHEDULER_EXECUTED audit
```

## 3. Request authorisation flow

```
request → LoginRequired → capability check (CAPABILITIES role map)
        → row scope (Job.objects.visible_to / occurrence ownership / own notifications)
        → object rule in service (e.g. staff must be assigned; manager-only review)
        → mutation runs inside service → audit record (+notifications)
API: same services; JWT auth; HasCapability mirrors the map; scoping identical.
```

## 4. Build progress log

| Date | Milestone | Status |
|---|---|---|
| 2026-07-05 | Scaffolding: venv (Py 3.14), Django 5.2 LTS, settings split, env config, vendored assets | ✅ |
| 2026-07-05 | core: RBAC capability map, Department, SystemSetting (+cache), validators, CSP middleware | ✅ |
| 2026-07-05 | accounts: email-login User, invitations (token+expiry+email), lockout policy, last-activity, profile | ✅ |
| 2026-07-05 | audits: immutable AuditLog (+queryset guards), record_audit, auth signals, viewer | ✅ |
| 2026-07-05 | notifications: model, fanout service, email architecture (opt-outs), bell feed endpoints | ✅ |
| 2026-07-05 | operations: Job + number sequence, status machine, assignments, comments, validated attachments, filters, CSV | ✅ |
| 2026-07-05 | scheduling: templates (5 frequencies), occurrences, engine w/ lock + idempotency + bounded catch-up, run logs, run_scheduler | ✅ |
| 2026-07-05 | reports: 9 reports (distributions, workload, performance, completion-time, scheduled, audit) + CSV | ✅ |
| 2026-07-05 | dashboard: role-scoped widgets, AJAX range refresh, charts; admin area (settings/health/notif monitor/API overview) | ✅ |
| 2026-07-05 | API v1: JWT (+lockout+audit), 14 resources, envelope, throttles, Swagger/ReDoc | ✅ |
| 2026-07-05 | UI: dark/light theme, collapsible sidebar, 45+ templates, toasts, skeletons, empty states, error pages | ✅ |
| 2026-07-05 | seed_demo (guarded, service-driven) · pytest suite **163 green** | ✅ |
| 2026-07-05 | Docs set (README/API/SECURITY/TESTING/ENVIRONMENT/DEPLOYMENT/FEATURES/CHANGELOG) + final verification | ✅ |

## 5. Backlog / next iterations

- Attachment storage on S3-compatible backend for multi-node deployments.
- WebSocket (or SSE) live dashboard updates — currently AJAX polling.
- Optional TOTP 2FA for Super Admin / Manager accounts.
- Per-user notification preferences by type (today: global email opt-out).
- Saved report presets & scheduled emailed report exports.
- Kanban board view for jobs (status columns, drag to transition).
- API keys (scoped, hashed) for machine integrations alongside JWT.
