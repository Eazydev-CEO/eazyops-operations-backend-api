# EazyOps REST API (v1)

Base URL: **`/api/v1/`** · Interactive docs: **`/api/docs/`** (Swagger UI) ·
`/api/redoc/` (ReDoc) · raw schema `/api/schema/` (OpenAPI 3).

Versioning is namespace-based; v1 is the only (and default) version.

## Authentication

JWT bearer tokens (SimpleJWT):

| Endpoint | Purpose | Throttle |
|---|---|---|
| `POST /api/v1/auth/token/` | Obtain `{access, refresh}` from `{email, password}` | 10/min per IP |
| `POST /api/v1/auth/token/refresh/` | New access token (refresh rotates) | 30/min per IP |
| `POST /api/v1/auth/token/verify/` | Validate a token | 30/min per IP |

* Access tokens live 30 minutes, refresh tokens 1 day (rotated on use).
* The web login lockout policy also applies here (5 failures / 15 min by
  default) and successful token grants are written to the audit trail.
* Session authentication is additionally accepted, so the Swagger UI works
  while signed in to the dashboard.

```bash
curl -X POST https://ops.example.com/api/v1/auth/token/ \
     -H "Content-Type: application/json" \
     -d '{"email":"manager@example.com","password":"…"}'
# → {"access":"…","refresh":"…"}
curl https://ops.example.com/api/v1/jobs/ -H "Authorization: Bearer <access>"
```

## Response conventions

**Success** — resource JSON, or for lists:

```json
{"count": 42, "next": "…?page=3", "previous": "…?page=1", "results": [ … ]}
```

Pagination: `?page=` & `?page_size=` (default 20, max 100).

**Errors** — every error uses one envelope (validation problems from the
service layer included):

```json
{
  "success": false,
  "error": {
    "code": "validation_error",
    "detail": "Invalid transition: Open → Completed.",
    "fields": {"due_date": ["Due date cannot be before the start date."]}
  }
}
```

`code` values follow DRF defaults: `authentication_failed`, `not_authenticated`,
`permission_denied`, `not_found`, `validation_error`, `throttled`, …
Row-level misses return **404** (not 403) so object existence never leaks.

## Resources

Access column: *management* = Super Admin + Operations Manager.

| Resource | Methods | Access |
|---|---|---|
| `/me/` | GET, PATCH (name, phone, photo, email prefs) | any authenticated |
| `/users/` | GET list/detail; PATCH; POST `{id}/deactivate|activate/` | read: management · write: Super Admin |
| `/roles/` | GET role & capability matrix | management |
| `/invitations/` | GET, POST; POST `{id}/cancel/` | Super Admin |
| `/departments/` | GET; POST/PATCH (no DELETE — deactivate) | read: any · write: Super Admin |
| `/categories/` | GET; POST/PATCH (no DELETE — deactivate) | read: any · write: management |
| `/jobs/` | full CRUD + filters/search/ordering | row-scoped by role (see below) |
| `/jobs/{id}/transition/` | POST `{status, note}` — validated status change | per status machine & role |
| `/jobs/{id}/assign/` | POST `{staff: [ids]}` — replace assignment set | management |
| `/jobs/{id}/timeline/` | GET audit timeline for the job | job visibility |
| `/job-comments/` | GET (`?job=`), POST | job visibility; comment rights per role |
| `/job-attachments/` | GET (`?job=`), POST multipart, DELETE, GET `{id}/download/` | job visibility; uploads validated |
| `/assignments/` | GET read-only assignment records (`?job=`, `?staff=`) | management |
| `/scheduled-tasks/` | GET, POST, PATCH; POST `{id}/pause|resume/` (no DELETE) | read: staff see own · write: management |
| `/occurrences/` | GET (`?status=`, `?scheduled_task=`); POST `{id}/complete/` | staff: own · management: all |
| `/scheduler-runs/` | GET execution log | management |
| `/notifications/` | GET own; POST `{id}/read/`, `read-all/`; GET `unread_count/` | own records only |
| `/audit-logs/` | GET read-only (`?action=`, `?object_type=`, `?actor=`, `?date_from/to=`, `?search=`) | management |
| `/dashboard/summary/` | GET aggregate widget data (`?range=` days) | any (role-scoped) |
| `/reports/{slug}/` | GET report JSON — `status`, `category`, `priority`, `overdue`, `workload`, `performance`, `completion-time`, `scheduled`, `audit` | distributions: viewer+ (scoped) · rest: management |

### Job row scoping (identical to the web app)

| Role | Sees |
|---|---|
| Super Admin / Manager | all jobs |
| Operations Staff | jobs they are assigned to |
| Viewer / Client | non-draft jobs of their own department |

### Job filters

`?status=` (incl. the derived `OVERDUE`), `?priority=`, `?category=`,
`?department=`, `?manager=`, `?staff=`, `?tag=`, `?due_from/due_to=`,
`?created_from/created_to=`, `?search=` (number/title/description/tag),
`?ordering=` (`created_at`, `due_date`, `priority`, `number`, `title`,
`status`; prefix `-` for descending).

### Example: create and drive a job

```bash
POST /api/v1/jobs/
{"title":"Replace pump seals","category":3,"department":2,"manager":5,
 "priority":"HIGH","due_date":"2026-07-20","staff":[7],"tags":["pump","urgent"]}
# 201 → status ASSIGNED (staff supplied), number JOB-2026-00031

POST /api/v1/jobs/31/transition/   {"status":"IN_PROGRESS"}          # as assigned staff
POST /api/v1/jobs/31/transition/   {"status":"AWAITING_REVIEW"}      # as assigned staff
POST /api/v1/jobs/31/transition/   {"status":"COMPLETED","note":"ok"} # as manager
```

## Rate limits

| Scope | Default |
|---|---|
| authenticated users | 2000/hour |
| anonymous | 50/hour |
| token obtain | 10/min per IP |
| token refresh/verify | 30/min per IP |
| sensitive (users, invitations, audit logs) | 60/min |

Throttled responses return `429` with the standard envelope and a
`Retry-After` header.
