#!/usr/bin/env bash
# Snapshot the SQLite DB and (optionally) push to remote object storage.
#
# Designed to run from the host (cron) or from inside the backend container.
# Uses sqlite3's online .backup so we never copy a half-flushed file.
#
# Local:   ./scripts/backup_sqlite.sh
# In cron: 0 3 * * * /opt/penny-pincher-pro/scripts/backup_sqlite.sh
#
# Required env vars:
#   DATABASE_PATH      Path to the live SQLite file. Defaults to ./data/wheel.db.
#   BACKUP_DIR         Local snapshot directory. Defaults to ./data/backups.
#
# Optional env vars (all-or-nothing for off-site upload):
#   BACKUP_REMOTE      rclone remote name + path, e.g. "b2:wheel-backups/prod".
#                      When set, copies the snapshot off-site.
#   BACKUP_RETENTION_DAYS  Local snapshots older than this many days are
#                          deleted after a successful upload. Default 14.

set -euo pipefail

DATABASE_PATH="${DATABASE_PATH:-./data/wheel.db}"
BACKUP_DIR="${BACKUP_DIR:-./data/backups}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

if [[ ! -f "$DATABASE_PATH" ]]; then
    echo "backup: source DB not found at $DATABASE_PATH" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
snapshot="$BACKUP_DIR/wheel-$stamp.db"

echo "backup: snapshotting $DATABASE_PATH -> $snapshot"
sqlite3 "$DATABASE_PATH" ".backup '$snapshot'"

# Compress for off-site transport. zstd is small + fast; gzip is the fallback
# the world always has.
if command -v zstd >/dev/null 2>&1; then
    zstd --rm -q "$snapshot"
    snapshot="${snapshot}.zst"
elif command -v gzip >/dev/null 2>&1; then
    gzip "$snapshot"
    snapshot="${snapshot}.gz"
fi

echo "backup: local snapshot = $snapshot ($(stat -c %s "$snapshot" 2>/dev/null || stat -f %z "$snapshot") bytes)"

if [[ -n "$BACKUP_REMOTE" ]]; then
    if ! command -v rclone >/dev/null 2>&1; then
        echo "backup: rclone not installed; skipping off-site copy" >&2
        exit 1
    fi
    echo "backup: rclone copy -> $BACKUP_REMOTE"
    rclone copy "$snapshot" "$BACKUP_REMOTE/" --quiet

    # Prune old local snapshots after a successful upload.
    find "$BACKUP_DIR" -type f -name 'wheel-*.db*' \
        -mtime +"$BACKUP_RETENTION_DAYS" -delete
fi

echo "backup: done"
