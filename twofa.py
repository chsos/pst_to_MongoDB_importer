"""Two-factor authentication by SMS, shared by mynotes365 and pstbrowser.

Deliberately self-contained: it takes the users collection and a send-SMS
callable, and touches nothing else. Both apps keep their own auth routes and
templates; only this logic is shared, so a fix lands in one place.

The factor is SMS because that is what was chosen — worth recording that it is
the weakest of the common options. SIM-swap and SS7 interception are real, and
a code sent to a hijacked number is a code the attacker reads. It is still far
better than a password alone, but it should not be mistaken for TOTP.

Storage, all on the user document:

    twofa_enabled     bool
    twofa_phone       E.164, verified before enabling
    twofa_code        sha256(uid:code) — never the code itself
    twofa_expires     datetime
    twofa_attempts    int, per challenge
    twofa_sends       [datetime] within the last hour, for rate limiting
    twofa_recovery    [sha256(uid:code)] single-use, popped when used
"""

import datetime
import hashlib
import hmac
import secrets

CODE_TTL = datetime.timedelta(minutes=10)
MAX_ATTEMPTS = 5
MAX_SENDS_PER_HOUR = 5
RECOVERY_COUNT = 10
CHALLENGE_MAX_AGE = datetime.timedelta(minutes=15)   # half-finished login expires


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _hash(uid, code):
    """Salted by the account id, so an identical code on two accounts does not
    produce an identical hash."""
    clean = "".join(ch for ch in str(code) if ch.isalnum()).upper()
    return hashlib.sha256(f"{uid}:{clean}".encode("utf-8")).hexdigest()


def is_enabled(users, uid):
    doc = users.find_one({"_id": uid}, {"twofa_enabled": 1}) or {}
    return bool(doc.get("twofa_enabled"))


def status(users, uid):
    doc = users.find_one({"_id": uid},
                         {"twofa_enabled": 1, "twofa_phone": 1, "twofa_recovery": 1}) or {}
    phone = doc.get("twofa_phone") or ""
    return {
        "enabled": bool(doc.get("twofa_enabled")),
        "phone_hint": _mask(phone),
        "recovery_left": len(doc.get("twofa_recovery") or []),
    }


def _mask(phone):
    """Enough to recognise your own number, not enough to learn someone's."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    return f"•••• {digits[-4:]}" if len(digits) >= 4 else ""


# ── Challenge ────────────────────────────────────────────────────────────────

def send_code(users, uid, send_sms, app_name="MyNotes365"):
    """Generate and text a code. Returns (ok, message)."""
    doc = users.find_one({"_id": uid}, {"twofa_phone": 1, "twofa_sends": 1})
    if not doc or not doc.get("twofa_phone"):
        return False, "No mobile number is set up for this account."

    now = _now()
    recent = [t for t in (doc.get("twofa_sends") or [])
              if t > now - datetime.timedelta(hours=1)]
    if len(recent) >= MAX_SENDS_PER_HOUR:
        return False, "Too many codes requested. Try again in an hour."

    code = f"{secrets.randbelow(1000000):06d}"
    if not send_sms(doc["twofa_phone"],
                    f"{code} is your {app_name} sign-in code. It expires in 10 "
                    f"minutes. If you did not try to sign in, change your password. "
                    f"Reply STOP to unsubscribe."):
        return False, "Could not send the code. Try again shortly."

    users.update_one({"_id": uid}, {"$set": {
        "twofa_code": _hash(uid, code),
        "twofa_expires": now + CODE_TTL,
        "twofa_attempts": 0,
        "twofa_sends": recent + [now],
    }})
    return True, f"Code sent to {_mask(doc['twofa_phone'])}."


def check_code(users, uid, code):
    """Verify a challenge code or a recovery code. Returns (ok, message).

    A recovery code is accepted here too: someone locked out of their phone is
    exactly who needs one, and making them find a different form would not make
    anything safer.
    """
    doc = users.find_one({"_id": uid}, {
        "twofa_code": 1, "twofa_expires": 1, "twofa_attempts": 1, "twofa_recovery": 1})
    if not doc:
        return False, "Sign in again."

    attempts = doc.get("twofa_attempts", 0)
    if attempts >= MAX_ATTEMPTS:
        return False, "Too many wrong codes. Request a new one."

    given = _hash(uid, code)

    for stored in (doc.get("twofa_recovery") or []):
        if hmac.compare_digest(stored, given):
            # Single use — burn it, and clear any live challenge with it.
            users.update_one({"_id": uid}, {
                "$pull": {"twofa_recovery": stored},
                "$unset": {"twofa_code": "", "twofa_expires": "", "twofa_attempts": ""}})
            left = len(doc.get("twofa_recovery") or []) - 1
            return True, (f"Recovery code accepted. {left} left — "
                          f"generate new ones from account settings.")

    if not doc.get("twofa_code"):
        return False, "Request a code first."
    if doc.get("twofa_expires", _now()) < _now():
        return False, "That code has expired. Request a new one."

    if not hmac.compare_digest(doc["twofa_code"], given):
        users.update_one({"_id": uid}, {"$inc": {"twofa_attempts": 1}})
        left = MAX_ATTEMPTS - attempts - 1
        return False, (f"That code is not right. {left} attempt"
                       f"{'' if left == 1 else 's'} left."
                       if left > 0 else "That code is not right. Request a new one.")

    users.update_one({"_id": uid}, {
        "$unset": {"twofa_code": "", "twofa_expires": "", "twofa_attempts": ""}})
    return True, ""


# ── Enrolment ────────────────────────────────────────────────────────────────

def enable(users, uid, phone):
    """Turn 2FA on and hand back fresh recovery codes. The caller must have
    verified the number first — this does not check."""
    codes = ["-".join(secrets.token_hex(2).upper() for _ in range(2))
             for _ in range(RECOVERY_COUNT)]
    users.update_one({"_id": uid}, {"$set": {
        "twofa_enabled": True,
        "twofa_phone": phone,
        "twofa_recovery": [_hash(uid, c) for c in codes],
        "twofa_enabled_at": _now(),
    }})
    # Returned once, in plain text, and never recoverable afterwards.
    return codes


def regenerate_recovery(users, uid):
    codes = ["-".join(secrets.token_hex(2).upper() for _ in range(2))
             for _ in range(RECOVERY_COUNT)]
    users.update_one({"_id": uid},
                     {"$set": {"twofa_recovery": [_hash(uid, c) for c in codes]}})
    return codes


def disable(users, uid):
    users.update_one({"_id": uid}, {
        "$set": {"twofa_enabled": False},
        "$unset": {"twofa_code": "", "twofa_expires": "", "twofa_attempts": "",
                   "twofa_recovery": "", "twofa_phone": "", "twofa_enabled_at": ""}})


def challenge_expired(started_at):
    """A half-finished sign-in should not sit open indefinitely."""
    return not started_at or (_now() - started_at) > CHALLENGE_MAX_AGE
