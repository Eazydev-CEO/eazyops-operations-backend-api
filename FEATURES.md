# FEATURES.md — complete feature list

## 1. Accounts & role-based access
- Email-login custom user model; profile with photo (validated image upload),
  phone, department, job title, active flag, email-notification preference.
- **Roles**: Super Admin (full platform) · Operations Manager (jobs, staff
  assignment, schedules, reports, configuration) · Operations Staff (assigned
  jobs: progress, notes, evidence, transitions) · Viewer/Client (read-only,
  scoped to own department).
- Capability map shared by web views and API permissions; row-level scoping
  everywhere (no IDOR).
- Invitation-only registration: token + expiry emails, resend/cancel, accept
  flow sets name/password and signs in; inviter notified on acceptance.
- Login, logout, password change, full password-reset flow (email).
- Failed-login lockout (per-account + per-IP), fully audited.
- Deactivation (not deletion) preserving audit history; protection against
  deactivating yourself or the last active Super Admin; role changes audited
  with before/after and guarded the same way.
- `last_login` + throttled `last_activity_at` tracking.

## 2. Jobs / work orders
- Unique automatic reference numbers `JOB-<year>-<seq>` (per-year sequence
  under row lock, collision-retry safe).
- Fields: title, description, category (colour-coded), priority (Low/Medium/
  High/Urgent), status, client/department, manager, multi-staff assignment,
  start/due/completed dates, estimated & actual cost (variance shown), tags,
  attachments, comments, activity timeline.
- **Status machine** (invalid transitions rejected, every change audited):
  Draft → Open → Assigned (automatic on staff assignment) → In Progress →
  Awaiting Review → Completed, Cancelled from any active state; Awaiting
  Review can be rejected back to In Progress with a review note.
- **Overdue** derived automatically from due date + active status: badge
  everywhere, list/API filter value, report rows, one-time notifications.
- Assignment: replace-set semantics, notify added & removed staff, auto
  Open↔Assigned status flips, terminal jobs locked.
- Manager review: approve (sets completed timestamp, reviewer, note) or
  reject (note, notify staff).
- Comments & progress updates with author + timestamp; staff limited to
  their own jobs.
- Attachments: extension whitelist, size cap, content-type cross-check,
  image verification, sanitised filenames, permission-gated downloads,
  uploader/manager deletion.
- List: search (number/title/description/tag), filters (status incl.
  Overdue, priority, category, department, staff, tag, due range, created
  range), 6 sort options, pagination, CSV export honouring filters (audited).
- Hard deletion restricted to Super Admin; unauthorised attempts audited.

## 3. Scheduled tasks (recurring engine)
- Templates: one-time, daily, weekly (weekday), monthly (day 1–28), custom
  interval (≥5 min); start/end window; UTC times.
- Actions: create checklist **occurrences** per assignee, or create a full
  **job** from a template (category, department, manager, priority, due
  offset) with automatic staff assignment.
- Assign explicit staff and/or a whole team (department members resolve at
  run time).
- Engine state per template: next run, last run, status
  (Active/Paused/Finished), failure count, last error.
- `run_scheduler` management command — single idempotent pass, cron-ready:
  DB lock (stale-reclaim) prevents concurrent passes; unique occurrence
  constraints make slot materialisation idempotent; catch-up after downtime
  bounded to 10 slots/task/pass.
- Each pass also: marks pending occurrences past a grace period as Missed
  (notifying assignee + managers), flags newly overdue jobs (once), sends
  due-soon notices (once).
- Every execution logged (`SchedulerRun`: counters, duration, full text log)
  and audited; monitor page with failing-task panel and manual **Run now**.
- Staff "My Tasks" queue with inline completion + notes; managers can
  complete on behalf; pause/resume audited.

## 4. Audit log
- Captures: login/logout/failed logins, password changes, API token grants,
  invitations (sent/accepted/cancelled), user create/update/role/activate/
  deactivate, job create/update/status/assign/unassign/comment/attachment/
  delete-attempt/delete, schedule create/update/pause/resume, scheduler
  executions, occurrence completions, setting/category/department changes,
  CSV exports.
- Each record: actor (+denormalised email), action, object type/id/repr,
  before/after change map, IP, user agent, timestamp, metadata.
- Tamper-resistant: insert-only at model and queryset level.
- Viewer: full-text search, action/object/actor/date filters, pagination —
  restricted to management roles.

## 5. Notifications
- Types: job assigned/reassigned, due soon, overdue, submitted for review,
  approved, rejected, task due, task missed, scheduler failure, invitation
  accepted, welcome, system.
- Bell with unread badge, AJAX dropdown feed (60 s refresh), mark one/all
  read, full history page with unread filter.
- Email copies for important types via env-configured SMTP; per-user opt-out
  plus global master switch; delivery recorded per notification; failures
  never break business flows.

## 6. Dashboard
- Role-scoped widgets: total/open/assigned/in-progress/overdue/completed
  stats, jobs by status (doughnut), jobs by priority (bar), 8-week created
  vs completed trend (line), staff workload table, upcoming scheduled tasks
  (staff see their own), failed/missed schedule alert banner, recent audit
  activity (management), recent notifications.
- AJAX range selector (7/30/90/180 days) refreshes stats and charts without
  reload; loading state during fetch.

## 7. Reports (all filterable, all CSV-exportable, exports audited)
- Jobs by status / category / priority (charts + share bars).
- Staff workload (active, urgent, pending occurrences).
- Staff completion performance (volume, avg days, on-time rate).
- Overdue jobs (days overdue, oldest first).
- Completion time (overall, by category, by priority).
- Scheduled task completion (totals, missed, completion rate).
- Audit activity (per-day series, top actions, most active users).
- Viewers get the distribution reports scoped to their department only.

## 8. Custom admin area (inside the dashboard)
- Users (search/filter, edit, activate/deactivate) & invitations.
- Job categories and departments/clients (with usage counts).
- Scheduler monitor (runs, logs, failing tasks, manual run).
- Notification monitor (volume, unread, emailed stats, type filter).
- System settings (runtime-editable, validated, audited).
- System health (DB, pending migrations, scheduler freshness, failing
  tasks, volumes, versions, DEBUG warning).
- API access overview (endpoints, token lifetimes, throttle rates, quick
  start) + link to Swagger.
- Django admin retained as restricted emergency backend.

## 9. REST API v1
- JWT auth with lockout + audit; session auth for the docs UI.
- Resources: auth, me, users, roles, invitations, departments, categories,
  jobs (+transition/assign/timeline), job comments, job attachments
  (+download), assignments, scheduled tasks (+pause/resume), occurrences
  (+complete), scheduler runs, notifications (+read/read-all/unread-count),
  audit logs, dashboard summary, reports.
- Consistent error envelope, pagination contract, django-filter + search +
  ordering, scoped throttles, OpenAPI schema with Swagger UI and ReDoc.

## 10. UI/UX
- Premium dark **and** light theme (CSS variables + Bootstrap 5.3 colour
  modes), persisted, no flash-of-wrong-theme.
- Collapsible sidebar (persisted) with off-canvas mobile drawer; sticky
  glassy topbar.
- Responsive throughout; tables scroll horizontally on small screens.
- Toastr-style toasts for Django messages and AJAX results; skeleton
  loaders; designed empty states; confirm dialogs on destructive actions;
  labelled, validated forms; custom 403/404/500 pages.
- All assets vendored (Bootstrap, Bootstrap Icons, jQuery, Chart.js) — CSP
  `script-src 'self'` with zero inline scripts.
