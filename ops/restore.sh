#!/bin/sh
# ---------------------------------------------------------------------------
# Restore from an encrypted backup.
#
# Usage:
#   restore.sh <backup.dump.gpg> [target-database]
#
# Restoring is deliberately explicit: it refuses to overwrite a database that
# already holds data unless RESTORE_FORCE=1 is set, because an accidental
# restore over a live catalogue destroys the revision history it is meant to
# protect.
# ---------------------------------------------------------------------------
set -eu

ENCRYPTED="${1:?usage: restore.sh <backup.dump.gpg> [target-database]}"
TARGET="${2:-${PGDATABASE:?PGDATABASE or a target database argument is required}}"

: "${PGHOST:?PGHOST is required}"
: "${PGUSER:?PGUSER is required}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is required}"

log() { echo "[restore $(date -u +%H:%M:%S)] $*"; }

[ -f "$ENCRYPTED" ] || { log "no such backup: $ENCRYPTED"; exit 1; }

if [ -f "${ENCRYPTED}.sha256" ]; then
    log "checking the file has not been altered"
    EXPECTED=$(cat "${ENCRYPTED}.sha256")
    ACTUAL=$(sha256sum "$ENCRYPTED" | awk '{print $1}')
    if [ "$EXPECTED" != "$ACTUAL" ]; then
        log "FAILED: checksum mismatch; this backup is corrupt or was tampered with"
        exit 1
    fi
    log "checksum ok"
fi

EXISTING=$(psql -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '${TARGET}'" 2>/dev/null || true)

if [ "$EXISTING" = "1" ]; then
    TABLES=$(psql -d "$TARGET" -tAc \
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'" 2>/dev/null || echo 0)
    if [ "$TABLES" -gt 0 ] && [ "${RESTORE_FORCE:-0}" != "1" ]; then
        log "REFUSING: database '${TARGET}' already has ${TABLES} table(s)."
        log "Restore into a new database name, or set RESTORE_FORCE=1 to replace it."
        exit 1
    fi
    if [ "${RESTORE_FORCE:-0}" = "1" ]; then
        log "dropping and recreating '${TARGET}' (RESTORE_FORCE=1)"
        psql -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
                              WHERE datname = '${TARGET}' AND pid <> pg_backend_pid()" >/dev/null
        psql -d postgres -c "DROP DATABASE IF EXISTS \"${TARGET}\"" >/dev/null
        psql -d postgres -c "CREATE DATABASE \"${TARGET}\" OWNER \"${PGUSER}\"" >/dev/null
    fi
else
    log "creating database '${TARGET}'"
    psql -d postgres -c "CREATE DATABASE \"${TARGET}\" OWNER \"${PGUSER}\"" >/dev/null
fi

log "decrypting and restoring into '${TARGET}'"
printf '%s' "$BACKUP_PASSPHRASE" | gpg --batch --quiet \
    --passphrase-fd 0 --pinentry-mode loopback --decrypt "$ENCRYPTED" \
    | pg_restore --dbname="$TARGET" --no-owner --no-privileges --exit-on-error

log "verifying the restored data"
MEDS=$(psql -d "$TARGET" -tAc "SELECT count(*) FROM medications" 2>/dev/null || echo "?")
USERS=$(psql -d "$TARGET" -tAc "SELECT count(*) FROM users" 2>/dev/null || echo "?")
REVISIONS=$(psql -d "$TARGET" -tAc "SELECT count(*) FROM revisions" 2>/dev/null || echo "?")
AUDIT=$(psql -d "$TARGET" -tAc "SELECT count(*) FROM audit_log" 2>/dev/null || echo "?")
MIGRATIONS=$(psql -d "$TARGET" -tAc "SELECT count(*) FROM schema_migrations" 2>/dev/null || echo "?")

log "restored: ${MEDS} medications, ${USERS} users, ${REVISIONS} revisions, ${AUDIT} audit entries, ${MIGRATIONS} migrations"
log "done"
