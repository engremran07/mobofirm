#!/usr/bin/env bash
# Restore a dump created by scripts/backup_db.sh. DESTRUCTIVE: existing objects
# in the target database are dropped and recreated.
#
# Safety rails:
#   * the dump path and the exact confirmation text "RESTORE <DB_NAME>" are required;
#   * non-local database hosts need ALLOW_REMOTE_RESTORE=true;
#   * stop the web/worker/beat processes before restoring.
#
# Usage: scripts/restore_db.sh DUMP_FILE --confirm "RESTORE mobofirm" [--files ARCHIVE]
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dump_file="${1:-}"
[ -n "$dump_file" ] || { echo "Usage: $0 DUMP_FILE --confirm \"RESTORE <db>\" [--files ARCHIVE]" >&2; exit 2; }
shift
confirmation=""
files_archive=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --confirm) confirmation="$2"; shift ;;
        --files) files_archive="$2"; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

env_file="$repo_root/.env"
if [ -f "$env_file" ]; then
    while IFS='=' read -r key value; do
        case "$key" in
            DB_NAME|DB_USER|DB_PASSWORD|DB_HOST|DB_PORT)
                if [ -z "${!key:-}" ]; then
                    export "$key=${value%$'\r'}"
                fi
                ;;
        esac
    done < <(grep -E '^(DB_NAME|DB_USER|DB_PASSWORD|DB_HOST|DB_PORT)=' "$env_file" || true)
fi

: "${DB_NAME:?DB_NAME is required}"
: "${DB_USER:?DB_USER is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
db_host="${DB_HOST:-127.0.0.1}"
db_port="${DB_PORT:-5432}"

[ -f "$dump_file" ] || { echo "Dump file not found: $dump_file" >&2; exit 1; }
[ "$confirmation" = "RESTORE $DB_NAME" ] || {
    echo "Refusing to restore: pass --confirm \"RESTORE $DB_NAME\"." >&2
    exit 1
}
case "$db_host" in
    127.0.0.1|localhost|::1) ;;
    *)
        [ "${ALLOW_REMOTE_RESTORE:-false}" = "true" ] || {
            echo "Refusing to restore to remote host '$db_host' without ALLOW_REMOTE_RESTORE=true." >&2
            exit 1
        }
        ;;
esac
command -v pg_restore >/dev/null || { echo "pg_restore is not installed or not on PATH." >&2; exit 1; }

PGPASSWORD="$DB_PASSWORD" pg_restore \
    --host "$db_host" --port "$db_port" --username "$DB_USER" \
    --dbname "$DB_NAME" --clean --if-exists --no-owner --exit-on-error "$dump_file"
echo "Database restored from $dump_file"

if [ -n "$files_archive" ]; then
    [ -f "$files_archive" ] || { echo "Files archive not found: $files_archive" >&2; exit 1; }
    tar -xzf "$files_archive" -C "$repo_root"
    echo "Files restored from $files_archive"
fi
