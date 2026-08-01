#!/bin/bash
# PST Browser — daily backup script
# Backs up Attachments/ and pst_files/ to /root/backups/pstbrowser/
# Keeps the last 7 daily snapshots. Run via cron.
#
# Setup:
#   chmod +x /root/pst_to_MongoDB_importer/scripts/backup.sh
#   crontab -e
#   Add:  0 3 * * * /root/pst_to_MongoDB_importer/scripts/backup.sh >> /var/log/pstbrowser-backup.log 2>&1

set -euo pipefail

APP_DIR="/root/pst_to_MongoDB_importer"
BACKUP_ROOT="/root/backups/pstbrowser"
DATE=$(date +%Y-%m-%d)
DEST="$BACKUP_ROOT/$DATE"
KEEP_DAYS=7

echo "=========================================="
echo "PST Browser backup — $(date)"
echo "=========================================="

mkdir -p "$DEST"

# ── Attachments ────────────────────────────────
if [ -d "$APP_DIR/Attachments" ]; then
    echo "[1/3] Backing up Attachments/ ..."
    rsync -a --delete "$APP_DIR/Attachments/" "$DEST/Attachments/"
    echo "      Done — $(du -sh "$DEST/Attachments" | cut -f1)"
else
    echo "[1/3] Attachments/ not found, skipping."
fi

# ── PST files ──────────────────────────────────
if [ -d "$APP_DIR/pst_files" ]; then
    echo "[2/3] Backing up pst_files/ ..."
    rsync -a --delete "$APP_DIR/pst_files/" "$DEST/pst_files/"
    echo "      Done — $(du -sh "$DEST/pst_files" | cut -f1)"
else
    echo "[2/3] pst_files/ not found, skipping."
fi

# ── MongoDB dump ───────────────────────────────
echo "[3/4] Dumping MongoDB (all databases, incl. mynotes365) ..."
# Auth credentials come from MONGO_URI in the app's .env (if set)
MONGO_URI=$(grep -E '^MONGO_URI=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2- || true)
if mongodump --quiet ${MONGO_URI:+--uri="$MONGO_URI"} --out="$DEST/mongodb_dump"; then
    echo "      Done — $(du -sh "$DEST/mongodb_dump" | cut -f1)"
else
    echo "      WARNING: mongodump failed"
fi

# ── mynotes365 app, SSL & config ──────────────
echo "[4/4] Backing up mynotes365 app & config ..."
mkdir -p "$DEST/mynotes365"
if [ -d /root/mynotes365 ]; then
    rsync -a --delete --exclude venv --exclude __pycache__ /root/mynotes365/ "$DEST/mynotes365/app/"
fi
[ -d /etc/ssl/mynotes365 ] && rsync -a --delete /etc/ssl/mynotes365/ "$DEST/mynotes365/ssl/"
[ -f /etc/nginx/sites-available/mynotes365 ] && cp /etc/nginx/sites-available/mynotes365 "$DEST/mynotes365/nginx-vhost"
[ -f /etc/systemd/system/mynotes365.service ] && cp /etc/systemd/system/mynotes365.service "$DEST/mynotes365/"
[ -d /etc/letsencrypt ] && rsync -a --delete /etc/letsencrypt/ "$DEST/letsencrypt/"
echo "      Done — $(du -sh "$DEST/mynotes365" | cut -f1) (+ letsencrypt $(du -sh "$DEST/letsencrypt" 2>/dev/null | cut -f1 || echo 0))"

# ── Rotate old backups ────────────────────────
echo "Rotating backups older than $KEEP_DAYS days ..."
find "$BACKUP_ROOT" -maxdepth 1 -type d -name "????-??-??" | sort | head -n -$KEEP_DAYS | while read -r old; do
    echo "  Removing $old"
    rm -rf "$old"
done

echo ""
echo "Backup complete — stored in $DEST"
echo "Total backup size: $(du -sh "$BACKUP_ROOT" | cut -f1)"
