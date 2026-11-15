"""App error reports, a company's devices and asking for access, on a real PostgreSQL with every migration (tests/pg):
the app reports its crashes (masked, counted once per error), the team sees them grouped across companies and marks them
fixed — seen again from that version, they open again; a company's page lists its devices and versions; the team member
answering a ticket asks the customer to let them in."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

OWNER = "owner@platform.test"
LEAD = "lead@platform.test"
SALES = "sales@platform.test"
OP = dict(p_app_user="boss", p_method="POST", p_path="/api/journal", p_content_type="application/json", p_clock="2026-11-14 10:00:00")


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class AppErrors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.ws.sql(f"insert into public.platform_admins(email, name) values ('{OWNER}', 'Owner');")
        cls.owner = cls.ws.as_user(OWNER)
        cls.owner.rpc("console_staff_save", p={"email": LEAD, "name": "Lead", "perms": {"support": True}})
        cls.owner.rpc("console_staff_save", p={"email": SALES, "name": "Sales", "perms": {"customers": True}})
        cls.lead, cls.sales = cls.ws.as_user(LEAD), cls.ws.as_user(SALES)
        cls.a, cls.b = cls.ws.as_user("boss@one.test"), cls.ws.as_user("boss@two.test")
        cls.one = cls.a.rpc("create_company", p_name="One")["id"]
        cls.two = cls.b.rpc("create_company", p_name="Two")["id"]
        cls.stranger = cls.ws.as_user("nobody@else.test")

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def report(self, who, cid, **kw):
        item = {"kind": "js", "message": "TypeError: x is undefined", "place": "#/ledger?party=Acme", "version": "2.4.0", "n": 1, **kw}
        return who.rpc("app_errors_report", p_company=cid, p_items=[item])

    def mine(self, msg):
        return next(e for e in self.lead.rpc("console_app_errors", p_status="") if e["message"] == msg)

    def test_the_same_error_from_two_companies_is_one_row_masked_and_counted(self):
        self.report(self.a, self.one, message="Cannot read 'total' of 1234567 for ali@acme.com", n=3)
        self.report(self.b, self.two, message="Cannot read 'total' of 9999999 for omar@x.org", version="2.5.0")
        e = self.mine("Cannot read 'total' of # for …@…")
        self.assertEqual((e["total"], e["companies"], e["last_version"], e["first_version"]), (4, 2, "2.5.0", "2.4.0"))
        self.assertEqual(e["place"], "#/ledger")                              # the query (a contact's name) is left out
        d = self.lead.rpc("console_app_error", p_id=e["id"])
        self.assertEqual(sorted(w["company"] for w in d["where"]), ["One", "Two"])

    def test_fixed_in_a_version_opens_again_only_when_seen_from_it_or_later(self):
        msg = "RangeError: bad date"
        self.report(self.a, self.one, message=msg, version="2.4.0")
        e = self.mine(msg)
        self.lead.rpc("console_app_error_set", p_id=e["id"], p={"status": "fixed", "fixed_in": "2.10.0", "note": "date parser"})
        self.report(self.a, self.one, message=msg, version="2.9.3")            # an old copy of the app: still fixed
        self.assertEqual(self.mine(msg)["status"], "fixed")
        self.report(self.b, self.two, message=msg, version="2.10.0")           # the fixed version: open again
        e = self.mine(msg)
        self.assertEqual(e["status"], "open")
        self.assertIn("Seen again in 2.10.0", e["note"])

    def test_only_members_report_and_only_the_desk_reads(self):
        with self.assertRaises(pgtest.SQLError):
            self.report(self.stranger, self.one)
        with self.assertRaises(pgtest.SQLError):
            self.report(self.a, self.two)                                     # not their company
        with self.assertRaises(pgtest.SQLError):
            self.sales.rpc("console_app_errors", p_status="")                 # customers right, not bug reports
        with self.assertRaises(pgtest.SQLError):
            self.a.rpc("console_app_errors", p_status="")
        self.assertEqual(self.a.rpc("app_errors_report", p_company=self.one, p_items=[{"message": ""}] * 30)["saved"], 0)

    def test_a_companys_devices_versions_and_errors(self):
        for i, (dev, ver) in enumerate((("laptop", "2.3.0"), ("phone", "2.5.0"), ("phone", "2.5.1")), 1):
            self.a.rpc("append_op", p_company=self.one, p_seq=i, p_version=ver, p_device=dev, p_body="{}", **OP)
        self.report(self.a, self.one, message="Device test error")
        h = self.sales.rpc("console_company_health", p_company=self.one)       # the customers right reads it
        devs = {d["device"]: d for d in h["devices"]}
        self.assertEqual((devs["phone"]["version"], devs["phone"]["changes"], devs["laptop"]["version"]), ("2.5.1", 2, "2.3.0"))
        self.assertIn("Device test error", [e["message"] for e in h["errors"]])
        self.assertTrue(h["latest"])
        with self.assertRaises(pgtest.SQLError):
            self.a.rpc("console_company_health", p_company=self.one)

    def test_the_team_member_asks_for_access_and_the_customer_sees_it(self):
        t = self.a.rpc("support_open", p_company=self.one, p_kind="issue", p_subject="Wrong totals", p_body="Details", p_diag={},
                       p_log=[], p_files=[], p_app_user="")
        got = self.lead.rpc("support_access_ask", p_ticket=t["id"], p_mode="fix", p_note="To correct the March entries")
        self.assertEqual(got["assignee"], LEAD)
        seen = [m["body"] for m in self.a.rpc("support_get", p_ticket=t["id"])["messages"]]
        self.assertIn("access_asked_fix:To correct the March entries", seen)
        self.a.rpc("support_access_grant", p_ticket=t["id"], p_hours=2, p_mode="fix")   # then the administrator allows it
        with self.assertRaises(pgtest.SQLError):
            self.sales.rpc("support_access_ask", p_ticket=t["id"], p_mode="view", p_note="")
        with self.assertRaises(pgtest.SQLError):
            self.lead.rpc("support_access_ask", p_ticket=t["id"], p_mode="all", p_note="")

    def test_a_spike_and_a_new_error_reach_the_teams_notifications(self):
        c3 = self.ws.as_user("boss@three.test"); three = c3.rpc("create_company", p_name="Three")["id"]
        for who, cid in ((self.a, self.one), (self.b, self.two), (c3, three)):
            self.report(who, cid, message="Spike: totals fail")
        self.report(self.a, self.one, message="Only here once")
        kinds = {(a["kind"], a["title"]) for a in self.lead.rpc("console_alerts")}
        self.assertIn(("error_spike", "Spike: totals fail"), kinds)
        self.assertIn(("new_error", "Only here once"), kinds)
        self.assertNotIn("error_spike", {a["kind"] for a in self.sales.rpc("console_alerts")})     # not on the desk
        key = next(a["key"] for a in self.lead.rpc("console_alerts") if a["kind"] == "error_spike")
        self.lead.rpc("console_alert_mark", p_keys=[key], p_state="dismissed")
        self.assertNotIn(key, [a["key"] for a in self.lead.rpc("console_alerts")])
        self.assertIn("new_company", {a["kind"] for a in self.owner.rpc("console_alerts")})           # the others still there

    def test_the_last_day_at_a_glance(self):
        self.report(self.a, self.one, message="Digest error")
        d = self.owner.rpc("console_digest")
        for k in ("errors_new", "tickets_unread", "orders_waiting", "companies_new", "trials_ending", "active_companies"):
            self.assertIn(k, d)
        self.assertGreaterEqual(d["errors_new"], 1)
        s = self.sales.rpc("console_digest")
        self.assertIn("companies_new", s)
        self.assertNotIn("errors_new", s)
        self.assertNotIn("orders_waiting", s)
        with self.assertRaises(pgtest.SQLError):
            self.a.rpc("console_digest")

    def test_a_companys_recent_changes_without_figures(self):
        for i in range(1, 4):
            self.b.rpc("append_op", p_company=self.two, p_seq=i, p_version="2.5.0", p_device="pc", p_body='{"amount": 5000}',
                       **{**OP, "p_path": f"/api/journal/{i}?x=secret"})
        ch = self.lead.rpc("console_company_changes", p_company=self.two, p_limit=2)
        self.assertEqual([c["seq"] for c in ch], [3, 2])
        self.assertEqual(ch[0]["path"], "/api/journal/3")
        self.assertNotIn("5000", str(ch))
        self.assertEqual(len(self.sales.rpc("console_company_changes", p_company=self.two, p_limit=50)), 3)
        with self.assertRaises(pgtest.SQLError):
            self.b.rpc("console_company_changes", p_company=self.two, p_limit=5)


if __name__ == "__main__":
    unittest.main()
