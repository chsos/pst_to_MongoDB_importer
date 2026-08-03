#!/bin/bash
# PST Browser — provision the AWS S3 backup bucket (one-time)
#
# Creates copy #3 of the backups, on a third vendor:
#
#   Primary    Hetzner Storage Box  (nightly, full, 7d/4w/6m)
#   Secondary  IONOS backup1        (weekly, no PSTs)
#   Tertiary   THIS — AWS S3        (weekly, full, append-only)
#
# WHY THIS EXISTS: the primary backup is a Hetzner Storage Box and production
# is also Hetzner, so one suspended account takes out both. backup1 covers that
# for everything except the 122 G of PSTs — which therefore live on exactly one
# vendor today. This closes that, for about $1/month.
#
# Region us-east-1: prod is `ubuntu-4gb-ash-1` = Hetzner Ashburn, VA, so the
# 147 G seed uploads across the same metro and AWS charges nothing for ingress.
#
# SECURITY MODEL — the point of copy #3 is surviving a compromise of prod, so
# prod is deliberately not trusted with deletion:
#   - Object Lock (GOVERNANCE, 35 days) — objects cannot be deleted or
#     overwritten before expiry. Overriding needs s3:BypassGovernanceRetention,
#     which the backup user does NOT have.
#   - The backup IAM user gets PutObject/ListBucket/GetObject and NOT
#     DeleteObject, with an explicit Deny on deletes for good measure.
#   - SSE-KMS under a customer-managed key. Disabling that key instantly makes
#     every object unreadable — a kill switch AWS's default key cannot give you.
#   - Versioning on, so history comes from object versions rather than the
#     dated snapshot dirs the rsync targets use.
#
# NOTE: Object Lock can ONLY be enabled when the bucket is created. If you ever
# need to change that, it means making a new bucket. Get it right here.
#
# Idempotent: re-running skips whatever already exists.
#
# Prerequisites (these need YOUR credentials — this script never handles keys):
#   1. aws configure                → access key + secret for an admin user
#   2. aws sts get-caller-identity  → confirms the CLI is authenticated
#
# Usage:  ./s3_setup.sh [bucket-name]

set -uo pipefail

REGION="${AWS_REGION:-us-east-1}"
LOCK_DAYS="${LOCK_DAYS:-35}"
NAME="pstbrowser-backup"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Preflight ────────────────────────────────────────────────────────────────
command -v aws >/dev/null 2>&1 || {
    echo "ERROR: aws CLI not found. Install it, then run 'aws configure'."; exit 1; }

ACCOUNT_ID=$(command aws sts get-caller-identity --query Account --output text 2>/dev/null)
if [[ -z "$ACCOUNT_ID" || "$ACCOUNT_ID" == "None" ]]; then
    echo "ERROR: AWS CLI is not authenticated. Run 'aws configure' first."; exit 1
fi

BUCKET="${1:-$NAME-$ACCOUNT_ID}"    # bucket names are globally unique across AWS
aws() { command aws --region "$REGION" "$@"; }

echo "=========================================="
echo "S3 backup bucket — provisioning $(date)"
echo "  Account : $ACCOUNT_ID"
echo "  Region  : $REGION"
echo "  Bucket  : $BUCKET"
echo "  Lock    : GOVERNANCE, $LOCK_DAYS days"
echo "=========================================="

# ── 1. Customer-managed KMS key ──────────────────────────────────────────────
echo "[1/8] KMS key..."
KEY_ARN=$(aws kms describe-key --key-id "alias/$NAME" \
          --query KeyMetadata.Arn --output text 2>/dev/null)
if [[ -z "$KEY_ARN" || "$KEY_ARN" == "None" ]]; then
    KEY_ID=$(aws kms create-key \
        --description "PST Browser S3 backup encryption" \
        --tags TagKey=Project,TagValue=pstbrowser \
        --query KeyMetadata.KeyId --output text) || exit 1
    aws kms create-alias --alias-name "alias/$NAME" --target-key-id "$KEY_ID"
    aws kms enable-key-rotation --key-id "$KEY_ID"
    KEY_ARN=$(aws kms describe-key --key-id "$KEY_ID" --query KeyMetadata.Arn --output text)
    echo "      created $KEY_ARN (annual rotation on)"
else
    echo "      exists  $KEY_ARN"
fi

# ── 2. Bucket, WITH Object Lock ──────────────────────────────────────────────
# --object-lock-enabled-for-bucket also turns versioning on; both are required
# and neither can be retrofitted later.
# Gotcha: us-east-1 is the API default and REJECTS an explicit
# LocationConstraint; every other region REQUIRES one.
echo "[2/8] Bucket..."
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
    echo "      exists — NOTE: if it predates this script, verify Object Lock is on"
else
    if [[ "$REGION" == "us-east-1" ]]; then
        aws s3api create-bucket --bucket "$BUCKET" --object-lock-enabled-for-bucket || exit 1
    else
        aws s3api create-bucket --bucket "$BUCKET" --object-lock-enabled-for-bucket \
            --create-bucket-configuration "LocationConstraint=$REGION" || exit 1
    fi
    echo "      created (Object Lock + versioning enabled)"
fi

# ── 3. Block ALL public access ───────────────────────────────────────────────
# Customer mailboxes. Nothing here is ever public, under any circumstances.
echo "[3/8] Blocking public access..."
aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# ── 4. Default encryption (SSE-KMS, our own key) ─────────────────────────────
# BucketKeyEnabled cuts KMS request charges by ~99% — without it, every object
# PUT is a billed KMS call and 100k attachments gets expensive.
echo "[4/8] Default encryption..."
aws s3api put-bucket-encryption --bucket "$BUCKET" \
    --server-side-encryption-configuration "{
        \"Rules\": [{
            \"ApplyServerSideEncryptionByDefault\": {
                \"SSEAlgorithm\": \"aws:kms\",
                \"KMSMasterKeyID\": \"$KEY_ARN\"
            },
            \"BucketKeyEnabled\": true
        }]
    }"

# ── 5. Object Lock default retention ─────────────────────────────────────────
# GOVERNANCE (not COMPLIANCE): an admin holding s3:BypassGovernanceRetention can
# still clean up a mistake. The backup user cannot. COMPLIANCE would block even
# the root account for the full window — stronger, but you would be paying to
# store any accidental multi-GB upload for 35 days with no way out.
echo "[5/8] Object Lock retention..."
aws s3api put-object-lock-configuration --bucket "$BUCKET" \
    --object-lock-configuration "{
        \"ObjectLockEnabled\": \"Enabled\",
        \"Rule\": {\"DefaultRetention\": {\"Mode\": \"GOVERNANCE\", \"Days\": $LOCK_DAYS}}
    }"

# ── 6. Lifecycle ─────────────────────────────────────────────────────────────
# Old VERSIONS are expired; current objects are never touched. The 90-day
# figure for pst/ is not arbitrary — Glacier IR bills a 90-day minimum, so
# deleting a version sooner incurs an early-deletion charge for the remainder.
# Everything else is STANDARD with no minimum, so 30 days is fine there.
echo "[6/8] Lifecycle rules..."
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" \
    --lifecycle-configuration '{
        "Rules": [
            {
                "ID": "expire-old-pst-versions",
                "Status": "Enabled",
                "Filter": {"Prefix": "pst/"},
                "NoncurrentVersionExpiration": {"NoncurrentDays": 90}
            },
            {
                "ID": "expire-old-versions",
                "Status": "Enabled",
                "Filter": {"Prefix": ""},
                "NoncurrentVersionExpiration": {"NoncurrentDays": 30}
            },
            {
                "ID": "abort-incomplete-multipart",
                "Status": "Enabled",
                "Filter": {"Prefix": ""},
                "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}
            }
        ]
    }'
# That last rule matters more than it looks: multi-GB PSTs upload as multipart,
# and a failed upload leaves orphaned parts you are billed for but cannot see
# with `aws s3 ls`.

# ── 7. Tags ──────────────────────────────────────────────────────────────────
echo "[7/8] Tagging..."
aws s3api put-bucket-tagging --bucket "$BUCKET" \
    --tagging 'TagSet=[{Key=Project,Value=pstbrowser},{Key=ManagedBy,Value=s3_setup.sh}]'

# ── 8. Verify ────────────────────────────────────────────────────────────────
echo "[8/8] Verifying..."
echo "  Versioning : $(aws s3api get-bucket-versioning --bucket "$BUCKET" --query Status --output text)"
echo "  Encryption : $(aws s3api get-bucket-encryption --bucket "$BUCKET" \
                        --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.SSEAlgorithm' \
                        --output text)"
echo "  ObjectLock : $(aws s3api get-object-lock-configuration --bucket "$BUCKET" \
                        --query 'ObjectLockConfiguration.Rule.DefaultRetention.[Mode,Days]' \
                        --output text 2>/dev/null)"
echo "  Lifecycle  : $(aws s3api get-bucket-lifecycle-configuration --bucket "$BUCKET" \
                        --query 'length(Rules)' --output text) rules"
echo "  PublicBlk  : $(aws s3api get-public-access-block --bucket "$BUCKET" \
                        --query 'PublicAccessBlockConfiguration.BlockPublicAcls' --output text)"

cat <<EOF

==========================================
Bucket ready: s3://$BUCKET
==========================================

NEXT — create the append-only backup user. Run these yourself; the last
command prints the secret key exactly once, and I never see or handle it.

  sed -e "s|BUCKET_NAME|$BUCKET|g" -e "s|KMS_KEY_ARN|$KEY_ARN|g" \\
      $SCRIPT_DIR/s3-backup-policy.json > /tmp/pol.json

  aws iam create-user --user-name pstbrowser-backup
  aws iam put-user-policy --user-name pstbrowser-backup \\
      --policy-name pstbrowser-s3-append-only --policy-document file:///tmp/pol.json
  aws iam create-access-key --user-name pstbrowser-backup

  rm -f /tmp/pol.json

Then ON THE PROD BOX (178.156.253.42):

  apt-get install -y awscli          # or the v2 installer, see README.md
  aws configure                      # paste the two keys from above, region $REGION
  chmod 600 /root/.aws/credentials

  # enable the tertiary target in scripts/backup.sh:
  #   S3_BUCKET="$BUCKET"

  # seed it once by hand before trusting cron (147 G, allow a few hours):
  DOW=7 bash scripts/backup.sh

VERIFY the append-only guarantee actually holds — this MUST fail:

  aws s3 rm s3://$BUCKET/pst/ --recursive --dryrun   # lists, harmless
  aws s3api delete-object --bucket $BUCKET --key <some-key>   # expect AccessDenied

==========================================
EOF
