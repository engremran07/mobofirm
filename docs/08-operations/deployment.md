# Deployment

Mobofirm ships as one Docker image (`Dockerfile`: Vite build → Python dependencies → non-root runtime) run in the topology of `docker-compose.yml`: `web` (gunicorn), `worker` and `beat` (Celery), `db` (PostgreSQL 16), `redis`, and `nginx` as the only published service. Nothing in this repository applies a migration or touches a database on its own.

## Required configuration

Copy `.env.example` to `.env`. With `DJANGO_SETTINGS_MODULE=app.settings.production`, startup fails (`ImproperlyConfigured`) unless these are set:

| Setting | Rule |
| --- | --- |
| `DJANGO_SECRET_KEY` | ≥ 50 random characters |
| `DJANGO_ALLOWED_HOSTS`, `SITE_URL` | real hostname(s) (no `*`); `SITE_URL` is the public `https://` origin and seeds `CSRF_TRUSTED_ORIGINS` |
| `DB_PASSWORD` (+ `DB_NAME/USER/HOST/PORT`) | explicit |
| `MFA_ENCRYPTION_KEY` | separate from the secret key (rotating `DJANGO_SECRET_KEY` must not lock out MFA users) |
| `SOCIAL_CREDENTIAL_ENCRYPTION_KEY` | a Fernet key |
| `CACHE_URL` | shared Redis cache; rate limits and MFA throttles are only effective across workers with it |
| `CELERY_BROKER_URL` | explicit |
| `EMAIL_HOST` (or Gmail) and `DEFAULT_FROM_EMAIL` | console/locmem mail is rejected |

Production also forces secure cookies, HTTPS redirect (health probes are exempt), HSTS, `SECURE_PROXY_SSL_HEADER`, and `TRUSTED_PROXY_COUNT=1` (nginx). Client IPs come from `REMOTE_ADDR` or the address the trusted proxy appended to `X-Forwarded-For`; `X-Real-IP` and client-supplied forwarded values are never trusted. `QUEUE_BACKEND` defaults to `celery`.

## First deployment

1. Put TLS material at `deploy/certs/fullchain.pem` and `privkey.pem` (git-ignored).
2. `docker compose build`
3. `docker compose up -d db redis`
4. Review the target database, then run `docker compose --profile ops run --rm migrate` and `docker compose --profile ops run --rm bootstrap` (`bootstrap_site` sets the `Site`, singleton settings/`AppRegistry`, staff groups and the automation user; it is idempotent). Create the first admin with `docker compose run --rm web python manage.py createsuperuser`.
5. `docker compose up -d` — `web` collects static files into a volume that nginx serves.

## Files and downloads

- `/static/` is served by nginx from the collected `staticfiles` volume; `/media/` serves public uploads only.
- Firmware files are never public: Django authorises the request and replies with `X-Accel-Redirect: /protected-media/…`, which nginx maps (internal location) to the `media` volume. An internal `/protected-storage/` location maps to the `storage` volume for files kept under `FIRMWARE_STORAGE_ROOT`.

## Health, logs, scheduled work

- `/health/live/` (process) and `/health/ready/` (database and cache) return only `{"status": "ok"|"unavailable"}`; details go to the server log. The image `HEALTHCHECK` uses readiness.
- Logs go to stdout. Set `LOG_DIR` for a daily-rotated file kept `LOG_RETENTION_DAYS` days. Device events (raw IP/user agent) are purged after `DEVICE_EVENT_RETENTION_DAYS`.
- `beat` runs `CELERY_BEAT_SCHEDULE` (`app/settings/base.py`): retention cleanup (`apps.core.tasks.cleanup_old_data`, expired sessions and API tokens), device-event/fingerprint/insight purges, plus the app-owned firmware, storage, file-manager and shop jobs.

## Backups and restore

`scripts/backup_db.sh` / `scripts/backup_db.ps1` write a timestamped `pg_dump` (custom format, mode 600), optionally `--with-files` (`media`, `storage`), and prune only their own files past `BACKUP_RETENTION_DAYS`. `scripts/restore_db.sh` / `.ps1` are destructive and require the exact text `RESTORE <DB_NAME>`; remote hosts need `ALLOW_REMOTE_RESTORE=true` (`-AllowRemote`). Stop `web`, `worker` and `beat` first. Store dumps off-host and test a restore periodically; backup access and retention are a data-owner decision.

## Windows development

`scripts\dev_up.ps1` checks `.venv`, `.env`, PostgreSQL/Redis reachability, builds the frontend if needed, runs Django checks and starts `runserver`. `-Migrate` and `-Bootstrap` are opt-in; confirm the database named in `.env` before using them.

## Verification

CI runs `manage.py check --deploy --fail-level WARNING --settings=app.settings.production` (with throwaway values), `pip-audit`, `npm audit --omit=dev --audit-level=high` and builds the image. Smoke test a deployment with `curl -fsS http://<host>/health/ready/` and by signing in, receiving a verification email, and downloading a firmware file.
