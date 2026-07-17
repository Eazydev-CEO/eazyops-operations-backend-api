# DEPLOYMENT.md — production deployment guide

Target shape: Linux host (or container), PostgreSQL, HTTPS-terminating
reverse proxy (nginx/Caddy/ALB), gunicorn app server, cron for the
scheduler. Windows hosting works the same way with waitress + Task
Scheduler.

## 1. Provision

```bash
sudo mkdir -p /srv/eazyops && cd /srv/eazyops
git clone <repo> . && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt "psycopg[binary]" gunicorn
```

## 2. Configure environment

Create `/srv/eazyops/.env` (see [ENVIRONMENT.md](ENVIRONMENT.md)):

```ini
DJANGO_SETTINGS_MODULE=config.settings.prod
SECRET_KEY=<50+ random chars>
DEBUG=False
ALLOWED_HOSTS=ops.example.com
CSRF_TRUSTED_ORIGINS=https://ops.example.com
SITE_URL=https://ops.example.com
DATABASE_URL=postgres://eazyops:<password>@127.0.0.1:5432/eazyops
EMAIL_BACKEND=smtp
EMAIL_HOST=smtp.example.com
EMAIL_HOST_USER=ops-mailer
EMAIL_HOST_PASSWORD=<secret>
DEFAULT_FROM_EMAIL=EazyOps <no-reply@example.com>
ADMIN_EMAILS=oncall@example.com
```

`config.settings.prod` refuses to start with the placeholder secret and
enforces SSL redirect, HSTS, secure cookies and `DEBUG=False`.

## 3. Database & static files

```bash
createdb eazyops   # or via your managed Postgres
python manage.py migrate
python manage.py collectstatic --noinput   # hashed files into staticfiles/, served by whitenoise
python manage.py createsuperuser           # first Super Admin
python manage.py check --deploy            # should be clean
```

Do **not** run `seed_demo` in production (it refuses unless forced).

## 4. Application server

```bash
gunicorn config.wsgi:application --bind 127.0.0.1:8001 --workers 3 --timeout 60
```

systemd unit sketch:

```ini
[Unit]
Description=EazyOps
After=network.target postgresql.service

[Service]
WorkingDirectory=/srv/eazyops
ExecStart=/srv/eazyops/.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8001 --workers 3
Restart=always
User=eazyops

[Install]
WantedBy=multi-user.target
```

## 5. Reverse proxy (nginx sketch)

```nginx
server {
    listen 443 ssl http2;
    server_name ops.example.com;
    # ssl_certificate …; ssl_certificate_key …;

    client_max_body_size 20m;

    location /media/avatars/ { alias /srv/eazyops/media/avatars/; }
    # NOTE: do NOT expose /media/job_attachments/ — those are permission-gated
    # and served by the app.

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

`SECURE_PROXY_SSL_HEADER` is preconfigured for `X-Forwarded-Proto`.

## 6. Scheduler cron

```cron
*/5 * * * * eazyops cd /srv/eazyops && .venv/bin/python manage.py run_scheduler >> /var/log/eazyops/scheduler.log 2>&1
```

Overlap-safe (DB lock) and idempotent (unique slot constraints), so an
aggressive interval is fine. Monitor at `/schedule/runs/`; the System
Health page warns when no pass has run for over an hour.

Windows: Task Scheduler → run
`C:\srv\eazyops\.venv\Scripts\python.exe manage.py run_scheduler`
(start in `C:\srv\eazyops`) every 5 minutes.

## 7. Media persistence & backups

- `media/` (avatars + job attachments) must live on persistent storage and
  be included in backups alongside PostgreSQL dumps
  (`pg_dump eazyops | gzip > …`).
- Attachments are only ever streamed through authenticated views — keep the
  directory itself outside any public web root.

## 8. Upgrades

```bash
cd /srv/eazyops && git pull
source .venv/bin/activate && pip install -r requirements.txt
python manage.py migrate && python manage.py collectstatic --noinput
sudo systemctl restart eazyops
```

Run `pytest` in CI before shipping; `python manage.py check --deploy` after
each config change.

## 9. Post-deploy smoke checklist

- [ ] `/core/health/` returns `{"status": "ok"}` over HTTPS
- [ ] Login works; failed logins audit + lock out
- [ ] `/api/docs/` loads; token obtain works
- [ ] A test job full lifecycle (create → assign → progress → review)
- [ ] Scheduler run visible at `/schedule/runs/` within 5 minutes
- [ ] Emails delivered (invite yourself)
- [ ] System Health page: no pending migrations, DEBUG off
