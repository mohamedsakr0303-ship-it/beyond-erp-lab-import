"""The app lock: a second layer after sign-in, on this device — a PIN (required) and, where the device has one,
fingerprint / face (erp/webauthn.py).

The person signs in once with their password; the device remembers them (the session). The app then locks:
* when it is opened again (the page asks for it: a fresh start, not a refresh),
* when it comes back after being in the background longer than their lock time (5 minutes by default),
* when nothing has reached the app for that long (here, as a backstop: a phone that slept stops talking to it),
and every call but these lock ones is refused with 423 until the PIN or fingerprint / face opens it again.
Wrong PINs: after 5, a wait that doubles each time; after 10, the session ends (sign in with the password again).
Everything here stays on the device: never shared with the workspace (erp/cloud.py LOCAL_ONLY / LOCAL_TABLES).
"""
import datetime as _dt
import json
import re
import secrets

from . import auth, webauthn
from .accounting import ValidationError

FREE = re.compile(r"^/api/(lock(?:/.*)?|logout)$")      # what a locked session may still call
FRESH_SECONDS = 600          # a password sign-in this recent may set a new PIN without the old one ("forgot my PIN")
CHALLENGE_SECONDS = 180
MAX_FAILS = 10
MINUTES = (1, 2, 5, 10, 15, 30, 60)

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_lock (
    user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    pin_hash     TEXT NOT NULL DEFAULT '',
    pin_len      INTEGER NOT NULL DEFAULT 4,
    fails        INTEGER NOT NULL DEFAULT 0,
    wait_until   TEXT NOT NULL DEFAULT '',
    minutes      INTEGER NOT NULL DEFAULT 5,
    challenge    TEXT NOT NULL DEFAULT '',
    challenge_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS lock_keys (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cred_id    TEXT NOT NULL UNIQUE,
    key        TEXT NOT NULL,
    count      INTEGER NOT NULL DEFAULT 0,
    label      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    used_at    TEXT NOT NULL DEFAULT ''
);
"""


def _now():
    return _dt.datetime.utcnow().replace(microsecond=0)


def _iso(t):
    return t.isoformat(sep=" ")


def _parse(s):
    try:
        return _dt.datetime.fromisoformat(str(s))
    except ValueError:
        return None


def _row(db, uid):
    r = db.one("SELECT * FROM app_lock WHERE user_id=?", (uid,))
    if not r:
        db.x("INSERT OR IGNORE INTO app_lock(user_id) VALUES (?)", (uid,))
        r = db.one("SELECT * FROM app_lock WHERE user_id=?", (uid,))
    return r


def _session(db, token):
    return db.one("SELECT token, user_id, locked, seen, born FROM sessions WHERE token=?", (token,)) if token else None


def state(db, user, token):
    r = _row(db, user["id"])
    s = _session(db, token) or {}
    wait = _parse(r["wait_until"])
    born = _parse(s.get("born") or "")
    return {"pin_set": bool(r["pin_hash"]), "pin_len": r["pin_len"], "minutes": r["minutes"], "minutes_choices": list(MINUTES),
            "locked": bool(s.get("locked")) and bool(r["pin_hash"]), "fails": r["fails"],
            "wait": max(0, int((wait - _now()).total_seconds())) if wait else 0,
            "fresh": bool(born and (_now() - born).total_seconds() < FRESH_SECONDS),
            "keys": [{"id": k["id"], "label": k["label"], "created_at": k["created_at"], "used_at": k["used_at"]}
                     for k in db.q("SELECT id, label, created_at, used_at FROM lock_keys WHERE user_id=? ORDER BY id", (user["id"],))],
            "key_ids": [k["cred_id"] for k in db.q("SELECT cred_id FROM lock_keys WHERE user_id=?", (user["id"],))],
            "name": user.get("full_name") or user.get("username") or "", "username": user.get("username") or ""}


def check(db, user, token, path):
    """Every signed-in call: None when it may go ahead, else the 423 answer. A session of someone without a PIN is
    never locked (there would be no way back in); the app asks them to set one."""
    if not token or FREE.match(path):
        return None
    s = _session(db, token)
    if not s:
        return None
    now = _now()
    if not s["born"]:                                  # the first call of this session (right after the password)
        db.x("UPDATE sessions SET born=?, seen=? WHERE token=?", (_iso(now), _iso(now), token))
        return None
    r = db.one("SELECT pin_hash, minutes FROM app_lock WHERE user_id=?", (user["id"],))
    if not r or not r["pin_hash"]:
        return None
    seen = _parse(s["seen"])
    if s["locked"] or (seen and (now - seen).total_seconds() > max(1, r["minutes"]) * 60):
        if not s["locked"]:
            db.x("UPDATE sessions SET locked=1 WHERE token=?", (token,))
        return {"error": "Locked — enter your PIN", "locked": True}
    if not seen or (now - seen).total_seconds() > 20:     # (not a write on every call)
        db.x("UPDATE sessions SET seen=? WHERE token=?", (_iso(now), token))
    return None


def lock(db, user, token):
    if db.one("SELECT 1 FROM app_lock WHERE user_id=? AND pin_hash<>''", (user["id"],)):
        db.x("UPDATE sessions SET locked=1 WHERE token=?", (token,))
    return state(db, user, token)


def _open(db, user, token):
    db.x("UPDATE app_lock SET fails=0, wait_until='' WHERE user_id=?", (user["id"],))
    db.x("UPDATE sessions SET locked=0, seen=? WHERE token=?", (_iso(_now()), token))


def _hash_pin(pin):
    """Like a password (PBKDF2-SHA256, salted; auth.check_password reads it) without the password length rule."""
    salt = secrets.token_hex(16)
    return f"pbkdf2_sha256${auth.ITERATIONS}${salt}${auth._pbkdf2(pin.encode(), bytes.fromhex(salt), auth.ITERATIONS).hex()}"


def _clean_pin(pin):
    pin = str(pin or "").strip()
    if not re.fullmatch(r"\d{4,6}", pin):
        raise ValidationError("A PIN is 4 to 6 digits")
    if len(set(pin)) == 1 or pin in "0123456789" or pin in "9876543210":
        raise ValidationError("Choose a PIN that is harder to guess (not the same digit or a straight run)")
    return pin


def set_pin(db, user, token, pin, current=None):
    pin = _clean_pin(pin)
    r = _row(db, user["id"])
    st = state(db, user, token)
    if r["pin_hash"] and (not st["fresh"] or st["locked"]):     # a locked screen never sets a new PIN without the old
        if not current or not auth.check_password(str(current), r["pin_hash"]):
            raise ValidationError("Enter your current PIN (or sign in again with your password to set a new one)")
    db.x("UPDATE app_lock SET pin_hash=?, pin_len=?, fails=0, wait_until='' WHERE user_id=?",
         (_hash_pin(pin), len(pin), user["id"]))
    _open(db, user, token)
    return state(db, user, token)


def set_minutes(db, user, token, minutes):
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        m = 0
    if m not in MINUTES:
        raise ValidationError("Choose how long the app may stay in the background")
    _row(db, user["id"])
    db.x("UPDATE app_lock SET minutes=? WHERE user_id=?", (m, user["id"]))
    return state(db, user, token)


def unlock_pin(db, user, token, pin):
    r = _row(db, user["id"])
    if not r["pin_hash"]:
        raise ValidationError("Set a PIN first")
    wait = _parse(r["wait_until"])
    if wait and wait > _now():
        raise ValidationError(f"Too many wrong PINs — wait {int((wait - _now()).total_seconds()) + 1} seconds")
    if auth.check_password(str(pin or ""), r["pin_hash"]):
        _open(db, user, token)
        return state(db, user, token)
    fails = r["fails"] + 1
    if fails >= MAX_FAILS:                              # ten wrong: this session ends; the password opens a new one
        db.x("UPDATE app_lock SET fails=0, wait_until='' WHERE user_id=?", (user["id"],))
        auth.logout(db, token)
        raise PermissionError("Too many wrong PINs — sign in again with your password")
    wait = _iso(_now() + _dt.timedelta(seconds=30 * 2 ** (fails - 5))) if fails >= 5 else ""
    db.x("UPDATE app_lock SET fails=?, wait_until=? WHERE user_id=?", (fails, wait, user["id"]))
    left = MAX_FAILS - fails
    raise ValidationError(f"Wrong PIN — {left} tries left" + (" (then you sign in with your password again)" if left <= 5 else ""))


# ---------------------------------------------------------------- fingerprint / face
def bio_begin(db, user, token):
    ch = webauthn.b64u(secrets.token_bytes(32))
    _row(db, user["id"])
    db.x("UPDATE app_lock SET challenge=?, challenge_at=? WHERE user_id=?", (ch, _iso(_now()), user["id"]))
    st = state(db, user, token)
    return {"challenge": ch, "user_id": webauthn.b64u(f"berp-{user['id']}".encode()), "name": st["username"],
            "display": st["name"], "key_ids": st["key_ids"]}


def _challenge(db, user):
    r = _row(db, user["id"])
    at = _parse(r["challenge_at"])
    if not r["challenge"] or not at or (_now() - at).total_seconds() > CHALLENGE_SECONDS:
        raise ValidationError("That took too long — try again")
    db.x("UPDATE app_lock SET challenge='' WHERE user_id=?", (user["id"],))      # used once
    return r["challenge"]


def bio_add(db, user, token, d, rp_id):
    st = state(db, user, token)
    if not st["pin_set"]:
        raise ValidationError("Set a PIN first")
    if st["locked"]:
        raise PermissionError("Unlock first")
    try:
        cred = webauthn.register(d.get("client"), d.get("attestation"), _challenge(db, user), rp_id)
    except webauthn.WebAuthnError as e:
        raise ValidationError(str(e)) from None
    label = str(d.get("label") or "This device")[:60]
    db.x("INSERT OR REPLACE INTO lock_keys(user_id, cred_id, key, count, label, created_at) VALUES (?,?,?,?,?,?)",
         (user["id"], cred["id"], json.dumps(cred["key"]), cred["count"], label, _iso(_now())))
    return state(db, user, token)


def bio_unlock(db, user, token, d, rp_id):
    k = db.one("SELECT * FROM lock_keys WHERE user_id=? AND cred_id=?", (user["id"], str(d.get("id") or "")))
    if not k:
        raise ValidationError("This fingerprint / face is not set up for you on this device — use your PIN")
    try:
        count = webauthn.verify(json.loads(k["key"]), k["count"], d.get("client"), d.get("auth"), d.get("sig"),
                                _challenge(db, user), rp_id)
    except webauthn.WebAuthnError as e:
        raise ValidationError(str(e)) from None
    db.x("UPDATE lock_keys SET count=?, used_at=? WHERE id=?", (count, _iso(_now()), k["id"]))
    _open(db, user, token)
    return state(db, user, token)


def bio_remove(db, user, token, kid):
    db.x("DELETE FROM lock_keys WHERE id=? AND user_id=?", (int(kid), user["id"]))
    return state(db, user, token)
