# ENVIRONMENT.md — configuration reference

All deploy-specific configuration comes from environment variables, loaded
from `.env` in development (`python-dotenv`). Copy `.env.example` → `.env`
to start. **Never commit `.env`.**

## Core

| Variable | Default | Notes |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.dev` | `config.settings.prod` in production |
| `SECRET_KEY` | placeholder | **Required in prod** — boot fails on the placeholder. Generate: `python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"` |
| `DEBUG` | `False` (dev module defaults to `True`) | never `True` in production |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | comma-separated |
| `CSRF_TRUSTED_ORIGINS` | *(empty)* | e.g. `https://ops.example.com` — required behind HTTPS |
| `SITE_URL` | `http://localhost:8000` | absolute base used in invitation/notification emails |

## Database

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | *(empty → SQLite `db.sqlite3`)* | `postgres://user:pass@host:5432/dbname` for PostgreSQL (install `psycopg[binary]`) |
| `DB_CONN_MAX_AGE` | `60` | persistent connection lifetime (seconds) |

## Email (SMTP)

Console backend by default (emails print to stdout). Set
`EMAIL_BACKEND=smtp` to enable real delivery.

| Variable | Default |
|---|---|
| `EMAIL_BACKEND` | `console` (`smtp` enables SMTP) |
| `EMAIL_HOST` / `EMAIL_PORT` | *(empty)* / `587` |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | *(empty)* |
| `EMAIL_USE_TLS` | `True` |
| `DEFAULT_FROM_EMAIL` | `EazyOps <no-reply@localhost>` |

## Security tuning

| Variable | Default | Notes |
|---|---|---|
| `SECURE_SSL_REDIRECT` | `True` (prod) | disable only when TLS terminates without the `X-Forwarded-Proto` header |
| `SECURE_HSTS_SECONDS` | `31536000` | prod |
| `SESSION_COOKIE_AGE` | `28800` | seconds (8 h) |
| `LOGIN_MAX_FAILURES` | `5` | lockout threshold (runtime-overridable in System Settings) |
| `LOGIN_LOCKOUT_MINUTES` | `15` | lockout window |
| `ADMIN_EMAILS` / `SERVER_EMAIL` | *(empty)* | prod error emails |

## Application knobs

| Variable | Default | Notes |
|---|---|---|
| `INVITATION_EXPIRY_DAYS` | `7` | invitation link lifetime |
| `MAX_UPLOAD_MB` | `10` | attachment size cap |
| `JOB_DUE_SOON_DAYS` | `1` | due-soon notification threshold |

The last group also exists as **runtime System Settings** (custom admin
area → System Settings); a database value takes precedence over the
environment default and every change is audited.
