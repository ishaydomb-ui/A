#!/usr/bin/env bash
#
# התקנת Immich בשרת לינוקס.
# שימוש:   ./install.sh [נתיב-לאחסון-התמונות]
# דוגמה:   ./install.sh /srv/immich/library
#
set -euo pipefail

cd "$(dirname "$0")"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mשגיאה:\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. בדיקות מוקדמות -----------------------------------------------------
command -v docker >/dev/null 2>&1 || die "Docker לא מותקן. ראה https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "חסר התוסף 'docker compose' (v2). ה-docker-compose הישן אינו נתמך."
docker info >/dev/null 2>&1 || die "אין הרשאה להריץ docker. הרץ עם sudo או הוסף את המשתמש לקבוצת docker."

TOTAL_RAM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)
if [ "$TOTAL_RAM_MB" -gt 0 ] && [ "$TOTAL_RAM_MB" -lt 5800 ]; then
  warn "בשרת יש כ-${TOTAL_RAM_MB}MB RAM. Immich דורש 6GB (מומלץ 8GB)."
  warn "אפשר להמשיך, אבל ייתכן שיהיה צורך לכבות את שירות ה-Machine Learning."
fi

# --- 2. יצירת .env ---------------------------------------------------------
if [ ! -f .env ]; then
  say "יוצר קובץ .env"
  cp .env.example .env
  DB_PASSWORD=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32)
  sed -i "s|^DB_PASSWORD=.*|DB_PASSWORD=${DB_PASSWORD}|" .env
  say "נוצרה סיסמת מסד נתונים אקראית."
else
  say "קובץ .env כבר קיים – משאיר אותו כמו שהוא."
fi

# --- 3. נתיב האחסון --------------------------------------------------------
if [ $# -ge 1 ]; then
  UPLOAD_PATH="$1"
  say "מגדיר את אחסון התמונות ל: $UPLOAD_PATH"
  mkdir -p "$UPLOAD_PATH"
  sed -i "s|^UPLOAD_LOCATION=.*|UPLOAD_LOCATION=${UPLOAD_PATH}|" .env
fi

UPLOAD_LOCATION=$(grep -E '^UPLOAD_LOCATION=' .env | cut -d= -f2-)
DB_DATA_LOCATION=$(grep -E '^DB_DATA_LOCATION=' .env | cut -d= -f2-)
mkdir -p "$UPLOAD_LOCATION" "$DB_DATA_LOCATION"

case "$(df -T "$UPLOAD_LOCATION" 2>/dev/null | awk 'NR==2{print $2}')" in
  nfs*|cifs|smb*|fuse.sshfs)
    warn "אחסון התמונות נמצא על שיתוף רשת. זה נתמך – אך ודא ש-DB_DATA_LOCATION מקומי!" ;;
esac
case "$(df -T "$DB_DATA_LOCATION" 2>/dev/null | awk 'NR==2{print $2}')" in
  nfs*|cifs|smb*|fuse.sshfs)
    die "DB_DATA_LOCATION נמצא על שיתוף רשת – Postgres יישבר. הצבע אותו על דיסק מקומי." ;;
esac

AVAIL_GB=$(df -BG --output=avail "$UPLOAD_LOCATION" 2>/dev/null | awk 'NR==2{gsub("G","");print $1}')
[ -n "${AVAIL_GB:-}" ] && say "מקום פנוי ב-${UPLOAD_LOCATION}: ${AVAIL_GB}GB"

# --- 4. הפעלה --------------------------------------------------------------
say "מוריד את קונטיינרים של Immich (עשוי לקחת כמה דקות)…"
docker compose pull
say "מפעיל…"
docker compose up -d

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
say "Immich עלה! פתח בדפדפן:  http://${IP:-<כתובת-השרת>}:2283"
say "במסך הראשון צור את משתמש האדמין. באפליקציית האייפון הזן בדיוק את אותה כתובת."
echo
say "מעקב אחרי הלוגים:  docker compose logs -f immich-server"
