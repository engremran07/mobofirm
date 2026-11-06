#!/usr/bin/env bash
# Back up the Mobofirm PostgreSQL database (and optionally uploaded files).
#
# Credentials come from the environment (DB_NAME, DB_USER, DB_PASSWORD, DB_HOST,
# DB_PORT) or from a .env file in the repository root; nothing is hard-coded.
#
# Usage: scripts/backup_db.sh [--with-files] [--output-dir DIR] [--retention-days N]
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${BACKUP_DIR:-$repo_root/backups}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"
with_files=false

while [ "$#" -gt 0 ]; do
    case "$1" in
        --with-files) with_files=true ;;
        --output-dir) output_dir="$2"; shift ;;
        --retention-days) retention_days="$2"; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

# Read only the DB_* keys from .env so unrelated (possibly non-shell-safe) lines are ignored.
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

command -v pg_dump >/dev/null || { echo "pg_dump is not installed or not on PATH." >&2; exit 1; }

mkdir -p "$output_dir"
chmod 700 "$output_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump_file="$output_dir/${DB_NAME}_${stamp}.dump"

PGPASSWORD="$DB_PASSWORD" pg_dump \
    --host "$db_host" --port "$db_port" --username "$DB_USER" \
    --format custom --no-owner --file "$dump_file" "$DB_NAME"
chmod 600 "$dump_file"
echo "Database dump written: $dump_file"

if [ "$with_files" = true ]; then
    files_archive="$output_dir/files_${stamp}.tar.gz"
    tar -czf "$files_archive" -C "$repo_root" \
        $(for dir in media storage; do [ -d "$repo_root/$dir" ] && echo "$dir"; done)
    chmod 600 "$files_archive"
    echo "Files archive written: $files_archive"
fi

# Prune only files this script creates, and only past the retention window.
find "$output_dir" -maxdepth 1 -type f \
    \( -name "${DB_NAME}_*.dump" -o -name "files_*.tar.gz" \) \
    -mtime "+$retention_days" -print -delete
