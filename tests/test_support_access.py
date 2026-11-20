"""Support access to a customer's books on a real PostgreSQL with every migration applied (tests/pg): only the
customer lets the team member on their ticket in, for a while, to view or to view and fix; the change log signs their
changes; no seat is taken; ended or expired access stops at once."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

OWNER = "owner@platform.test"
AGENT = "agent@platform.test"
OP = dict(p_version="1.3.0", p_app_user="", p_device="d", p_method="POST", p_path="/api/x", p_body="{}",
          p_content_type="application/json", p_clock="2026-11-01 10:00:00")


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class SupportAccess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.ws.sql(f"insert into public.platform_admins(email, name) values ('{OWNER}', 'Owner');")
        cls.owner = cls.ws.as_user(OWNER)
        cls.owner.rpc("console_staff_save", p={"email": AGENT, "name": "Sara Adel", "perms": {"support": True}})
        cls.agent = cls.ws.as_user(AGENT)

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def setup_company(self, email, name):
        cust = self.ws.as_user(email)
        cid = cust.rpc("create_company", p_name=name)["id"]
        t = cust.rpc("support_open", p_company=cid, p_kind="issue", p_subject="Wrong total", p_body="", p_diag={}, p_log=[],
                     p_files=[], p_app_user="")
        return cust, cid, t

    def seq(self, cid):
        return int(self.ws.sql(f"select coalesce(max(seq), 0) from ops where company_id = '{cid}'").strip().splitlines()[-1].strip()) + 1

    def test_the_customer_lets_support_in_to_fix_and_ends_it(self):
        cust, cid, t = self.setup_company("boss@x.test", "Boss Co")
        with self.assertRaisesRegex(pgtest.SQLError, "wait until a team member answers"):
            cust.rpc("support_access_grant", p_ticket=t["id"], p_hours=4, p_mode="fix")
        self.agent.rpc("support_reply", p_ticket=t["id"], p_body="Can you let me look?", p_files=[], p_internal=False)
        with self.assertRaises(pgtest.SQLError):                                         # the team cannot let itself in
            self.agent.rpc("support_access_grant", p_ticket=t["id"], p_hours=4, p_mode="fix")
        self.assertNotIn(cid, [c["id"] for c in self.agent.rpc("my_companies")])
        with self.assertRaisesRegex(pgtest.SQLError, "not a member"):
            self.agent.rpc("append_op", p_company=cid, p_seq=self.seq(cid), **OP)

        g = cust.rpc("support_access_grant", p_ticket=t["id"], p_hours=4, p_mode="fix")
        self.assertEqual((g["agent"], g["agent_name"], g["mode"], g["on"]), (AGENT, "Sara", "fix", True))
        mine = next(c for c in self.agent.rpc("my_companies") if c["id"] == cid)
        self.assertEqual((mine["support"], mine["is_admin"]), ("fix", False))
        info = self.agent.rpc("workspace_info", p_company=cid)
        self.assertEqual((info["is_member"], info["is_admin"], info["invite_code"]), (True, False, None))
        out = self.agent.rpc("append_op", p_company=cid, p_seq=self.seq(cid), **OP)
        self.assertEqual(out["app_user"], AGENT)                                           # signed with their own name
        actor = self.ws.sql(f"select actor from ops where company_id = '{cid}' order by seq desc limit 1").strip().splitlines()[-1].strip()
        self.assertEqual(actor, AGENT)
        with self.assertRaises(pgtest.SQLError):                                           # never an administrator
            self.agent.rpc("put_snapshot", p_company=cid, p_seq=0, p_version="1.3.0", p_data="x")
        self.assertEqual(cust.rpc("billing_status", p_company=cid)["users"], 1)             # no seat taken
        events = [m["body"] for m in cust.rpc("support_get", p_ticket=t["id"])["messages"] if m["kind"] == "event"]
        self.assertTrue(events[-1].startswith("access_fix:"))

        cust.rpc("support_access_end", p_id=g["id"])
        with self.assertRaisesRegex(pgtest.SQLError, "support access to this company has ended"):
            self.agent.rpc("append_op", p_company=cid, p_seq=self.seq(cid), **OP)
        self.assertNotIn(cid, [c["id"] for c in self.agent.rpc("my_companies")])
        self.assertFalse(cust.rpc("support_access_list", p_company=cid)[0]["on"])

    def test_view_only_and_expiry(self):
        cust, cid, t = self.setup_company("viewer@x.test", "Viewer Co")
        self.agent.rpc("support_reply", p_ticket=t["id"], p_body="Hello", p_files=[], p_internal=False)
        g = cust.rpc("support_access_grant", p_ticket=t["id"], p_hours=1, p_mode="view")
        self.assertTrue(self.agent.rpc("workspace_info", p_company=cid)["is_member"])
        with self.assertRaisesRegex(pgtest.SQLError, "view only"):
            self.agent.rpc("append_op", p_company=cid, p_seq=self.seq(cid), **OP)
        self.ws.sql(f"update support_access set expires_at = now() - interval '1 minute' where id = {g['id']};")
        self.assertFalse(self.agent.rpc("workspace_info", p_company=cid)["is_member"])
        with self.assertRaises(pgtest.SQLError):
            cust.rpc("support_access_grant", p_ticket=t["id"], p_hours=100, p_mode="fix")

    def test_the_support_user_takes_no_seat_and_only_admins_allow(self):
        cust, cid, t = self.setup_company("full@x.test", "Full Co")
        self.owner.rpc("billing_set_company", p_company=cid, p={"overrides": {"users": 1}})
        self.agent.rpc("support_reply", p_ticket=t["id"], p_body="Hello", p_files=[], p_internal=False)
        cust.rpc("set_member", p_company=cid, p_email="staff@x.test", p_is_admin=False, p_gateway=False, p_active=False)
        staff = self.ws.as_user("staff@x.test")
        with self.assertRaises(pgtest.SQLError):                                           # not an administrator
            staff.rpc("support_access_grant", p_ticket=t["id"], p_hours=2, p_mode="view")
        cust.rpc("support_access_grant", p_ticket=t["id"], p_hours=2, p_mode="fix")
        add = dict(OP, p_path="/api/users", p_body=json.dumps({"username": "support-sara", "email": AGENT}))
        cust.rpc("append_op", p_company=cid, p_seq=self.seq(cid), **add)                  # every seat taken, still allowed
        other = dict(OP, p_path="/api/users", p_body=json.dumps({"username": "someone", "email": "someone@x.test"}))
        with self.assertRaisesRegex(pgtest.SQLError, "seat_limit"):
            cust.rpc("append_op", p_company=cid, p_seq=self.seq(cid), **other)
        # a team member without the support permission is never let in
        self.owner.rpc("console_staff_save", p={"email": AGENT, "name": "Sara Adel", "perms": {"billing": True}})
        self.assertFalse(self.agent.rpc("workspace_info", p_company=cid)["is_member"])
        self.owner.rpc("console_staff_save", p={"email": AGENT, "name": "Sara Adel", "perms": {"support": True}})


if __name__ == "__main__":
    unittest.main()
