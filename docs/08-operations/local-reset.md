# Local database reset and seed

`scripts\reset_dev_db.ps1` rebuilds the **local** PostgreSQL development database from nothing: backup, drop and recreate schema `public`, migrate, seed, create the superuser. It is destructive by design and only ever runs against a loopback database.

## Safety rails

- `DB_HOST` must be `127.0.0.1`, `localhost` or `::1`; anything else aborts before any command runs.
- `-ConfirmDatabase <DB_NAME>` must repeat the database name.
- `pg_dump` (from `PATH` or the newest `C:\Program Files\PostgreSQL\<version>\bin`) writes `<DB_NAME>_before_reset_<timestamp>.dump` into `backups\` (or `-BackupDir` / `BACKUP_DIR`) first. A missing or empty dump stops the script before anything is dropped. Restore with `scripts\restore_db.ps1`.
- Credentials come from the environment or `.env` (`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`). If the application role cannot drop the schema, set `PG_ADMIN_USER` and `PG_ADMIN_PASSWORD`; that role is used for the backup, drop and create steps only.

## Run it

```powershell
.\scripts\reset_dev_db.ps1 -ConfirmDatabase mobofirm
# Optional: -LimitBrands 3 (faster), -SkipSeo, -BackupDir D:\backups
```

The superuser comes from `DJANGO_SUPERUSER_EMAIL` and `DJANGO_SUPERUSER_PASSWORD` (environment or `.env`); the script prompts for whatever is missing. The password is never written to disk, and the superuser's email is marked verified so the account can sign in. This is the only account with a usable password.

## What `seed_platform` creates

`manage.py seed_platform --confirm` is deterministic and idempotent. It refuses a non-loopback database and refuses a database that already holds catalogue, blog or comment data unless `--allow-existing` is given. Other options: `--limit-brands N`, `--skip-seo`, `--crawl-base-url URL`.

Steps, each through the owning app's service or signals:

1. `bootstrap_site` — Site row, singleton settings, `AppRegistry`, permission groups, the automation user; then `seed_pages` for the public pages.
2. `seed_people` — submission, review, editorial and publishing accounts plus reader accounts. All have unusable passwords and reserved `.invalid` addresses.
3. Ads — every ad switch is turned off; no campaigns or networks are created.
4. `seed_catalog` — chipsets, at least 20 brands with at least 5 real models each (marketing names and model codes), variants, and flashing tool categories, official-vendor tools and guides. Each model gets two official firmware listings as **external** downloads on the reserved host `downloads.example.invalid`, which must be in `FIRMWARE_EXTERNAL_URL_ALLOWED_HOSTS` (it is the default). The uploader account submits each listing and the reviewer account approves it through `approve_pending_firmware`. The brand, model and chipset articles, and their tags, come from the firmware blog automation (one article each, firmware grouped by category).
5. `seed_editorial` — original articles written by the editorial account, reviewed and published by the publishing account through the blog workflow.
6. `seed_comments` — reader comments submitted through the comments service; its screening publishes clean comments and queues the rest.
7. SEO — `rebuild_linkable`, `seo_rebuild_graph` and `seo_process_jobs` derive the inventory from the records. The crawl needs a running site: start the server and run `manage.py seo_crawl --base-url http://127.0.0.1:8000`, or pass `--crawl-base-url`. No SEO data is invented.
8. Verification — at least 20 brands, 5 models per brand and 2 approved firmware files per model, exactly one automated article per brand, model and chipset, public visibility, and HTTP 200 for every seeded firmware detail and download page.

The download links are intentionally dummy: the host is reserved (`.invalid`) and cannot resolve. The pages label them as external sources.

## Validate without PostgreSQL

`tests/backend/core/test_seed_platform.py` runs a two-brand slice and one full-scale seed on in-memory SQLite:

```powershell
.venv\Scripts\python.exe -m pytest tests/backend/core/test_seed_platform.py -q
```
