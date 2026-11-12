"""Security review findings (local app and workspace copies): each test is one finding."""
import base64
import gzip
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import accounting as acc, auth, cloud, server  # noqa: E402
from erp.db import Database  # noqa: E402


class App:
    pass


class Local(unittest.TestCase):
    def setUp(self):
        self.db = db = Database(":memory:")
        auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        auth.create_user(db, {"username": "dev", "password": "pw123456", "perms": {"settings.api": "edit"}})
        auth.create_user(db, {"username": "hr", "password": "pw123456", "perms": {"users.users": "add", "sales": "view"}})
        db.x("INSERT INTO business_units(code,name) VALUES ('P1','One'),('P2','Two')")
        auth.create_user(db, {"username": "site", "password": "pw123456", "perms": {"accounting.journal": "view"}, "projects": ["P1"]})
        self.app = App()
        self.app.db, self.app.backup = db, (lambda *a, **k: None)
        self.tok = {u: auth.login(db, u, "pw123456")[0] for u in ("boss", "dev", "hr", "site")}

    def call(self, m, p, b=None, who="boss"):
        r = server.handle(self.app, m, p, {"x-session": self.tok[who], "x-requested-with": "BeyondERP",
                                           "content-type": "application/json"}, json.dumps(b or {}).encode())
        return r.status, json.loads(r.body or b"{}")

    def test_api_key_only_for_oneself_unless_super(self):
        self.assertEqual(self.call("POST", "/api/api-keys", {"username": "boss", "read_only": False}, "dev")[0], 403)
        self.assertEqual(self.call("POST", "/api/api-keys", {"name": "mine"}, "dev")[0], 200)
        self.assertEqual(self.call("POST", "/api/api-keys", {"username": "dev", "name": "for dev"}, "boss")[0], 200)

    def test_a_new_user_never_gets_more_than_the_creator(self):
        st, r = self.call("POST", "/api/users", {"username": "puppet", "password": "pw123456",
                                                 "perms": {"accounting": "edit", "sales": "edit", "users.users": "edit"}}, "hr")
        self.assertEqual(st, 200, r)
        u = auth.public_user(self.db.one("SELECT * FROM users WHERE username='puppet'"))
        self.assertEqual((auth.level_of(u, "accounting"), auth.level_of(u, "sales"), auth.level_of(u, "users.users")), ("none", "view", "add"))

    def test_two_step_cannot_be_replaced_with_only_a_session(self):
        secret = auth.new_totp_secret()
        auth.totp_enable(self.db, self.db.one("SELECT id FROM users WHERE username='dev'")["id"], secret,
                         auth.totp(secret, auth._now_step()))
        other = auth.new_totp_secret()
        st, r = self.call("POST", "/api/me/two-step", {"secret": other, "code": auth.totp(other, auth._now_step())}, "dev")
        self.assertEqual(st, 400, r)

    def test_attachments_follow_the_project_limit(self):
        cash = self.db.acct("cash")
        e2 = acc.record_journal(self.db, {"date": "2026-10-01", "bu_code": "P2", "lines": [{"account": "5010", "debit": 10}, {"account": cash, "credit": 10}]})
        e1 = acc.record_journal(self.db, {"date": "2026-10-01", "bu_code": "P1", "lines": [{"account": "5010", "debit": 10}, {"account": cash, "credit": 10}]})
        self.assertEqual(self.call("GET", f"/api/attachments?owner=entry&id={e2}", who="site")[0], 403)
        self.assertEqual(self.call("GET", f"/api/attachments?owner=entry&id={e1}", who="site")[0], 200)

    def test_an_account_code_is_never_markup(self):
        with self.assertRaises(acc.ValidationError):
            acc.save_account(self.db, {"code": '9"><b id=x>', "name": "zz", "type": "Expense"})
        acc.save_account(self.db, {"code": "6010.01", "name": "Office rent — branch", "type": "Expense"})

    def test_lockout_is_its_own_error(self):
        self.assertTrue(issubclass(auth.LockedOut, acc.ValidationError))
        for _ in range(auth.MAX_FAILS):
            with self.assertRaises(acc.ValidationError):
                auth.login(self.db, "boss", "wrong-one")
        with self.assertRaises(auth.LockedOut):
            auth.login(self.db, "boss", "pw123456")
        auth._fails.clear()


class Exports(unittest.TestCase):
    """A whole-book file carries everything: only for someone who can see every section and every project."""

    @classmethod
    def setUpClass(cls):
        from erp import demo
        cls.db = db = Database(":memory:")
        demo.load(db, "admin")
        auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        auth.create_user(db, {"username": "exp", "password": "pw123456", "perms": {"import_export": "edit", "sales": "view"}})
        code = db.one("SELECT code FROM business_units ORDER BY id")["code"]
        auth.create_user(db, {"username": "site", "password": "pw123456", "projects": [code],
                              "perms": {m: "edit" for m in {r[2] for r in server.ROUTES if r[2]}}})
        cls.app = App()
        cls.app.db, cls.app.backup, cls.app.backups = db, (lambda *a, **k: None), (lambda: [])
        cls.tok = {u: auth.login(db, u, "pw123456")[0] for u in ("boss", "exp", "site")}

    def st(self, who, method, path, body=b""):
        return server.handle(self.app, method, path, {"x-session": self.tok[who], "x-requested-with": "BeyondERP"}, body).status

    def test_whole_books_only_with_full_access(self):
        for p in ("/api/export/workbook", "/api/export/json"):
            self.assertEqual(self.st("exp", "GET", p), 403, p)
            self.assertEqual(self.st("site", "GET", p), 403, p)        # every screen, but one project
            self.assertEqual(self.st("boss", "GET", p), 200, p)
        self.assertEqual(self.st("exp", "POST", "/api/import?format=json&dry_run=1", b'{"sheets": {}}'), 403)

    def test_one_sheet_needs_its_screen(self):
        self.assertEqual(self.st("exp", "GET", "/api/export/csv?sheet=Sales%20Invoices"), 200)
        for sheet in ("Payroll", "Journal%20Vouchers", "Partners"):
            self.assertEqual(self.st("exp", "GET", f"/api/export/csv?sheet={sheet}"), 403, sheet)
        self.assertEqual(self.st("exp", "GET", "/api/export/csv?sheet=Payroll&template=1"), 200)   # an empty template is fine
        self.assertEqual(self.st("site", "GET", "/api/export/csv?sheet=Sales%20Invoices"), 403)   # all projects' rows

    def test_importing_a_sheet_follows_its_screen(self):
        self.assertEqual(self.st("exp", "POST", "/api/import-preview/analyze?sheet=Payroll&filename=x.csv", b"a,b\n1,2\n"), 403)


class WorkspaceCopies(unittest.TestCase):
    def test_copies_carry_no_secrets_and_a_device_keeps_its_own(self):
        db = Database(":memory:")
        auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        db.set_setting("eta_client_secret", "S3CRET")
        uid = db.one("SELECT id FROM users WHERE username='boss'")["id"]
        auth.create_api_key(db, uid, "tool", True, "boss")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.db"
            p.write_bytes(gzip.decompress(base64.b64decode(cloud.snapshot_native(db))))
            c = sqlite3.connect(str(p))
            self.assertEqual(c.execute("SELECT pw_hash FROM users").fetchone()[0], "!")
            self.assertEqual(c.execute("SELECT value FROM settings WHERE key='eta_client_secret'").fetchone()[0], "")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0], 0)
            c.close()
        snap = cloud.snapshot_native(db)
        cloud.restore_native(db, snap)
        self.assertEqual(db.setting("eta_client_secret"), "S3CRET")              # this device's own
        self.assertNotEqual(db.one("SELECT pw_hash FROM users WHERE username='boss'")["pw_hash"], "!")
        self.assertEqual(db.one("SELECT COUNT(*) n FROM api_keys")["n"], 1)
        self.assertIsNotNone(auth.login(db, "boss", "pw123456"))

    def test_the_change_log_leaves_out_secrets(self):
        body, _ = cloud._strip_password("PUT", "/api/einvoice/settings", json.dumps({"eta_client_id": "id", "eta_client_secret": "S3CRET"}))
        self.assertEqual(json.loads(body), {"eta_client_id": "id"})
        for call in ("POST /api/me/two-step", "POST /api/api-keys"):
            self.assertTrue(cloud._LOCAL_RX.match(call), call)


if __name__ == "__main__":
    unittest.main()
