#!/bin/sh
# Installs the backup schedule and then runs crond in the foreground.
set -eu

SCHEDULE="${BACKUP_SCHEDULE:-0 2 * * *}"
echo "$SCHEDULE /usr/local/bin/backup.sh >> /proc/1/fd/1 2>&1" > /etc/crontabs/root

echo "backup service ready; schedule: $SCHEDULE"
echo "run a backup now with: docker compose exec backup /usr/local/bin/backup.sh"

exec crond -f -l 8
