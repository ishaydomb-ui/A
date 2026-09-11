#!/bin/sh
# ---------------------------------------------------------------------------
# Encrypted PostgreSQL backup.
#
# Produces a compressed custom-format dump, encrypts it with a symmetric
# passphrase, verifies the encrypted file can be read back, and prunes dumps
# older than the retention period.
#
# The passphrase comes from the environment and is never written to disk or
# passed on a command line where it would show up in the process list.
# ---------------------------------------------------------------------------
set -eu

: "${PGHOST:?PGHOST is required}"
: "${PGUSER:?PGUSER is required}"
: "${PGDATABASE:?PGDATABASE is required}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is required}"

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BASENAME="${PGDATABASE}-${STAMP}"
DUMP="${BACKUP_DIR}/${BASENAME}.dump"
ENCRYPTED="${DUMP}.gpg"

mkdir -p "$BACKUP_DIR"
# Backups contain everything; nobody but the owner should be able to read them.
chmod 700 "$BACKUP_DIR"

log() { echo "[backup $(date -u +%H:%M:%S)] $*"; }

cleanup() {
    # The plaintext dump must never be left behind, including on failure.
    rm -f "$DUMP"
}
trap cleanup EXIT INT TERM

log "dumping ${PGDATABASE} from ${PGHOST}"
pg_dump --format=custom --compress=9 --no-owner --no-privileges --file="$DUMP"

DUMP_BYTES=$(wc -c < "$DUMP")
if [ "$DUMP_BYTES" -lt 1024 ]; then
    log "FAILED: dump is only ${DUMP_BYTES} bytes, which cannot be a real database"
    exit 1
fi
log "dump written (${DUMP_BYTES} bytes)"

log "encrypting"
printf '%s' "$BACKUP_PASSPHRASE" | gpg --batch --yes --quiet \
    --passphrase-fd 0 --pinentry-mode loopback \
    --symmetric --cipher-algo AES256 --s2k-digest-algo SHA512 \
    --output "$ENCRYPTED" "$DUMP"
chmod 600 "$ENCRYPTED"

# A backup that cannot be decrypted is not a backup. Verify before pruning.
log "verifying the encrypted file decrypts and is a valid dump"
if ! printf '%s' "$BACKUP_PASSPHRASE" | gpg --batch --quiet \
        --passphrase-fd 0 --pinentry-mode loopback --decrypt "$ENCRYPTED" 2>/dev/null \
        | pg_restore --list > /dev/null 2>&1; then
    log "FAILED: the encrypted backup did not verify; keeping it for inspection"
    exit 1
fi

sha256sum "$ENCRYPTED" | awk '{print $1}' > "${ENCRYPTED}.sha256"
log "verified: $(basename "$ENCRYPTED")"

# Imported workbooks are part of the audit trail, so they are captured too.
if [ -d /data/imports ]; then
    IMPORTS="${BACKUP_DIR}/imports-${STAMP}.tar.gz.gpg"
    # The passphrase goes in on a file descriptor, never as an argument, so it
    # cannot be read out of the process list.
    if tar -czf - -C /data imports 2>/dev/null | gpg --batch --yes --quiet \
            --passphrase-fd 3 --pinentry-mode loopback \
            --symmetric --cipher-algo AES256 --output "$IMPORTS" \
            3<<-PASSPHRASE
	${BACKUP_PASSPHRASE}
	PASSPHRASE
    then
        chmod 600 "$IMPORTS"
        log "archived imported workbooks"
    else
        log "warning: could not archive imported workbooks"
    fi
fi

log "pruning backups older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR" -type f -name '*.gpg' -mtime "+${RETENTION_DAYS}" -print -delete || true
find "$BACKUP_DIR" -type f -name '*.sha256' -mtime "+${RETENTION_DAYS}" -delete || true

REMAINING=$(find "$BACKUP_DIR" -type f -name '*.dump.gpg' | wc -l)
log "done; ${REMAINING} database backup(s) retained"
