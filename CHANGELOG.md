# Changelog

All notable changes to EazyOps are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow SemVer.

## [1.0.0] — 2026-07-05

Initial production-ready release.

### Added
- **Accounts & RBAC**: email-login user model; Super Admin / Operations
  Manager / Operations Staff / Viewer roles driven by a shared capability
  map (web + API); invitation-only registration with expiring email tokens
  (resend/cancel); login/logout/password change/password reset; failed-login
  lockout (account + IP); last-activity tracking; deactivation preserving
  audit history with self/last-admin safeguards.
- **Jobs / work orders**: automatic `JOB-YYYY-NNNNN` numbering; categories,
  priorities, departments/clients, tags, costs with variance; validated
  status machine with automatic Assigned flips and manager review
  (approve/reject + note); derived Overdue overlay with one-time alerts;
  multi-staff assignment with replace-set semantics and notifications;
  comments & progress updates; validated evidence attachments with
  permission-gated downloads; per-job audit timeline; search/filter/sort/
  pagination and audited CSV export; Super-Admin-only deletion with audited
  attempts.
- **Scheduling**: once/daily/weekly/monthly/interval templates assigned to
  staff and/or teams; checklist occurrences or full job creation per run;
  `run_scheduler` cron command — lock-guarded, idempotent, bounded catch-up;
  missed-occurrence sweep, overdue & due-soon job sweeps; per-run execution
  logs with counters; failure tracking with manager alerts; monitor UI with
  manual run; staff "My Tasks" completion flow.
- **Audit trail**: immutable `AuditLog` (model + queryset guards) capturing
  actor, action, object, before/after changes, IP, user agent across auth,
  accounts, jobs, scheduling, settings and exports; filterable viewer.
- **Notifications**: 13 in-app types with bell feed, unread badge, history,
  mark one/all read; SMTP email copies honouring per-user opt-out and a
  global switch.
- **Reports**: status/category/priority distributions, staff workload,
  staff performance, overdue, completion time, scheduled-task completion,
  audit activity — filters, Chart.js charts, audited CSV exports,
  viewer-scoped variants.
- **Dashboard**: role-scoped KPI cards, doughnut/bar/trend charts, upcoming
  tasks, workload, failure alerts, recent audits & notifications; AJAX
  range refresh.
- **Custom admin area**: users, invitations, categories, departments,
  scheduler monitor, notification monitor, audited runtime System Settings,
  System Health, API overview; Django admin retained as emergency backend.
- **REST API v1**: SimpleJWT auth with lockout + grant audit; 14 resources
  incl. workflow actions; consistent error envelope; filtering/search/
  ordering/pagination; scoped rate limits; drf-spectacular schema with
  Swagger UI and ReDoc.
- **UI**: custom dark/light enterprise theme, collapsible sidebar, sticky
  topbar, responsive tables, skeletons, empty states, toastr-style toasts,
  custom 403/404/500 pages; Bootstrap 5.3 / Icons / jQuery / Chart.js
  vendored locally under a strict CSP (no inline scripts, no CDN).
- **Tooling & docs**: guarded `seed_demo` demo-data command; pytest suite
  (163 tests); README, API, SECURITY, TESTING, ENVIRONMENT, DEPLOYMENT,
  FEATURES and PROJECTFLOW documentation set.
