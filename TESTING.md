# TESTING.md

## Running

```bash
pytest                     # full suite (163 tests, ~2 s, in-memory SQLite)
pytest tests/test_api.py   # one module
pytest -k lockout          # by keyword
python manage.py check                    # system checks
python manage.py makemigrations --check   # fails if migrations are missing
python manage.py check --deploy           # production hardening checklist
```

Configuration: `pytest.ini` (pytest-django, `config.settings.test` —
in-memory SQLite, MD5 hasher for speed, locmem email, in-memory file
storage, throttles disabled by default). An autouse fixture clears the
cache between tests so runtime settings and throttle counters never leak.

## What is covered (163 tests)

### tests/test_accounts.py
- Login page, success (+LOGIN audit), failure (+attempt row +audit).
- Lockout after N failures (even with correct password afterwards),
  clearing on success, inactive-user rejection, last-activity tracking.
- Invitations: creation (+email +audit), duplicate/existing-email/privilege
  rules, accept flow (user created, invitation consumed, audits), expired &
  cancelled links (410), absence of any self-signup route, invite-view
  permission.
- Lifecycle: deactivate preserves audit, self/last-super-admin protection,
  role-change audit with before/after, own-role change refusal.

### tests/test_jobs.py
- Job numbers: format, sequence, uniqueness.
- Status machine: full happy path, invalid transitions, same-status,
  derived-OVERDUE unsettable, ASSIGNED only via assignment, terminal
  freeze, reject flow (+notification +review note), overdue derivation,
  transition audits with before/after.
- Permission boundaries: staff limited to their own jobs and their two
  transitions, no self-approval, no cancel; viewer department scoping and
  draft exclusion; staff visibility; IDOR detail → 404; delete Super-Admin-
  only (+attempt audit); completed jobs locked for managers.
- Assignment: notify added/removed, auto status flips, replace-set,
  staff-only validation, terminal lock, permission.
- Comments/attachments: author rules, empty-comment rejection, valid image
  accepted (+audit), bad extension / oversize / fake-image rejection,
  filename sanitisation, cross-department download denial + same-department
  viewer access.
- List & export: OVERDUE/priority/search/date filters, pagination, CSV
  content + export audit + permission, update audit diffs.

### tests/test_scheduling.py
- Recurrence math per frequency (daily/weekly/monthly/interval/once,
  end-window), model validation rules.
- Engine: occurrence per assignee (+TASK_DUE), duplicate-run prevention,
  team resolution, CREATE_JOB action (job + assignment + linkage), bounded
  catch-up, once-task finishing, paused skip, missed marking (+notify),
  overdue flag once-only, run audit/log, lock exclusivity + stale reclaim,
  management command, failure recording (+manager notification, PARTIAL run).
- Completion: assignee/manager rights, double-completion rejection,
  pause/resume audit + re-arm.

### tests/test_audits_notifications.py
- Immutability at instance and queryset level; request meta capture
  (proxy-aware IP, UA); change serialisation; system (actor-less) entries;
  viewer permission + filtering.
- Notification dedupe, inactive skip, email-by-type, user opt-out, master
  switch, in-app-only types, owner-scoped mark-read/all, feed endpoint
  scoping, history scoping, open-redirect guard.

### tests/test_api.py
- JWT obtain/refresh/use, audit on grant, error envelope on bad
  credentials, lockout parity, anonymous rejection, /me profile update +
  role-escalation refusal.
- Jobs: role-scoped listing/detail (404 not 403), create permission +
  payloads (staff/tags), validation envelope with field errors, transition
  action (valid/invalid/permission), assign action, delete rights,
  filters/search/ordering, pagination contract, timeline action.
- Comments/attachments via API incl. upload validation and scoping.
- Users/invitations/roles/audit-logs/scheduled-tasks/occurrences/
  notifications/dashboard/reports permissions and behaviour.
- Throttling: auth scope returns 429 with envelope.

### tests/test_reports_security.py
- Report correctness against known fixtures: status counts (incl. derived
  overdue), staff performance math (avg days, on-time rate), completion
  time, scheduled-task completion rate, viewer department scoping, CSV.
- Security posture: anonymous redirects on protected pages, CSP/nosniff/
  frame/permissions/referrer headers, Django-admin gating, custom 404,
  minimal public health endpoint, CSRF enforcement on POST, production
  settings hardening (DEBUG off, secure cookies, HSTS, SSL redirect),
  viewer locked out of every admin-area page.

## Manual verification checklist

- `python manage.py seed_demo --run-scheduler`, sign in as each role and
  walk the dashboard, jobs, schedule, notifications, reports, admin pages.
- Responsive pass: 375 px (mobile drawer, horizontal table scroll), 768 px,
  1440 px; dark and light themes.
- `/api/docs/` renders and authorises with a demo JWT.
