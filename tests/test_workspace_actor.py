"""A desktop set up on its own first, then connected to the shared workspace: what its people do is recorded under the
workspace account, and every device replays it as that account's app user. So:

* connecting links the account to the person signed in (no second user), and they keep their rights;
* a change is checked as the person it will be replayed as, so nothing that fails everywhere is stored;
* a device whose books do not show its account as the super administrator the workspace counts it as (connected by
  an older version) says so through the log, every device agrees, and an ordinary member cannot claim it.
"""
import json
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mock_supabase  # noqa: E402
from erp import auth, cloud  # noqa: E402
from erp.cloud import Supabase  # noqa: E402
from erp.db import Database  # noqa: E402
from erp.server import ERPServer  # noqa: E402


class Node:
    def __init__(self, tmp, name):
        self.db = Database(Path(tmp) / f"{name}.sqlite3")
        self.srv = ERPServer(("127.0.0.1", 0), self.db, Path(tmp) / f"bk-{name}")
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.cookie = None

    def call(self, m, p, b=None):
        rq = urllib.request.Request(self.base + p, method=m, data=json.dumps(b or {}).encode(), headers={
            "X-Requested-With": "BeyondERP", "content-type": "application/json", **({"cookie": self.cookie} if self.cookie else {})})
        try:
            r = urllib.request.urlopen(rq)
        except urllib.error.HTTPError as e:
            r = e
        c = r.headers.get("set-cookie")
        if c and "beyond_session" in c:
            self.cookie = c.split(";")[0]
        return r.status, json.loads(r.read() or b"{}")


class WorkspaceActor(unittest.TestCase):
    def setUp(self):
        self.sb = mock_supabase.start()
        self.url = f"http://127.0.0.1:{self.sb.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.sb.shutdown()

    def desktop(self, name="a"):
        n = Node(self.tmp.name, name)
        self.assertEqual(n.call("POST", "/api/setup", {"username": "admin", "password": "secret123"})[0], 200)
        n.call("PUT", "/api/cloud/config", {"url": self.url, "key": "anon"})
        return n

    def test_connecting_links_the_account_to_the_person_signed_in(self):
        a = self.desktop()
        self.assertEqual(a.call("POST", "/api/cloud/signin", {"email": "owner@x.com", "password": "pw123456", "create": True})[0], 200)
        self.assertEqual(a.call("POST", "/api/cloud/claim", {"name": "Beta"})[0], 200)
        users = {u["username"]: u for u in a.db.q("SELECT username, email, is_super FROM users")}
        self.assertEqual(list(users), ["admin"])                                     # no second user
        self.assertEqual((users["admin"]["email"], users["admin"]["is_super"]), ("owner@x.com", 1))
        st, r = a.call("POST", "/api/users", {"username": "clerk", "password": "clerkpw123"})
        self.assertEqual(st, 200, r)
        uid = r["id"]
        st, r = a.call("PUT", f"/api/users/{uid}", {"full_name": "The clerk"})       # a super administrator's edit
        self.assertEqual(st, 200, r)
        self.assertEqual(a.db.one("SELECT full_name FROM users WHERE id=?", (uid,))["full_name"], "The clerk")
        cid = self.sb.state.company_named("Beta")
        self.assertEqual(self.sb.state.ops[cid][-1]["app_user"], "owner@x.com")      # recorded under the account

    def test_a_device_connected_before_is_repaired_through_the_log(self):
        a = self.desktop()
        a.call("POST", "/api/cloud/signin", {"email": "owner@x.com", "password": "pw123456", "create": True})
        a.call("POST", "/api/cloud/claim", {"name": "Beta"})
        uid = a.call("POST", "/api/users", {"username": "clerk", "password": "clerkpw123"})[1]["id"]
        # as an older version left it: the workspace account a second user, an administrator but not super
        a.db.x("UPDATE users SET email='' WHERE username='admin'")
        a.db.x("INSERT INTO users(username, full_name, pw_hash, is_admin, is_super, perms, email) "
               "VALUES ('owner', 'owner', '!', 1, 0, '{}', 'owner@x.com')")
        cid = self.sb.state.company_named("Beta")
        # checked as the person it is recorded as: refused, and nothing that would fail everywhere is stored
        before = len(self.sb.state.ops[cid])
        st, r = a.call("PUT", f"/api/users/{uid}", {"full_name": "x"})
        self.assertEqual(st, 403, r)
        self.assertEqual(len(self.sb.state.ops[cid]), before)
        self.assertTrue(a.srv.cloud.sync_role())                                      # the workspace counts it as super
        self.assertEqual(self.sb.state.ops[cid][-1]["path"], "/api/users/workspace-role")
        self.assertEqual(a.db.one("SELECT is_super FROM users WHERE username='owner'")["is_super"], 1)
        st, r = a.call("PUT", f"/api/users/{uid}", {"full_name": "The clerk"})
        self.assertEqual(st, 200, r)
        self.assertFalse(a.srv.cloud.sync_role())                                     # once is enough

    def test_an_ordinary_member_cannot_claim_the_role(self):
        owner = Supabase(self.url, "anon")
        otok = owner.sign_up("boss@y.com", "pw123456")["access_token"]
        cid = owner.rpc("create_company", {"p_name": "Gamma"}, otok)["id"]
        clerk = Supabase(self.url, "anon")
        ctok = clerk.sign_up("clerk@y.com", "pw123456")["access_token"]
        code = self.sb.state.companies[cid]["invite_code"]
        clerk.rpc("join_company", {"p_code": code}, ctok)
        self.sb.state.members[(cid, "clerk@y.com")]["pending"] = False
        op = {"p_company": cid, "p_seq": 1, "p_version": "1.3.0", "p_app_user": "", "p_device": "x", "p_method": "POST",
              "p_path": "/api/users/workspace-role", "p_body": "{}", "p_content_type": "application/json", "p_clock": "2026-01-01T00:00:00"}
        with self.assertRaises((PermissionError, RuntimeError)):
            clerk.rpc("append_op", op, ctok)
        hostile = {**op, "p_version": "999.0.0", "p_path": "/api/contacts"}
        with self.assertRaises((PermissionError, RuntimeError)):
            clerk.rpc("append_op", hostile, ctok)
        self.assertEqual(self.sb.state.ops[cid], [])
        self.assertEqual(owner.rpc("append_op", op, otok)["seq"], 1)                  # the creator may
        self.assertFalse(clerk.rpc("my_super", {"p_company": cid}, ctok))
        self.assertTrue(owner.rpc("my_super", {"p_company": cid}, otok))


class LocalOnly(unittest.TestCase):
    def test_failed_replay_does_not_advance_the_sync_cursor(self):
        tmp = tempfile.TemporaryDirectory()
        n = Node(tmp.name, "replay")
        n.call("POST", "/api/setup", {"username": "admin", "password": "secret123"})
        cloud.local_set(n.db, "last_seq", 0)
        op = {"seq": 1, "version": "1.3.0", "clock": "2026-11-19T10:00:00", "method": "POST",
              "path": "/api/contacts", "body": "{}", "content_type": "application/json", "app_user": "admin"}
        failure = SimpleNamespace(status=403, body=b'{"error":"not allowed"}')
        with patch("erp.server.handle", return_value=failure):
            reply = cloud.apply(n.srv, op)
        self.assertEqual(reply.status, 403)
        self.assertEqual(cloud.last_seq(n.db), 0)
        self.assertEqual(cloud.local_get(n.db, "sync_failed_seq"), "1")
        self.assertIn("HTTP 403", cloud.local_get(n.db, "sync_failed_reason"))

    def test_the_role_change_is_refused_without_the_workspace(self):
        tmp = tempfile.TemporaryDirectory()
        n = Node(tmp.name, "solo")
        n.call("POST", "/api/setup", {"username": "admin", "password": "secret123"})
        auth.create_user(n.db, {"username": "clerk", "password": "clerkpw123"})
        n.cookie = None
        st, r = n.call("POST", "/api/login", {"username": "clerk", "password": "clerkpw123"})
        self.assertEqual(st, 200, r)
        st, r = n.call("POST", "/api/users/workspace-role", {})
        self.assertEqual(st, 403, r)
        self.assertEqual(n.db.one("SELECT is_super FROM users WHERE username='clerk'")["is_super"], 0)
        self.assertIsNone(cloud.username_for("clerk@example.com"))


if __name__ == "__main__":
    unittest.main()
