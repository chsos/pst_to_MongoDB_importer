# AWS S3 backup target — runbook

Copy #3 of the PST Browser backups, on a third vendor.

| # | Target | When | Contents |
|---|--------|------|----------|
| 1 | Hetzner Storage Box | nightly 03:00 | full, 7 daily / 4 weekly / 6 monthly |
| 2 | IONOS backup1 | Sundays | everything except the 122 G of PSTs |
| 3 | **AWS S3** | Sundays | full, including the PSTs — append-only |

**Why it exists.** The primary backup is a Hetzner Storage Box and production is
also Hetzner, so one suspended account takes out both. backup1 covers that for
everything *except* the PSTs — which therefore live on a single vendor today.
This closes that gap for roughly **$1/month**.

**What it protects against that the others don't.** backup1 is reachable from
prod over SSH with a key that prod holds; anyone who owns prod can wipe it. The
S3 user has `PutObject` but not `DeleteObject`, and the bucket has Object Lock,
so a fully compromised prod box can add backups but cannot destroy history.

---

## Files

| File | Purpose |
|---|---|
| `s3_setup.sh` | One-time bucket provisioning. Idempotent. |
| `s3-backup-policy.json` | Append-only IAM policy template for the backup user. |

The weekly sync itself lives in `../backup.sh`, guarded by `S3_BUCKET`.

---

## Setup

Run steps 1–2 from your laptop with an **admin** AWS identity; step 3 on prod.

### 1. Install the CLI and authenticate (laptop)

AWS CLI v2, Windows:

```bash
winget install --id Amazon.AWSCLI -e
```

Then, entering your own access key and secret — never paste credentials into a
chat or a script:

```bash
aws configure
```

Confirm it worked:

```bash
aws sts get-caller-identity
```

### 2. Create the bucket and the backup user

```bash
bash scripts/aws/s3_setup.sh
```

It prints the exact `aws iam` commands for the append-only user, pre-filled with
your bucket name and KMS key ARN. Run those; the last one prints the secret
access key **once**.

> **Object Lock can only be enabled at bucket creation.** If you need to change
> it later, that means a new bucket. Review the settings before running.

### 3. Configure prod (178.156.253.42)

```bash
ssh root@178.156.253.42
```

```bash
apt-get update && apt-get install -y awscli
aws configure          # the backup user's keys, region us-east-1
chmod 600 /root/.aws/credentials
```

Set the bucket in `scripts/backup.sh`:

```bash
S3_BUCKET="pstbrowser-backup-<account-id>"
```

### 4. Seed and verify

The first upload is ~147 G. Run it by hand before trusting cron — same metro as
Ashburn, but still allow a few hours:

```bash
DOW=7 bash /root/pst_to_MongoDB_importer/scripts/backup.sh
```

Then confirm the append-only guarantee actually holds. **This must fail with
`AccessDenied`** — if it succeeds, the policy did not apply and copy #3 is not
doing its job:

```bash
aws s3api delete-object --bucket <bucket> --key pst/<some-file>
```

---

## Layout in the bucket

| Prefix | Source | Class | Size |
|---|---|---|---|
| `pst/` | `/mnt/HC_Volume_106058598/pst_files` | Glacier IR | 122 G |
| `attachments/` | `/mnt/HC_Volume_106058598/Attachments` | Standard | 6.1 G |
| `mongodump/` | staged mongodump, all DBs | Standard | 11 G |
| `etc/` | nginx, systemd, ssl, letsencrypt | Standard | small |
| `kuma/` | Uptime Kuma sqlite snapshot + configs | Standard | 7 M |
| `apps/` | both app dirs incl. `.env` | Standard | small |

History comes from **object versions**, not dated snapshot directories — S3 has
no hardlinks, so the rsync rotation pattern doesn't apply. Old versions expire
after 30 days (90 for `pst/`, matching Glacier IR's minimum storage duration).

`aws s3 sync` runs **without** `--delete`, so files removed on prod persist in
S3. For a backup that is the desired behaviour.

---

## Restoring

Everything is instantly retrievable — Glacier *Instant* Retrieval, not the
multi-hour tiers. A full restore is roughly **$5–10** in retrieval and egress.

Current state of a prefix:

```bash
aws s3 sync s3://<bucket>/mongodump/ ./restore/mongodump/
```

A specific file as of an earlier date — list versions, then fetch one:

```bash
aws s3api list-object-versions --bucket <bucket> --prefix pst/user@example.com/
```

```bash
aws s3api get-object --bucket <bucket> --key <key> --version-id <id> ./restored.pst
```

---

## Cost

At 147 G, `us-east-1` list prices:

| Item | Monthly |
|---|---|
| `pst/` — 122 G Glacier IR | $0.49 |
| Everything else — ~25 G Standard | $0.58 |
| Requests + KMS (bucket keys on) | ~$0.05 |
| **Total** | **~$1.10** |

Ingress is free. Full-restore egress is a one-off ~$5–10.

---

## Notes and gotchas

- **KMS key is the kill switch.** Disabling `alias/pstbrowser-backup` makes every
  object unreadable immediately. The backup user is explicitly denied
  `kms:DisableKey`, so only an admin can pull it.
- **Governance vs compliance mode.** Object Lock is set to GOVERNANCE, so an
  admin with `s3:BypassGovernanceRetention` can clean up mistakes. COMPLIANCE
  would block even the root account for the full 35 days — stronger against
  ransomware, but an accidental multi-GB upload becomes unremovable and billable
  for the whole window.
- **Glacier IR's 128 KB minimum billable object size** is why `attachments/`
  stays in Standard. Many small files there would cost more in Glacier IR than
  they save.
- **Kuma alerting.** Any `[s3-*] FAILED` sets `FAIL=1`, which suppresses the
  Kuma heartbeat, which fires the existing SMS + email alert after 25h.
- **This runs Sundays only.** A Sunday when prod is down means no S3 copy that
  week; the nightly Storage Box copy still runs.
