"""The app lock (erp/applock.py, erp/webauthn.py): a PIN after the password, fingerprint / face where the device has it.

* someone without a PIN is never locked (there would be no way back); with one, a locked session is refused (423)
  except the lock calls, and opens again with the PIN;
* the lock time: nothing reaching the app for that long locks it; wrong PINs count down, wait, then end the session;
* a PIN is 4–6 digits, not trivial; changing it needs the old one — unless the password was just typed, and never
  from a locked screen; the browser edition's reopened session starts locked;
* fingerprint / face: a P-256 credential registered and a sign-in verified (signature, challenge used once, site,
  person verified, counter); a forged signature, another site or an old challenge are refused;
* all of it stays on the device: not written to the workspace log, not in workspace copies.
"""
import base64
import hashlib
import json
import secrets
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from erp import accounting as acc, applock, auth, cloud, webauthn as wa  # noqa: E402
from erp.db import Database  # noqa: E402


def session(db, name="ali"):
    if not db.one("SELECT 1 FROM users WHERE username=?", (name,)):
        auth.create_user(db, {"username": name, "password": "pw123456"}, first=not db.one("SELECT 1 FROM users"))
    tok, _ = auth.login(db, name, "pw123456")
    u = auth.user_for_token(db, tok)
    applock.check(db, u, tok, "/api/boot")             # the first call of the session
    return u, tok


# ---- a platform authenticator, in a few lines (P-256, the way a phone signs)
def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def cbor_enc(v):
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | info]) + n.to_bytes(size, "big")
    if isinstance(v, bool):
        return bytes([0xf5 if v else 0xf4])
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, bytes):
        return head(2, len(v)) + v
    if isinstance(v, str):
        return head(3, len(v.encode())) + v.encode()
    if isinstance(v, dict):
        return head(5, len(v)) + b"".join(cbor_enc(k) + cbor_enc(x) for k, x in v.items())
    raise TypeError(v)


class Authenticator:
    def __init__(self, rp="app.example"):
        self.rp, self.d = rp, secrets.randbelow(wa.N - 1) + 1
        self.x, self.y = wa._mul(self.d, wa.G)
        self.cred, self.count = secrets.token_bytes(16), 0

    def _ad(self, attested=False):
        self.count += 1
        flags = 0x01 | 0x04 | (0x40 if attested else 0)
        ad = hashlib.sha256(self.rp.encode()).digest() + bytes([flags]) + self.count.to_bytes(4, "big")
        if attested:
            cose = cbor_enc({1: 2, 3: -7, -1: 1, -2: self.x.to_bytes(32, "big"), -3: self.y.to_bytes(32, "big")})
            ad += b"\0" * 16 + len(self.cred).to_bytes(2, "big") + self.cred + cose
        return ad

    def _client(self, kind, challenge, origin=None):
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or f"https://{self.rp}"}).encode()

    def create(self, challenge):
        return {"client": b64u(self._client("webauthn.create", challenge)),
                "attestation": b64u(cbor_enc({"fmt": "none", "attStmt": {}, "authData": self._ad(True)}))}

    def sign(self, challenge, origin=None, tamper=False):
        cj, ad = self._client("webauthn.get", challenge, origin), self._ad()
        z = int.from_bytes(hashlib.sha256(ad + hashlib.sha256(cj).digest()).digest(), "big")
        k = secrets.randbelow(wa.N - 1) + 1
        r = wa._mul(k, wa.G)[0] % wa.N
        s = pow(k, -1, wa.N) * (z + r * self.d) % wa.N
        if tamper:
            s = (s + 1) % wa.N
        der = lambda n: (lambda b: b"\x02" + bytes([len(b)]) + b)(n.to_bytes(33, "big").lstrip(b"\0").rjust(1, b"\0") if n.bit_length() % 8 else b"\0" + n.to_bytes(n.bit_length() // 8, "big"))  # noqa: E731
        body = der(r) + der(s)
        return {"id": b64u(self.cred), "client": b64u(cj), "auth": b64u(ad), "sig": b64u(b"\x30" + bytes([len(body)]) + body)}


class AppLock(unittest.TestCase):
    def test_no_pin_no_lock(self):
        db = Database(":memory:")
        u, tok = session(db)
        applock.lock(db, u, tok)
        self.assertIsNone(applock.check(db, u, tok, "/api/journal"))
        self.assertFalse(applock.state(db, u, tok)["pin_set"])

    def test_locked_until_the_pin(self):
        db = Database(":memory:")
        u, tok = session(db)
        applock.set_pin(db, u, tok, "2580")
        self.assertIsNone(applock.check(db, u, tok, "/api/journal"))
        applock.lock(db, u, tok)
        self.assertTrue(applock.check(db, u, tok, "/api/journal")["locked"])
        self.assertIsNone(applock.check(db, u, tok, "/api/lock"))                 # the lock calls still answer
        self.assertIsNone(applock.check(db, u, tok, "/api/logout"))
        with self.assertRaisesRegex(acc.ValidationError, "Wrong PIN — 9 tries left"):
            applock.unlock_pin(db, u, tok, "1357")
        applock.unlock_pin(db, u, tok, "2580")
        self.assertIsNone(applock.check(db, u, tok, "/api/journal"))
        self.assertEqual(applock.state(db, u, tok)["fails"], 0)

    def test_lock_time_and_wrong_pins(self):
        db = Database(":memory:")
        u, tok = session(db)
        applock.set_pin(db, u, tok, "2580")
        applock.set_minutes(db, u, tok, 1)
        db.x("UPDATE sessions SET seen=datetime('now','-2 minutes') WHERE token=?", (tok,))
        self.assertTrue(applock.check(db, u, tok, "/api/boot")["locked"])           # idle past the lock time
        with self.assertRaises(acc.ValidationError):
            applock.set_minutes(db, u, tok, 7)
        for i in range(5):
            with self.assertRaises(acc.ValidationError):
                applock.unlock_pin(db, u, tok, "1357")
        with self.assertRaisesRegex(acc.ValidationError, "wait"):                  # five wrong: a wait, even for the right one
            applock.unlock_pin(db, u, tok, "2580")
        db.x("UPDATE app_lock SET wait_until='' WHERE user_id=?", (u["id"],))
        for i in range(4):
            with self.assertRaises(acc.ValidationError):
                applock.unlock_pin(db, u, tok, "1357")
                db.x("UPDATE app_lock SET wait_until='' WHERE user_id=?", (u["id"],))
            db.x("UPDATE app_lock SET wait_until='' WHERE user_id=?", (u["id"],))
        with self.assertRaisesRegex(PermissionError, "sign in again"):
            applock.unlock_pin(db, u, tok, "1357")                                  # the tenth: the session ends
        self.assertIsNone(auth.user_for_token(db, tok))

    def test_pin_rules_and_changing_it(self):
        db = Database(":memory:")
        u, tok = session(db)
        for bad in ("12", "1234567", "abcd", "0000", "1234", "9876"):
            with self.subTest(pin=bad), self.assertRaises(acc.ValidationError):
                applock.set_pin(db, u, tok, bad)
        applock.set_pin(db, u, tok, "582914")
        self.assertEqual(applock.state(db, u, tok)["pin_len"], 6)
        db.x("UPDATE sessions SET born='2000-01-01 00:00:00' WHERE token=?", (tok,))   # not just signed in
        with self.assertRaisesRegex(acc.ValidationError, "current PIN"):
            applock.set_pin(db, u, tok, "2580")
        applock.set_pin(db, u, tok, "2580", current="582914")
        u2, tok2 = session(db)                                                     # the password just typed: "forgot my PIN"
        applock.set_pin(db, u2, tok2, "3691")
        applock.lock(db, u2, tok2)
        with self.assertRaisesRegex(acc.ValidationError, "current PIN"):           # never from a locked screen
            applock.set_pin(db, u2, tok2, "4826")

    def test_fingerprint_face(self):
        db = Database(":memory:")
        u, tok = session(db)
        applock.set_pin(db, u, tok, "2580")
        dev = Authenticator()
        ch = applock.bio_begin(db, u, tok)["challenge"]
        st = applock.bio_add(db, u, tok, {**dev.create(ch), "label": "iPhone"}, "app.example")
        self.assertEqual([k["label"] for k in st["keys"]], ["iPhone"])
        applock.lock(db, u, tok)
        ch = applock.bio_begin(db, u, tok)["challenge"]
        self.assertFalse(applock.bio_unlock(db, u, tok, dev.sign(ch), "app.example")["locked"])
        self.assertIsNone(applock.check(db, u, tok, "/api/journal"))
        applock.lock(db, u, tok)
        for label, make, rp in (("forged", lambda c: dev.sign(c, tamper=True), "app.example"),
                                ("other site", lambda c: dev.sign(c, origin="https://evil.example"), "app.example"),
                                ("wrong rp", lambda c: dev.sign(c), "evil.example")):
            with self.subTest(label):
                ch = applock.bio_begin(db, u, tok)["challenge"]
                with self.assertRaises(acc.ValidationError):
                    applock.bio_unlock(db, u, tok, make(ch), rp)
        ch = applock.bio_begin(db, u, tok)["challenge"]
        sig = dev.sign(ch)
        applock.bio_unlock(db, u, tok, sig, "app.example")
        applock.lock(db, u, tok)
        with self.assertRaisesRegex(acc.ValidationError, "too long"):              # a challenge is used once
            applock.bio_unlock(db, u, tok, sig, "app.example")

    def test_routes_and_device_only(self):
        self.assertTrue(cloud._LOCAL_RX.match("POST /api/lock/pin"))
        self.assertTrue(cloud._LOCAL_RX.match("POST /api/lock/bio/add"))
        self.assertIn("app_lock", cloud.LOCAL_TABLES)
        self.assertIn("lock_keys", cloud.LOCAL_TABLES)


class AppLockHTTP(unittest.TestCase):
    def test_server_refuses_locked_calls(self):
        import threading
        import tempfile
        import urllib.request
        from erp.server import ERPServer
        d = tempfile.TemporaryDirectory()
        db = Database(Path(d.name) / "a.sqlite3")
        srv = ERPServer(("127.0.0.1", 0), db, Path(d.name) / "bk")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        jar = {}

        def call(m, p, b=None):
            rq = urllib.request.Request(base + p, method=m, data=json.dumps(b or {}).encode() if m != "GET" else None, headers={
                "X-Requested-With": "BeyondERP", "content-type": "application/json", **({"cookie": jar["c"]} if "c" in jar else {})})
            try:
                r = urllib.request.urlopen(rq)
            except urllib.error.HTTPError as e:
                r = e
            c = r.headers.get("set-cookie")
            if c:
                jar["c"] = c.split(";")[0]
            return r.status, json.loads(r.read() or b"{}")
        try:
            self.assertEqual(call("POST", "/api/setup", {"username": "admin", "password": "secret123"})[0], 200)
            self.assertEqual(call("GET", "/api/bootstrap")[0], 200)
            self.assertEqual(call("POST", "/api/lock/pin", {"pin": "2580"})[0], 200)
            self.assertEqual(call("POST", "/api/lock/lock")[0], 200)
            st, r = call("GET", "/api/journal")
            self.assertEqual((st, r.get("locked")), (423, True))
            st, r = call("GET", "/api/bootstrap")                                    # nothing of the books while locked
            self.assertEqual((st, r.get("locked"), "accounts" in r), (423, True, False))
            self.assertEqual(call("GET", "/api/lock")[1]["locked"], True)
            self.assertEqual(call("POST", "/api/lock/unlock", {"pin": "1357"})[0], 400)
            self.assertEqual(call("POST", "/api/lock/unlock", {"pin": "2580"})[0], 200)
            self.assertEqual(call("GET", "/api/journal")[0], 200)
            self.assertTrue(call("GET", "/api/bootstrap")[1]["lock"]["pin_set"])
        finally:
            srv.shutdown()
            srv.server_close()
            db.close()
            d.cleanup()


if __name__ == "__main__":
    unittest.main()
