#!/bin/bash
# PST Browser + MyNotes365 — nightly offsite backup to Hetzner Storage Box
#
# Primary:   u644073.your-storagebox.de (1 TB, Falkenstein), SSH key auth.
# Layout on the box:  backups/{daily,weekly,monthly}.YYYY-MM-DD/  — hardlink-
# rotated via rsync --link-dest, so each snapshot only costs the changed bytes.
# Sunday's daily is promoted to a weekly and the 1st of the month to a monthly
# with `cp -al` (hardlinks, near-zero cost). Keeps 7 dailies + 4 weeklies +
# 6 monthlies ≈ 6 months of history, so corruption found late is still fixable.
#
# Secondary: IONOS backup1 (74.208.191.225), a DIFFERENT vendor, so a lost or
# suspended Hetzner account cannot take out both copies. Weekly (Sunday) copy of
# the irreplaceable parts only — mongodump + /etc configs + both app dirs. The
# PSTs are deliberately excluded: 122 GB that users could re-upload.
#
# On success, pings the Uptime Kuma push monitor; if Kuma gets no ping for >25h
# it alerts (SMS + email) — no more silent failures.
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
KEEP_DAILY=7
KEEP_WEEKLY=4
KEEP_MONTHLY=6
DOW=$(date +%u)   # 1=Mon .. 7=Sun
DOM=$(date +%d)
KUMA_PUSH_URL="https://status.computerhelpsos.com/api/push/a74a2d0da8f3d9b031ca0b4f"
FAIL=0

# Uptime Kuma box — pulled from here, so that box holds no Storage Box credentials
KUMA_HOST="root@198.251.75.128"
KUMA_SSH="ssh -i /root/.ssh/kuma_ed25519 -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"

# Secondary offsite copy — IONOS backup1 (different vendor, weekly)
B1_HOST="root@74.208.191.225"
B1_SSH="ssh -i /root/.ssh/backup1_ed25519 -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"
B1_BASE="/backups/pstbrowser"
B1_KEEP=4

# Tertiary offsite copy — AWS S3 (third vendor, weekly, FULL incl. the PSTs).
#
# This exists because the PRIMARY backup is a Hetzner Storage Box and prod is
# also Hetzner: one suspended account takes out both. backup1 covers that for
# everything except the 122 G of PSTs, which until now lived on a single vendor.
#
# APPEND-ONLY BY DESIGN. The IAM user whose keys sit in /root/.aws/credentials
# has PutObject but NOT DeleteObject, and the bucket has Object Lock enabled.
# So even if this box is fully compromised, the attacker can add objects but
# cannot destroy backup history — which is the whole point of copy #3.
# History comes from S3 versioning, not from dated snapshot dirs.
# Provisioned by scripts/aws/s3_setup.sh. Set S3_BUCKET="" to disable.
S3_BUCKET="${S3_BUCKET:-pstbrowser-backup-971056407616}"
S3_REGION="${S3_REGION:-us-east-1}"

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
# Any tier will do — sort on the date field so a gap longer than the daily
# retention still links against the newest weekly/monthly instead of re-seeding.
PREV=$($SSH_CMD "$SB" ls "$REMOTE_BASE" 2>/dev/null \
       | grep -E '^(daily|weekly|monthly)\.' | grep -v "\.$DATE\$" \
       | sort -t. -k2 | tail -1 || true)
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

# ── Pull the Uptime Kuma box (monitors, notifications, cert, nginx) ───────────
# kuma.db is WAL-mode and written every 60s, so a raw file copy can be torn.
# Take a consistent snapshot through SQLite's online backup API first.
# Pulling (rather than pushing from Kuma) keeps Storage Box credentials off that
# host entirely. Losing this DB regenerates the push tokens, which would silently
# break the backup heartbeats on this box and the Plesk box.
echo "[kuma] ..."
rm -rf "$STAGING/kuma"
mkdir -p "$STAGING/kuma"
if $KUMA_SSH "$KUMA_HOST" "docker exec uptime-kuma sqlite3 /app/data/kuma.db \".backup /app/data/kuma-snapshot.db\""; then
    run_rsync "kuma-pull" -a --timeout=300 -e "$KUMA_SSH" \
        "$KUMA_HOST:/opt/uptime-kuma/data/kuma-snapshot.db" \
        "$KUMA_HOST:/opt/uptime-kuma/compose.yaml" \
        "$KUMA_HOST:/etc/nginx/sites-available" \
        "$KUMA_HOST:/etc/letsencrypt" \
        "$STAGING/kuma/"
    $KUMA_SSH "$KUMA_HOST" "rm -f /opt/uptime-kuma/data/kuma-snapshot.db"
else
    echo "[kuma] snapshot FAILED — not shipping a possibly-torn database"
    FAIL=1
fi

# ── Rsync everything to the Storage Box ───────────────────────────────────────
RS=(-a --timeout=300 -e "$SSH_CMD" "${LINKDEST[@]}")

run_rsync "pst_files"    "${RS[@]}" /mnt/HC_Volume_106058598/pst_files   "$SB:$DEST/"
run_rsync "attachments"  "${RS[@]}" /mnt/HC_Volume_106058598/Attachments "$SB:$DEST/"
run_rsync "mongodb"      "${RS[@]}" "$STAGING/mongodb_dump"              "$SB:$DEST/"
run_rsync "etc-configs"  "${RS[@]}" "$STAGING/etc"                       "$SB:$DEST/"
run_rsync "kuma"         "${RS[@]}" "$STAGING/kuma"                      "$SB:$DEST/"
run_rsync "pstbrowser"   "${RS[@]}" --exclude venv --exclude __pycache__ \
                         "$APP_DIR"                                      "$SB:$DEST/"
run_rsync "mynotes365"   "${RS[@]}" --exclude venv --exclude __pycache__ \
                         /root/mynotes365                                "$SB:$DEST/"

# ── Promote today's daily to weekly / monthly (hardlink copy, ~free) ──────────
promote() {
    local tier=$1
    if $SSH_CMD "$SB" ls "$REMOTE_BASE" 2>/dev/null | grep -q "^$tier\.$DATE\$"; then
        echo "[$tier] $tier.$DATE already exists — skipping."
        return
    fi
    echo "[$tier] promoting daily.$DATE -> $tier.$DATE ..."
    if $SSH_CMD "$SB" cp -al "$REMOTE_BASE/daily.$DATE" "$REMOTE_BASE/$tier.$DATE"; then
        echo "[$tier] done"
    else
        echo "[$tier] FAILED"
        FAIL=1
    fi
}

# Only promote a snapshot we know is complete.
if [ "$FAIL" -eq 0 ]; then
    [ "$DOW" = "7" ]  && promote weekly
    [ "$DOM" = "01" ] && promote monthly
fi

# ── Weekly secondary copy to IONOS backup1 (different vendor) ─────────────────
if [ "$DOW" = "7" ] && [ "$FAIL" -eq 0 ]; then
    echo "[backup1] weekly secondary copy ..."
    if $B1_SSH "$B1_HOST" "mkdir -p $B1_BASE/snap.$DATE"; then
        B1_PREV=$($B1_SSH "$B1_HOST" "ls -d $B1_BASE/snap.* 2>/dev/null" \
                  | grep -v "snap\.$DATE\$" | sort | tail -1 || true)
        B1_LINK=()
        if [ -n "$B1_PREV" ]; then
            B1_LINK=(--link-dest="$B1_PREV")
            echo "[backup1] hardlinking against $(basename "$B1_PREV")"
        fi
        B1RS=(-a --timeout=300 -e "$B1_SSH" "${B1_LINK[@]}")

        run_rsync "backup1-mongodb" "${B1RS[@]}" "$STAGING/mongodb_dump" \
                  "$B1_HOST:$B1_BASE/snap.$DATE/"
        run_rsync "backup1-etc"     "${B1RS[@]}" "$STAGING/etc" \
                  "$B1_HOST:$B1_BASE/snap.$DATE/"
        # 7 MB, but it is what restores the alerting — do not leave it single-vendor.
        run_rsync "backup1-kuma"    "${B1RS[@]}" "$STAGING/kuma" \
                  "$B1_HOST:$B1_BASE/snap.$DATE/"
        run_rsync "backup1-apps"    "${B1RS[@]}" --exclude venv --exclude __pycache__ \
                  "$APP_DIR" /root/mynotes365                 "$B1_HOST:$B1_BASE/snap.$DATE/"

        echo "[backup1] rotating (keep $B1_KEEP) ..."
        $B1_SSH "$B1_HOST" "cd $B1_BASE && ls -d snap.* 2>/dev/null | sort | head -n -$B1_KEEP | xargs -r rm -rf"
        echo "[backup1] $($B1_SSH "$B1_HOST" "df -h $B1_BASE | tail -1")"
    else
        echo "[backup1] UNREACHABLE — secondary copy skipped"
        FAIL=1
    fi
fi

# ── Weekly tertiary copy to AWS S3 (third vendor, append-only) ────────────────
if [ "$DOW" = "7" ] && [ -n "$S3_BUCKET" ]; then
    if ! command -v aws >/dev/null 2>&1; then
        echo "[s3] aws CLI not installed — tertiary copy skipped"
        FAIL=1
    elif ! aws sts get-caller-identity >/dev/null 2>&1; then
        echo "[s3] credentials missing/invalid — tertiary copy skipped"
        FAIL=1
    else
        # No --delete anywhere: this box must never be able to remove an object,
        # and the IAM policy would refuse anyway. Files deleted on prod linger
        # in S3, which for a backup is the behaviour we want.
        S3SYNC=(aws s3 sync --region "$S3_REGION" --only-show-errors --no-progress)

        run_s3() {
            # run_s3 <label> <src> <dst-prefix> [extra args...]
            local label=$1 src=$2 dst=$3; shift 3
            echo "[$label] ..."
            if "${S3SYNC[@]}" "$src" "s3://$S3_BUCKET/$dst" "$@"; then
                echo "[$label] done"
            else
                echo "[$label] FAILED (aws exit $?)"
                FAIL=1
            fi
        }

        # PSTs go straight to Glacier Instant Retrieval — 122 G that is written
        # once and re-read only on a restore. ~$0.49/mo instead of ~$2.80.
        run_s3 "s3-pst_files"   /mnt/HC_Volume_106058598/pst_files   pst/ \
               --storage-class GLACIER_IR
        # Attachments stay STANDARD: many small files, and Glacier IR bills a
        # 128 KB minimum per object, which would cost more than it saves here.
        run_s3 "s3-attachments" /mnt/HC_Volume_106058598/Attachments attachments/
        # mongodump stays STANDARD — it is the first thing you restore.
        run_s3 "s3-mongodb"     "$STAGING/mongodb_dump"              mongodump/
        run_s3 "s3-etc"         "$STAGING/etc"                       etc/
        run_s3 "s3-kuma"        "$STAGING/kuma"                      kuma/
        run_s3 "s3-pstbrowser"  "$APP_DIR"                           apps/pstbrowser/ \
               --exclude "venv/*" --exclude "*/__pycache__/*"
        run_s3 "s3-mynotes365"  /root/mynotes365                     apps/mynotes365/ \
               --exclude "venv/*" --exclude "*/__pycache__/*"

        echo "[s3] $(aws s3 ls "s3://$S3_BUCKET" --recursive --summarize --region "$S3_REGION" 2>/dev/null | tail -2 | tr '\n' ' ')"
    fi
fi

# ── Rotate each tier on the box ───────────────────────────────────────────────
rotate_tier() {
    local tier=$1 keep=$2
    $SSH_CMD "$SB" ls "$REMOTE_BASE" 2>/dev/null | grep "^$tier\." | sort | head -n -"$keep" | while read -r old; do
        echo "  Removing $old"
        $SSH_CMD "$SB" rm -rf "$REMOTE_BASE/$old"
    done
}

echo "Rotating (keep $KEEP_DAILY daily / $KEEP_WEEKLY weekly / $KEEP_MONTHLY monthly) ..."
rotate_tier daily   "$KEEP_DAILY"
rotate_tier weekly  "$KEEP_WEEKLY"
rotate_tier monthly "$KEEP_MONTHLY"

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
