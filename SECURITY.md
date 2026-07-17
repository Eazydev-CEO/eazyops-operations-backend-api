# SECURITY.md — EazyOps security posture

## Authentication & session

- Invitation-only registration: no self-service signup route exists; accounts
  are created from admin-issued email tokens with configurable expiry.
- Passwords: Django validators (min length 10, common/numeric/similarity
  checks), PBKDF2 hashing.
- **Login throttling**: failed attempts are persisted
  (`accounts.FailedLoginAttempt`); after N failures within the window
  (default 5 / 15 min, tunable at runtime) the account is locked out —
  per-email, plus a 3× headroom per-IP guard against spraying. The policy is
  enforced on the web form **and** the JWT token endpoint.
- Session cookies: `HttpOnly`, `SameSite=Lax`, 8-hour age; `Secure` in
  production. JWT: 30-min access / 1-day rotating refresh.
- Login, logout, failed logins and API token grants are all audited with IP
  and user agent.

## Authorisation (RBAC)

- Single capability map (`apps/core/rbac.py`) consumed by web mixins/
  decorators and the DRF `HasCapability` permission — web and API can never
  drift apart.
- **Row-level scoping** prevents IDOR: `Job.objects.visible_to(user)` is the
  only entry point for job reads (staff → assigned jobs; viewers → own
  department, drafts excluded); notifications and occurrences are owner-
  scoped. Cross-tenant probes return **404**, never 403, so record existence
  does not leak.
- Object-level rules live in the service layer (assigned staff only, manager
  review, Super-Admin-only deletion) and are covered by tests.
- Django admin stays enabled purely as an emergency backend (`is_staff`
  users only); the operational UI is the custom dashboard.

## Audit trail (tamper resistance)

- `AuditLog` is insert-only: instance `save()` on existing rows, instance
  `delete()`, and queryset `update()`/`delete()` all raise. There is no
  admin edit path (read-only ModelAdmin).
- Records store actor (FK + denormalised email), action, object type/id/repr,
  per-field before/after values, IP, user agent, timestamp and metadata.
- Deactivating users (never deleting) keeps history attributable.

## Input & upload safety

- All uploads pass `apps.core.validators.validate_upload`: extension
  whitelist, size cap (runtime setting, default 10 MB), content-type
  cross-check, and Pillow verification for images (rejects fake/corrupt
  image payloads). Filenames are sanitised (`get_valid_filename`) before
  storage.
- Attachments are **never** served from raw `MEDIA_URL` paths in production
  flows — downloads go through permission-checked views on both web and API.
- ORM everywhere (no raw SQL); templates auto-escape; user-supplied `next`
  redirects validated with `url_has_allowed_host_and_scheme`.

## HTTP hardening

- CSRF protection on all state-changing web requests (AJAX includes the
  header; a regression test asserts enforcement).
- `Content-Security-Policy: default-src 'self'; script-src 'self'; …` — all
  JS/CSS/fonts are vendored locally, no CDN, no inline scripts (a relaxed
  policy applies only to the Swagger/ReDoc doc pages).
- `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy:
  strict-origin-when-cross-origin`, `Permissions-Policy` deny-list.
- Production settings enforce: `DEBUG=False`, SSL redirect,
  `SECURE_PROXY_SSL_HEADER`, HSTS (1 year, subdomains, preload), secure
  cookies, and refuse to boot with a placeholder `SECRET_KEY`.
- Custom 403/404/500 pages; stack traces never leave the server
  (`DEBUG=False` + generic 500 handler).

## API-specific

- Default deny: every endpoint requires authentication; permissions mirror
  the dashboard.
- Rate limiting: global user/anon rates plus tight scopes for credential
  endpoints (10/min/IP) and sensitive resources (user admin, invitations,
  audit logs — 60/min).
- Consistent error envelope avoids leaking internals; unhandled exceptions
  return a bare 500 with no detail.

## Secrets & configuration

- All secrets/config via environment variables (`.env` is gitignored;
  `.env.example` documents every key). No credentials in code, templates,
  logs or audit metadata.
- Runtime-tunable security knobs (lockout counts, upload cap, invitation
  expiry) are editable only by Super Admins and every change is audited with
  before/after values.

## Reporting a vulnerability

Internal platform: report privately to the platform owner; do not open a
public issue. Include reproduction steps and affected URLs/endpoints.
