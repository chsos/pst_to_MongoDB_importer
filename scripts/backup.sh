#!/bin/bash
# PST Browser + MyNotes365 — nightly offsite backup to Hetzner Storage Box
#
# Destination: u644073.your-storagebox.de (1 TB, Falkenstein), SSH key auth.
# Layout on the box:  backups/daily.YYYY-MM-DD/  — hardlink-rotated via
# rsync --link-dest, so each daily snapshot only costs the changed bytes.
# Keeps the last 7 dailies. On success, pings the Uptime Kuma push monitor;
# if Kuma gets no ping for >25h it alerts (SMS + email) — no more silent failures.
#
# Cron:  0 3 * * * /root/pst_to_MongoDB_importer/scripts/backup.sh >> /var/log/pstbrowser-backup.log 2>&1

set -uo pipefail

SB_USER="u644073"
SB_HOST="u644073.your-storagebox.de"
SB="$SB_USER@$SB_HOST"
SSH_CMD="ssh -p23 -i /root/.ssh/storagebox_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
REMOTE_BASE="backups"
DATE=$(date +%Y-%m-%d)
DEST="$REMOTE_BASE/daily.$DATE"
STAGING="/mnt/HC_Volume_106058598/backup_staging"
APP_DIR="/root/pst_to_MongoDB_importer"
KEEP=7
KUMA_PUSH_URL="https://status.computerhelpsos.com/api/push/a74a2d0da8f3d9b031ca0b4f"
FAIL=0

# Prevent overlapping runs (e.g. cron firing while the initial seed is running)
exec 9>/var/lock/pstbrowser-backup.lock
flock -n 9 || { echo "Another backup is already running — exiting."; exit 0; }

echo "=========================================="
echo "Offsite backup — $(date)"
echo "=========================================="

run_rsync() {
    # run_rsync <label> <rsync args...>
    local label=$1; shift
    echo "[$label] ..."
    if rsync "$@"; then
        echo "[$label] done"
    else
        echo "[$label] FAILED (rsync exit $?)"
        FAIL=1
    fi
}

# ── Find previous snapshot for hardlink rotation ──────────────────────────────
PREV=$($SSH_CMD "$SB" ls "$REMOTE_BASE" 2>/dev/null | grep '^daily\.' | grep -v "daily.$DATE" | sort | tail -1 || true)
LINKDEST=()
if [ -n "$PREV" ]; then
    LINKDEST=(--link-dest="../$PREV")
    echo "Hardlinking unchanged files against $PREV"
else
    echo "No previous snapshot — full initial seed."
fi

# ── MongoDB dump (all databases: pst_emails* + mynotes365) ────────────────────
echo "[mongodump] ..."
MONGO_URI=$(grep -E '^MONGO_URI=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2- || true)
rm -rf "$STAGING/mongodb_dump"
mkdir -p "$STAGING"
if mongodump --quiet ${MONGO_URI:+--uri="$MONGO_URI"} --out="$STAGING/mongodb_dump"; then
    echo "[mongodump] done — $(du -sh "$STAGING/mongodb_dump" | cut -f1)"
else
    echo "[mongodump] FAILED"
    FAIL=1
fi

# ── Stage /etc configs (nginx, systemd units, SSL certs, letsencrypt) ─────────
rm -rf "$STAGING/etc"
mkdir -p "$STAGING/etc"
cp -a --parents /etc/nginx/sites-available \
                /etc/ssl/mynotes365 \
                /etc/ssl/pstbrowser \
                /etc/letsencrypt \
                /etc/systemd/system/mynotes365.service \
                /etc/systemd/system/pstbrowser.service \
                "$STAGING/etc/" 2>/dev/null || echo "  (some /etc paths missing — continuing)"

# ── Rsync everything to the Storage Box ───────────────────────────────────────
RS=(-a --timeout=300 -e "$SSH_CMD" "${LINKDEST[@]}")

run_rsync "pst_files"    "${RS[@]}" /mnt/HC_Volume_106058598/pst_files   "$SB:$DEST/"
run_rsync "attachments"  "${RS[@]}" /mnt/HC_Volume_106058598/Attachments "$SB:$DEST/"
run_rsync "mongodb"      "${RS[@]}" "$STAGING/mongodb_dump"              "$SB:$DEST/"
run_rsync "etc-configs"  "${RS[@]}" "$STAGING/etc"                       "$SB:$DEST/"
run_rsync "pstbrowser"   "${RS[@]}" --exclude venv --exclude __pycache__ \
                         "$APP_DIR"                                      "$SB:$DEST/"
run_rsync "mynotes365"   "${RS[@]}" --exclude venv --exclude __pycache__ \
                         /root/mynotes365                                "$SB:$DEST/"

# ── Rotate: keep last $KEEP dailies on the box ────────────────────────────────
echo "Rotating (keep $KEEP) ..."
$SSH_CMD "$SB" ls "$REMOTE_BASE" 2>/dev/null | grep '^daily\.' | sort | head -n -$KEEP | while read -r old; do
    echo "  Removing $old"
    $SSH_CMD "$SB" rm -rf "$REMOTE_BASE/$old"
done

# ── Report ────────────────────────────────────────────────────────────────────
echo "Remote usage: $($SSH_CMD "$SB" df -h 2>/dev/null | tail -1)"
if [ "$FAIL" -eq 0 ]; then
    echo "Backup OK — pinging Kuma heartbeat."
    curl -fsS -m 15 "$KUMA_PUSH_URL?status=up&msg=backup-ok" >/dev/null || echo "  (Kuma ping failed — check monitor)"
else
    echo "BACKUP HAD FAILURES — skipping Kuma ping so the alert fires."
fi
echo "Done — $(date)"
exit $FAIL
