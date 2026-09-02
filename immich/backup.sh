#!/usr/bin/env bash
#
# גיבוי Immich ליעד חיצוני (דיסק USB, NAS, נקודת עגינה כלשהי).
# שימוש:  ./backup.sh /mnt/backup-drive/immich
#
# מה מגובה:
#   * כל תיקיית UPLOAD_LOCATION – התמונות והסרטונים המקוריים,
#     התמונות הממוזערות, וגם backups/ שבו Immich שומר dump יומי של מסד הנתונים.
# מומלץ להריץ מ-cron, למשל כל לילה ב-04:00:
#   0 4 * * * /srv/immich/backup.sh /mnt/backup-drive/immich >> /var/log/immich-backup.log 2>&1
#
set -euo pipefail
cd "$(dirname "$0")"

DEST="${1:-}"
[ -n "$DEST" ] || { echo "שימוש: $0 <תיקיית-יעד>" >&2; exit 1; }
[ -f .env ]    || { echo "לא נמצא קובץ .env" >&2; exit 1; }

UPLOAD_LOCATION=$(grep -E '^UPLOAD_LOCATION=' .env | cut -d= -f2-)
[ -d "$UPLOAD_LOCATION" ] || { echo "לא נמצאה התיקייה $UPLOAD_LOCATION" >&2; exit 1; }

echo "==> מייצר dump טרי של מסד הנתונים"
docker compose exec -T database \
  pg_dumpall --clean --if-exists --username="$(grep -E '^DB_USERNAME=' .env | cut -d= -f2-)" \
  | gzip > "${UPLOAD_LOCATION%/}/backups/manual-$(date +%Y%m%d-%H%M%S).sql.gz"

echo "==> מסנכרן ל: $DEST"
mkdir -p "$DEST"
rsync -aH --delete --info=progress2 "${UPLOAD_LOCATION%/}/" "${DEST%/}/"

echo "==> הגיבוי הסתיים בהצלחה: $(date)"
