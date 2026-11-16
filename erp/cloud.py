"""Shared workspace on Supabase: many people, many devices, one set of books.

How it works
------------
Every device keeps the full engine and its own SQLite copy. What devices share is an ordered **log of changes**
(the `ops` table in Supabase): a change is the same API call the screen made (method, path, body), plus who made
it and when. To make a change a device

1. pulls the changes it has not seen yet and replays them,
2. runs its own change as a dry run (so a mistake never reaches the log),
3. appends it with the next sequence number — if another device got that number first, Supabase refuses
   (SQLSTATE 40001) and the device goes back to step 1 — and
4. applies it for real.

Replays use the change's own timestamp as "now" and run as the app user who made it, so every device computes the
same result and re-checks that user's permissions. A new device starts from the latest snapshot (a full copy saved
in Supabase) and replays only what came after it.

This module holds the transport-free core (used by both editions) and the Supabase client for the desktop/server
edition (the browser edition talks to Supabase from JavaScript, see web-src/bridge.js).
"""
import base64
import datetime as _dt
import gzip
import json
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

from . import __version__
from . import db as _db

# Calls that only concern this device (sessions, previews, maintenance) or talk to outside systems — replaying them
# on every device would be wrong (e.g. submitting an e-invoice to the tax authority once per device).
LOCAL_ONLY = [r"POST /api/login", r"POST /api/logout", r"POST /api/setup", r"PUT /api/me/password",
              r"POST /api/me/two-step(?:/off)?", r"POST /api/api-keys", r"DELETE /api/api-keys/\d+",   # secrets of this device
              r"POST /api/maintenance", r"POST /api/offers-preview", r"POST /api/einvoice/test",
              r"POST /api/einvoice/submit", r"POST /api/ereceipts-submit", r"POST /api/einvoice/\d+/refresh", r"POST /api/einvoice/\d+/cancel",
              r"POST /api/[\w-]+-preview(?:/[\w-]+)?", r"\w+ /api/cloud/.*",
              r"\w+ /api/ai/.*"]   # the assistant's key, requests and conversations stay on the device
_LOCAL_RX = re.compile("^(?:" + "|".join(LOCAL_ONLY) + ")$")
ETA_CALLS = re.compile(r"^POST /api/einvoice/(?:submit|(\d+)/(?:refresh|cancel))$")
SNAPSHOT_EVERY = 300          # changes between automatic workspace copies
# People created in Users without an email sign in through an internal address <username>@PLACEHOLDER_DOMAIN
# (supabase/functions/manage-user); it is never emailed. The ".invalid" top-level domain is reserved for this.
PLACEHOLDER_DOMAIN = "users.beyond-erp.invalid"


def username_for(email):
    """The app username behind a workspace account address, when it is an internal placeholder."""
    e = (email or "").strip().lower()
    return e.rsplit("@", 1)[0] if e.endswith("@" + PLACEHOLDER_DOMAIN) else None
PULL_PAGE = 500
PART = 250_000                # characters per piece: a large change or copy goes up in pieces, each its own short call
PARTS_RX = re.compile(r"^parts:([a-z0-9]{16,40}):([1-9][0-9]{0,3})$")
# a change sent without an answer (the connection dropped): if the log turns out to hold it, the same request made
# again within this many seconds gets that change's result instead of a second copy of the change
UNSURE_FOR = 3600


def _op_key(op):
    return (op.get("device"), op.get("method"), op.get("path"), op.get("body"))


class Conflict(Exception):
    """Someone else appended first."""


class Offline(Exception):
    pass


class NeedsUpdate(Exception):
    """The log contains changes made by a newer app version."""


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "0"))[:3])


# Changes that rewrite much of the books: right after one, the device that made it saves a fresh copy to the
# workspace, so phones load that copy instead of replaying the whole change (see web-src/bridge.js catchUp)
_HEAVY_RX = re.compile(r"^/api/(import(\?|$)|import-sheet|demo|erase|setup)")


def is_heavy(method, path):
    return method == "POST" and bool(_HEAVY_RX.match(path or ""))


def replicated(method, target):
    """Is this API call a change every device must replay?"""
    if method not in ("POST", "PUT", "DELETE"):
        return False
    url = urlparse(target)
    if not url.path.startswith("/api/") or _LOCAL_RX.match(f"{method} {url.path}"):
        return False
    if url.path == "/api/import" and "dry_run=1" in (url.query or ""):
        return False
    return True


# ------------------------------------------------------------------ device-local settings (never replicated)
def local_get(db, key, default=None):
    r = db.one("SELECT value FROM cloud_local WHERE key=?", (key,))
    return r["value"] if r else default


def local_set(db, key, value):
    if value is None:
        db.x("DELETE FROM cloud_local WHERE key=?", (key,))
    else:
        db.x("INSERT OR REPLACE INTO cloud_local(key,value) VALUES (?,?)", (key, str(value)))


def device_id(db):
    d = local_get(db, "device")
    if not d:
        d = uuid.uuid4().hex[:12]
        local_set(db, "device", d)
    return d


def last_seq(db):
    return int(local_get(db, "last_seq", "-1") or -1)


def joined(db):
    return last_seq(db) >= 0 and bool(local_get(db, "url"))


# ------------------------------------------------------------------ change encoding
def encode_body(raw, ctype):
    if not raw:
        return ""
    if "json" in (ctype or ""):
        return raw.decode("utf-8")
    return "b64:" + base64.b64encode(raw).decode()


def decode_body(text):
    if not text:
        return b""
    if text.startswith("b64:"):
        return base64.b64decode(text[4:])
    return text.encode("utf-8")


def _strip_password(method, path, body_text):
    """Passwords never go to the workspace: people sign in there with their own account. Returns (logged body,
    password to set on this device only). The e-invoice keys stay on the device they are typed on too."""
    if urlparse(path).path == "/api/einvoice/settings" and body_text:
        try:
            body = json.loads(body_text)
        except ValueError:
            return body_text, None
        for k in _db.ETA_SECRETS:
            body.pop(k, None)
        return json.dumps(body, ensure_ascii=False), None
    if not re.match(r"^/api/users(/\d+)?$", urlparse(path).path) or not body_text:
        return body_text, None
    try:
        body = json.loads(body_text)
    except ValueError:
        return body_text, None
    pw = body.pop("password", None) or None
    if method == "POST":
        body["_no_password"] = True
    return json.dumps(body, ensure_ascii=False), pw


def _parse_clock(s):
    s = str(s).replace("Z", "+00:00")
    t = _dt.datetime.fromisoformat(s)
    if t.tzinfo:  # store and replay in the device's local wall-clock of the author: keep it as written
        t = t.replace(tzinfo=None)
    return t.replace(microsecond=0)


# ------------------------------------------------------------------ core: dry run, apply
def prepare(app, method, target, headers, raw, user):
    """Dry-run a change as `user`; returns (reply, op or None). The database is left untouched."""
    from .server import handle, ok, route_module
    if headers.get("x-ai-call") == "1":   # the assistant's change: same policy, check and review as without the workspace
        from . import ai
        path = urlparse(target).path
        try:
            ai.guard(app.db, user, method, path, raw, headers, route_module(method, path))
        except PermissionError as e:
            return ok({"error": str(e) or "Not allowed"}, 403), None
    conn = app.db.conn
    url = urlparse(target)
    packing = method == "POST" and url.path == "/api/import"
    app.pack_import, app.packed_import = packing, None
    with app.db.lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            reply = handle(app, method, target, headers, raw, as_user=user["username"], dry=True)
        finally:
            conn.execute("ROLLBACK")
            app.pack_import = False
    packed, app.packed_import = app.packed_import, None
    if reply.status >= 400:
        return reply, None
    if b'"_nochange": true' in (reply.body or b"")[:4000]:   # a check that found nothing to do: nothing to share
        return reply, None
    ctype = headers.get("content-type") or ""
    if packing and packed is not None:
        # an import: the log keeps what was read (compact, gzip), not the file; every device replays that
        q = [(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True) if k not in ("format", "sheet")]
        target = url.path + "?" + urlencode(q + [("format", "parsed")])
        raw, ctype = packed, "application/x-beyond-import"
    body, pw = _strip_password(method, target, encode_body(raw, ctype))
    op = {"version": __version__, "app_user": user["username"], "device": device_id(app.db), "method": method,
          "path": target, "body": body, "content_type": ctype,
          "clock": _db.now().isoformat(timespec="seconds"), "_password": pw}
    return reply, op


def apply(app, op):
    """Replay one change from the log (own or someone else's). Returns the reply."""
    from .server import handle
    if version_tuple(op.get("version")) > version_tuple(__version__):
        raise NeedsUpdate(op.get("version"))
    headers = {"x-requested-with": "BeyondERP", "content-type": op.get("content_type") or "application/json"}
    _db.CLOCK["at"] = _parse_clock(op["clock"])
    try:
        reply = handle(app, op["method"], op["path"], headers, decode_body(op.get("body")), as_user=op["app_user"])
    finally:
        _db.CLOCK["at"] = None
    local_set(app.db, "last_seq", int(op["seq"]))
    pw = op.get("_password")
    if pw and reply.status < 400:  # this device's own change: set the password locally only
        _set_local_password(app.db, op, reply, pw)
    return reply


def _set_local_password(db, op, reply, pw):
    from . import auth
    m = re.match(r"^/api/users/(\d+)$", urlparse(op["path"]).path)
    uid = int(m.group(1)) if m else (json.loads(reply.body or b"{}").get("id"))
    if uid:
        db.x("UPDATE users SET pw_hash=? WHERE id=?", (auth.hash_password(pw), uid))


def followups(db, method, target, body):
    """After a device talks to the tax authority, share the resulting e-invoice / e-receipt status with everyone."""
    if f"{method} {urlparse(target).path}" == "POST /api/ereceipts-submit":
        ids = [int(i) for i in (body or {}).get("ids") or []]
        if not ids:          # "send all": the receipts of the latest submission
            last = db.one("SELECT submission_id FROM ereceipts WHERE submission_id<>'' ORDER BY id DESC LIMIT 1")
            ids = [r["id"] for r in db.q("SELECT id FROM ereceipts WHERE submission_id=?", (last["submission_id"],))] if last else []
        return [("PUT", f"/api/ereceipts/{r['id']}/eta-state", json.dumps({k: r[k] for k in ("status", "submission_id", "error")},
                                                                          ensure_ascii=False).encode())
                for r in (db.one("SELECT id, status, submission_id, error FROM ereceipts WHERE id=?", (i,)) for i in ids) if r]
    m = ETA_CALLS.match(f"{method} {urlparse(target).path}")
    if not m:
        return []
    ids = [int(m.group(1))] if m.group(1) else [int(i) for i in (body or {}).get("ids") or []]
    out = []
    for xid in ids:
        x = db.one("SELECT eta_uuid, eta_status, eta_submission, eta_error FROM extracts WHERE id=?", (xid,))
        if x:
            out.append(("PUT", f"/api/extracts/{xid}/eta-state", json.dumps(x, ensure_ascii=False).encode()))
    return out


def ensure_app_user(db, email, admin=False, super_=False, link_uid=None):
    """A workspace account signs in as the app user whose username is its email.

    link_uid: the person signed in on this device as it connects (a desktop set up on its own first). The workspace
    account becomes theirs (their email set) instead of a second user, so what they do is recorded under them, with
    their own rights. super_: the workspace makes the account the company's super administrator (it created it)."""
    from . import auth
    email = email.strip().lower()
    u = db.one("SELECT * FROM users WHERE lower(email)=? OR lower(username)=?", (email, email))
    if not u and link_uid:
        me = db.one("SELECT * FROM users WHERE id=? AND active=1", (link_uid,))
        if me and not (me.get("email") or "").strip():
            db.x("UPDATE users SET email=?, is_admin=MAX(is_admin, ?), is_super=MAX(is_super, ?) WHERE id=?",
                 (email, 1 if admin or super_ else 0, 1 if super_ else 0, me["id"]))
            return
    if u and super_ and not u["is_super"]:
        db.x("UPDATE users SET is_admin=1, is_super=1, active=1 WHERE id=?", (u["id"],))
        return
    if not u:
        name = email.split("@")[0]
        username = name if not db.one("SELECT 1 FROM users WHERE lower(username)=?", (name.lower(),)) else email
        # the company's first administrator owns the books (super administrator); later ones are administrators
        first = super_ or (admin and not db.one("SELECT 1 FROM users WHERE is_super=1 AND active=1"))
        auth.create_user(db, {"username": username, "email": email, "full_name": name, "is_admin": admin,
                              "_no_password": True, "perms": {"dashboard": "view"}}, first=first)
    elif admin and not u["is_admin"]:
        db.x("UPDATE users SET is_admin=1, active=1 WHERE id=?", (u["id"],))


# ------------------------------------------------------------------ snapshots (server edition; the browser does
# the same in JavaScript on its sql.js image)
LOCAL_TABLES = ("cloud_local", "sessions", "ai_threads", "ai_usage")   # left out of workspace copies (device-only)


# what a workspace copy never carries (any member can read the copies): sign-in secrets, tool keys, e-invoice keys
SCRUB = ("UPDATE users SET pw_hash='!', totp_secret=''", "DELETE FROM api_keys",
         "UPDATE settings SET value='' WHERE key IN (" + ",".join(f"'{k}'" for k in _db.ETA_SECRETS) + ")")


def device_secrets(db):
    """This device's own secrets, put back after a workspace copy is restored (matched by username, as ids change)."""
    return {"_user_secrets": db.q("SELECT username, pw_hash, totp_secret FROM users WHERE pw_hash<>'!' OR COALESCE(totp_secret,'')<>''"),
            "_eta_secrets": db.q("SELECT key, value FROM settings WHERE key IN (" + ",".join("?" * len(_db.ETA_SECRETS)) + ") AND value<>''",
                                 _db.ETA_SECRETS),
            "_api_keys": db.q("SELECT k.*, u.username FROM api_keys k JOIN users u ON u.id=k.user_id")}


def _keep_on_restore(db):
    # sessions are dropped: user ids change with the restored books, so everyone signs in again
    return {"cloud_local": db.q("SELECT * FROM cloud_local"), "sessions": [], **device_secrets(db)}


def snapshot_native(db):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "snap.sqlite3"
        db.backup_to(path)
        c = _db.sqlite3.connect(str(path))
        for t in LOCAL_TABLES:
            c.execute(f"DELETE FROM {t}")
        for q in SCRUB:
            try:
                c.execute(q)
            except _db.sqlite3.OperationalError:      # older books without that table / column
                pass
        c.commit()
        c.execute("VACUUM")
        c.close()
        return base64.b64encode(gzip.compress(path.read_bytes(), 6)).decode()


def restore_native(db, data_b64):
    keep = _keep_on_restore(db)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "restore.sqlite3"
        path.write_bytes(gzip.decompress(base64.b64decode(data_b64)))
        src = _db.sqlite3.connect(str(path))
        with db.lock:
            src.backup(db.conn)
        src.close()
    db.bump()
    restore_local_rows(db, keep)


def restore_local_rows(db, keep):
    db.bump()                                    # the books were replaced underneath: forget kept results
    with db.lock:
        db._init()  # schema upgrades if the copy came from an older version
        keep = dict(keep)
        for r in keep.pop("_user_secrets", None) or []:
            db.conn.execute("UPDATE users SET pw_hash=?, totp_secret=? WHERE username=?", (r["pw_hash"], r["totp_secret"] or "", r["username"]))
        for r in keep.pop("_eta_secrets", None) or []:
            db.conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (r["key"], r["value"]))
        for r in keep.pop("_api_keys", None) or []:
            u = db.conn.execute("SELECT id FROM users WHERE username=?", (r["username"],)).fetchone()
            if u:
                db.conn.execute("INSERT OR IGNORE INTO api_keys(user_id,name,key_hash,prefix,read_only,created_by,created_at) VALUES (?,?,?,?,?,?,?)",
                                (u[0], r["name"], r["key_hash"], r["prefix"], r["read_only"], r["created_by"], r["created_at"]))
        for t, rows in keep.items():
            db.conn.execute(f"DELETE FROM {t}")
            for r in rows:
                cols = list(r)
                db.conn.execute(f"INSERT OR REPLACE INTO {t}({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                [r[c] for c in cols])


# ------------------------------------------------------------------ Supabase REST client (stdlib)
AUTH_HELP = {
    "invalid_credentials": "Wrong email or password. No account yet? Use “Create account”. Just created one? Open the "
                           "confirmation email Supabase sent first.",
    "email_not_confirmed": "Please confirm your email first: open the link Supabase sent you (check spam), then sign in.",
    "over_email_send_rate_limit": "Supabase's free email service only sends a few emails per hour — try again later, or "
                                  "create the account in the Supabase dashboard (Authentication → Users → Add user, "
                                  "tick “Auto Confirm User”).",
    "user_already_exists": "An account with this email already exists — sign in instead.",
    "weak_password": "Choose a longer password (at least 6 characters).",
}


def auth_help(code, msg):
    if code in AUTH_HELP:
        return AUTH_HELP[code]
    low = msg.lower()
    for needle, key in (("invalid login credentials", "invalid_credentials"), ("email not confirmed", "email_not_confirmed"),
                        ("rate limit", "over_email_send_rate_limit"), ("already registered", "user_already_exists")):
        if needle in low:
            return AUTH_HELP[key]
    return msg


def fresh_books(company_name=""):
    """Empty books for a new company (default chart of accounts, settings…), named after the company."""
    from .db import Database
    db = Database(":memory:")
    if company_name:
        db.set_setting("company_name", company_name)
    return db


class Supabase:
    def __init__(self, url, key, timeout=25):
        self.url, self.key, self.timeout = url.rstrip("/"), key, timeout

    def _call(self, method, path, body=None, token=None, headers=None):
        h = {"apikey": self.key, "Content-Type": "application/json", "Authorization": f"Bearer {token or self.key}"}
        h.update(headers or {})
        req = urllib.request.Request(self.url + path, method=method, headers=h,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = r.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                err = json.loads(raw)
            except ValueError:
                err = {"message": raw.decode("utf-8", "replace")[:300]}
            msg = err.get("message") or err.get("msg") or err.get("error_description") or err.get("error") or str(e)
            if err.get("code") == "40001":
                raise Conflict(msg) from None
            msg = auth_help(err.get("error_code") or "", msg)
            ex = PermissionError(msg) if e.code in (401, 403) or err.get("code") == "42501" else RuntimeError(msg)
            ex.status = e.code
            raise ex from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise Offline(f"Cannot reach the workspace ({getattr(e, 'reason', e)})") from None

    def sign_in(self, email, password):
        return self._call("POST", "/auth/v1/token?grant_type=password", {"email": email, "password": password})

    def sign_up(self, email, password):
        return self._call("POST", "/auth/v1/signup", {"email": email, "password": password})

    def sign_in_username(self, username, password):
        try:
            return self._call("POST", "/functions/v1/login", {"username": username, "password": password})
        except RuntimeError as e:
            if getattr(e, "status", 0) == 404:
                raise PermissionError("Username sign-in is not configured for this workspace. Use the email address, or ask the administrator to deploy the login function (see the setup guide).") from None
            raise

    def refresh(self, refresh_token):
        return self._call("POST", "/auth/v1/token?grant_type=refresh_token", {"refresh_token": refresh_token})

    def rpc(self, name, params, token):
        return self._call("POST", f"/rest/v1/rpc/{name}", params, token)

    def select(self, table, query, token):
        return self._call("GET", f"/rest/v1/{table}?{query}", token=token)


class CloudSync:
    """The desktop/server edition's link to the workspace. One workspace account (ideally marked "gateway") relays
    the changes of this server's local users."""

    def __init__(self, app):
        self.app, self.db = app, app.db
        self.lock = threading.RLock()
        self.status = {"state": "off", "error": "", "last_sync": ""}
        self._token, self._token_exp = None, 0
        self._stop = threading.Event()
        self._thread = None
        self._gateway = None       # this device's account relays its local users (learnt from the workspace)

    # -- configuration
    @property
    def active(self):
        return joined(self.db)

    def client(self):
        url, key = local_get(self.db, "url"), local_get(self.db, "key")
        if not url or not key:
            raise PermissionError("Connect this device to a workspace first (Settings → Workspace).")
        return Supabase(url, key)

    def configure(self, url, key):
        url = (url or "").strip().rstrip("/")
        if not re.match(r"^https?://", url) or not (key or "").strip():
            raise ValueError("Enter the project URL (https://….supabase.co) and the anon public key")
        local_set(self.db, "url", url)
        local_set(self.db, "key", key.strip())

    def _save_session(self, s):
        if not s or not s.get("access_token"):
            raise PermissionError("Sign-in failed. If a confirmation link was sent, confirm your email first.")
        self._token, self._token_exp = s["access_token"], time.time() + int(s.get("expires_in") or 3600) - 60
        local_set(self.db, "refresh", s.get("refresh_token"))
        local_set(self.db, "email", ((s.get("user") or {}).get("email") or local_get(self.db, "email") or "").lower())

    def sign_in(self, email, password, create=False):
        c = self.client()
        email = (email or "").strip().lower()
        if create:
            r = c.sign_up(email, password) or {}
            if not r.get("access_token"):   # "Confirm email" is on (Supabase's default)
                if isinstance(r.get("identities"), list) and not r["identities"]:
                    raise PermissionError(AUTH_HELP["user_already_exists"])
                raise PermissionError(f"Almost done: we sent a confirmation email to {email}. Open the link in it, then "
                                      "come back here and choose Sign in.")
            self._save_session(r)
            return self.info()
        self._save_session(c.sign_in(email, password) if "@" in email else c.sign_in_username(email, password))
        return self.info()

    def token(self):
        if self._token and time.time() < self._token_exp:
            return self._token
        rt = local_get(self.db, "refresh")
        if not rt:
            raise PermissionError("Sign in to the workspace again (Settings → Workspace)")
        self._save_session(self.client().refresh(rt))
        return self._token

    # -- companies: one workspace holds several companies, each with its own shared books and members. This device
    # follows one of them (local "company"); switching means disconnecting and joining the other one.
    @property
    def company(self):
        return local_get(self.db, "company") or ""

    def _co(self):
        if not self.company:
            raise PermissionError("Choose a company first (Settings → Workspace).")
        return self.company

    def rpc(self, name, params=None):
        return self.client().rpc(name, params or {}, self.token())

    def companies(self):
        return self.rpc("my_companies") or []

    def choose_company(self, cid):
        cid = (cid or "").strip()
        if self.active and cid != self.company:
            raise RuntimeError("This device is connected to another company. Disconnect it first.")
        if not any(c["id"] == cid and c.get("active") and not c.get("pending") for c in self.companies()):
            raise PermissionError("You are not a member of that company.")
        local_set(self.db, "company", cid)
        return self.info()

    def create_company(self, name):
        co = self.rpc("create_company", {"p_name": name})
        if not self.active:
            local_set(self.db, "company", co["id"])
        return co

    def join_company(self, code):
        return self.rpc("join_company", {"p_code": code})

    def info(self):
        """This device's company as the signed-in person sees it, plus the list of their companies."""
        cos = self.companies()
        if not self.company:
            live = [c for c in cos if c.get("active") and not c.get("pending")]
            if len(live) == 1:
                local_set(self.db, "company", live[0]["id"])
        if not self.company:
            return {"email": local_get(self.db, "email") or "", "is_member": False, "has_members": True, "companies": cos,
                    "needs_company": True}
        return {**(self.rpc("workspace_info", {"p_company": self.company}) or {}), "companies": cos,
                "company": self.company}

    def members(self):
        return self.rpc("company_members", {"p_company": self._co()}) or []

    def set_member(self, email, is_admin=False, gateway=False, active=True):
        return self.rpc("set_member", {"p_company": self._co(), "p_email": email, "p_is_admin": bool(is_admin),
                                       "p_gateway": bool(gateway), "p_active": active is not False})

    def remove_member(self, email):
        return self.rpc("remove_member", {"p_company": self._co(), "p_email": email})

    def link_member(self, email, username="", active=True):
        return self.rpc("link_member", {"p_company": self._co(), "p_email": email, "p_username": username or "",
                                        "p_active": active is not False})

    def manage_user(self, body):
        return self.client()._call("POST", "/functions/v1/manage-user", {**body, "company": self._co()}, self.token())

    def rename_company(self, name):
        return self.rpc("rename_company", {"p_company": self._co(), "p_name": name})

    def delete_company(self, cid, name):
        """Delete a company for good (its super administrators, typing its name). Not the one this device follows."""
        cid = (cid or "").strip()
        if cid and cid == self.company:
            raise RuntimeError("This device is connected to this company. Disconnect it before deleting.")
        return self.rpc("delete_company", {"p_company": cid, "p_name": name})

    def new_invite_code(self):
        return self.rpc("new_invite_code", {"p_company": self._co()})

    # -- joining
    def claim(self, name=None, local_uid=None):
        """Start a new company from this device's books (you become its administrator — and its super administrator,
        as the workspace counts the company's creator). local_uid: who is signed in here, to link the account to."""
        with self.lock:
            if self.active:
                raise RuntimeError("This device is already connected to a company. Disconnect it first.")
            c, tok = self.client(), self.token()
            name = (name or self.db.setting("company_name") or "My company").strip()
            co = c.rpc("create_company", {"p_name": name}, tok)
            local_set(self.db, "company", co["id"])
            ensure_app_user(self.db, local_get(self.db, "email") or "", admin=True, super_=True, link_uid=local_uid)
            c.rpc("put_snapshot", {"p_company": co["id"], "p_seq": 0, "p_version": __version__,
                                   "p_data": self.put_big(snapshot_native(self.db), "copy")}, self.token())
            local_set(self.db, "last_seq", 0)
            local_set(self.db, "snap_seq", 0)
            self.start()
            return self.info()

    def join(self, company=None):
        """Replace this device's books with the company's shared books (a backup is taken first)."""
        with self.lock:
            if company:
                self.choose_company(company)
            c, tok = self.client(), self.token()
            info = self.info()
            if info.get("needs_company"):
                raise PermissionError("Choose which company to open")
            if not info.get("is_member"):
                raise PermissionError("This account is not yet a member of this company. Contact its administrator.")
            rows = c.select("snapshots", f"select=seq,version,data&company_id=eq.{self.company}&order=seq.desc&limit=1", tok)
            if not rows:
                if not info.get("is_admin"):
                    raise RuntimeError("This company's books are not set up. Its administrator must open them first.")
                # a brand-new company: its administrator starts its books here (empty, with the company's name)
                if hasattr(self.app, "backup"):
                    self.app.backup("before-new-company")
                restore_native(self.db, snapshot_native(fresh_books(info.get("company_name") or "")))
                ensure_app_user(self.db, info.get("email") or "", admin=True, super_=self._my_super())
                c.rpc("put_snapshot", {"p_company": self.company, "p_seq": 0, "p_version": __version__,
                                       "p_data": self.put_big(snapshot_native(self.db), "copy")}, self.token())
                local_set(self.db, "last_seq", 0)
                local_set(self.db, "snap_seq", 0)
                self.start()
                return self.info()
            snap = rows[0]
            if version_tuple(snap["version"]) > version_tuple(__version__):
                raise NeedsUpdate(snap["version"])
            if hasattr(self.app, "backup"):
                self.app.backup("before-join")
            restore_native(self.db, self.get_big(snap["data"], "copy"))
            local_set(self.db, "last_seq", int(snap["seq"]))
            local_set(self.db, "snap_seq", int(snap["seq"]))
            self.pull()
            self.start()
            return info

    def disconnect(self):
        self.stop()
        for k in ("last_seq", "refresh", "email", "snap_seq", "company"):
            local_set(self.db, k, None)
        self._token = None
        self.status = {"state": "off", "error": "", "last_sync": ""}

    # -- syncing
    def pull(self):
        """Replay everything new. Returns how many changes were applied."""
        with self.lock:
            c, n = self.client(), 0
            while True:
                rows = c.select("ops", f"select=*&company_id=eq.{self._co()}&seq=gt.{last_seq(self.db)}"
                                       f"&order=seq.asc&limit={PULL_PAGE}", self.token())
                for op in rows:
                    if int(op["seq"]) != last_seq(self.db) + 1:
                        raise RuntimeError("The workspace log is incomplete. Try again shortly.")
                    op["body"] = self.get_big(op.get("body"))
                    r = apply(self.app, op)
                    u = getattr(self, "_unsure", None)
                    if u and u["op"] == _op_key(op):
                        u["reply"] = r            # the change whose answer was lost did reach the log
                    n += 1
                if len(rows) < PULL_PAGE:
                    break
            self.status.update(state="ok", error="", last_sync=_dt.datetime.now().isoformat(timespec="seconds"))
            return n

    def _my_super(self):
        try:
            return bool(self.rpc("my_super", {"p_company": self._co()}))
        except Exception:      # an older workspace, or offline: nothing to add
            return False

    def recorded_as(self, user):
        """Who a change from `user` on this device is recorded as in the workspace log, and so replayed as on every
        device: the user themselves when this device's account is a gateway (it relays local users), otherwise the
        workspace account's own app user. A change is checked as both: the person's own rights, and what replays."""
        if self._gateway is None:
            try:
                self._gateway = bool((self.info() or {}).get("gateway"))
            except Exception:
                return user
        if self._gateway:
            return user
        from . import auth
        email = (local_get(self.db, "email") or "").strip().lower()
        row = self.db.one("SELECT * FROM users WHERE active=1 AND (username=? OR (email<>'' AND lower(email)=?)) "
                          "ORDER BY username=? DESC LIMIT 1", (username_for(email) or email, email, username_for(email) or email)) if email else None
        return auth.public_user(row) if row else user

    def sync_role(self):
        """The workspace makes this account the company's super administrator (it created the company, or a super
        administrator marked it) but this device's books do not: say so through the log, so every device agrees. The
        workspace stores that change only from an account it counts as a super administrator."""
        if not self.active or not self._my_super():
            return False
        u = self.recorded_as(None)
        if not u or u.get("is_super"):
            return False
        r = self.write("POST", "/api/users/workspace-role", {"x-requested-with": "BeyondERP", "content-type": "application/json"}, b"{}", u)
        return r.status < 400

    def write(self, method, target, headers, raw, user):
        """Make a change through the workspace; returns the reply the screen expects."""
        from .server import ok
        rec = self.recorded_as(user) or user     # who the log will replay it as (a gateway: the person themselves)
        with self.lock:
            try:
                ask, sent = (method, target, bytes(raw or b"")), [None, None]
                for _ in range(8):
                    self.pull()
                    u = getattr(self, "_unsure", None)
                    if u and u.get("reply") is not None and u["ask"] == ask and time.time() - u["at"] < UNSURE_FOR:
                        self._unsure = None       # the same request again: it was saved the first time
                        return u["reply"]
                    reply, op = prepare(self.app, method, target, headers, raw, user)    # the person's own rights
                    if op is None:
                        return reply
                    if rec and rec.get("id") != user.get("id"):
                        # and as the account it will be replayed as, on every device: what fails there is not stored
                        r2, _ = prepare(self.app, method, target, headers, raw, rec)
                        if r2.status >= 400:
                            return r2
                    op["seq"] = last_seq(self.db) + 1
                    if sent[0] != op["body"]:          # a large change goes up in pieces, once (not again on a retry)
                        sent[:] = [op["body"], self.put_big(op["body"])]
                    self._unsure = {"ask": ask, "op": _op_key(op), "at": time.time(), "reply": None}
                    try:
                        stored = self.client().rpc("append_op", {
                            "p_company": self._co(), "p_seq": op["seq"], "p_version": op["version"], "p_app_user": op["app_user"],
                            "p_device": op["device"], "p_method": op["method"], "p_path": op["path"],
                            "p_body": sent[1], "p_content_type": op["content_type"], "p_clock": op["clock"]},
                            self.token())
                    except Conflict:
                        self._unsure = None       # refused: nothing was stored
                        continue
                    self._unsure = None
                    op["app_user"] = (stored or {}).get("app_user") or op["app_user"]  # exactly as the log has it
                    reply = apply(self.app, op)
                    self._maybe_snapshot(force=is_heavy(op["method"], op["path"]))
                    return reply
                return ok({"error": "The workspace is busy. Try again."}, 409)
            except Offline as e:
                self.status.update(state="offline", error=str(e))
                return ok({"error": f"{e}. Changes need a connection to the shared workspace."}, 503)
            except NeedsUpdate as e:
                return ok({"error": f"A newer version ({e}) has written to the workspace. Update this application first."}, 409)
            except PermissionError as e:
                self.status.update(state="signed_out", error=str(e))
                return ok({"error": f"Workspace: {e}"}, 403)

    # -- large texts (a big import, a copy of the books) in pieces: a single call is stopped after a few seconds
    def _progress(self, what, done, total):
        self.status["progress"] = {"what": what, "done": done, "total": total, "at": time.time(),
                                   "started": (self.status.get("progress") or {}).get("started") if done else time.time()}

    def _retry(self, fn):
        for attempt in range(5):
            try:
                return fn()
            except (Offline, RuntimeError):
                if attempt == 4:
                    raise
                time.sleep(min(8, 0.5 * 2 ** attempt))

    def put_big(self, text, what="upload"):
        """text → itself when small, else "parts:<id>:<count>" once every piece is in the workspace."""
        if not text or len(text) <= PART:
            return text
        pid, co, n = uuid.uuid4().hex[:24], self._co(), -(-len(text) // PART)
        self._progress(what, 0, len(text))
        try:
            for i in range(n):
                piece = text[i * PART:(i + 1) * PART]
                self._retry(lambda: self.client().rpc("put_part", {"p_company": co, "p_id": pid, "p_idx": i, "p_data": piece},
                                                      self.token()))
                self._progress(what, min(len(text), (i + 1) * PART), len(text))
        finally:
            self.status.pop("progress", None)
        return f"parts:{pid}:{n}"

    def get_big(self, text, what="download"):
        """The other way: "parts:<id>:<count>" → the whole text, fetched piece by piece."""
        m = PARTS_RX.match(text or "")
        if not m:
            return text
        pid, n, co, out = m.group(1), int(m.group(2)), self._co(), []
        self._progress(what, 0, n * PART)
        try:
            for i in range(n):
                rows = self._retry(lambda: self.client().select(
                    "op_parts", f"select=data&company_id=eq.{co}&part_id=eq.{pid}&idx=eq.{i}", self.token()))
                if not rows:
                    raise RuntimeError("A piece of a shared change is missing from the workspace")
                out.append(rows[0]["data"])
                self._progress(what, (i + 1) * PART, n * PART)
        finally:
            self.status.pop("progress", None)
        return "".join(out)

    def _maybe_snapshot(self, force=False):
        seq, snap = last_seq(self.db), int(local_get(self.db, "snap_seq", "0") or 0)
        if seq - snap < (1 if force else SNAPSHOT_EVERY):
            return
        if not force and time.time() < float(local_get(self.db, "snap_retry_at", "0") or 0):
            return                                   # the last copy failed: wait before trying again
        try:
            data = self.put_big(snapshot_native(self.db), "copy")
            self.client().rpc("put_snapshot", {"p_company": self._co(), "p_seq": seq, "p_version": __version__,
                                               "p_data": data}, self.token())
            local_set(self.db, "snap_seq", seq)
            local_set(self.db, "snap_wait", "0")
            local_set(self.db, "snap_retry_at", "0")
        except PermissionError:
            local_set(self.db, "snap_seq", seq)      # not allowed for this member: the next batch, not a loop
        except (RuntimeError, Offline):
            # failed (offline, too large, a server error): the copy is still owed — retry after 5 min, doubling up to 1 h
            wait = min(3600, max(300, 2 * float(local_get(self.db, "snap_wait", "0") or 0)))
            local_set(self.db, "snap_wait", str(wait))
            local_set(self.db, "snap_retry_at", str(time.time() + wait))

    def touch(self):
        """Someone is using the app (called on every API request) — keep syncing at the quick pace."""
        self._used = time.time()

    def start(self, every=20, idle_every=300, idle_after=600):
        """Check for other people's changes every 20 s while the app is in use, every 5 min when nobody has used it for
        10 min. Every check is a request Supabase logs, so fewer checks keep its log usage down. A change made here
        always fetches what is new first, so nothing is missed."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._used = getattr(self, "_used", time.time())

        def loop():
            last = 0.0
            try:
                self.sync_role()
            except Exception:          # offline, or an older workspace: tried again next start
                pass
            while not self._stop.wait(every):
                if not self.active:
                    continue
                idle = time.time() - self._used > idle_after
                if idle and time.time() - last < idle_every:
                    continue
                last = time.time()
                try:
                    self.pull()
                except Offline as e:
                    self.status.update(state="offline", error=str(e))
                except NeedsUpdate as e:
                    self.status.update(state="update", error=f"Update this app to version {e} or newer")
                except Exception as e:  # noqa: BLE001 — keep the loop alive, show the problem in Settings
                    self.status.update(state="error", error=str(e)[:300])
        self._thread = threading.Thread(target=loop, name="workspace-sync", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def public_status(self):
        return {"configured": bool(local_get(self.db, "url")), "url": local_get(self.db, "url") or "",
                "key": local_get(self.db, "key") or "", "email": local_get(self.db, "email") or "",
                "joined": self.active, "last_seq": last_seq(self.db), "device": device_id(self.db),
                "company": self.company, **self.status}
