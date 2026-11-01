"""What each support team member may do, access that ends by itself, and announcement emails, on a real PostgreSQL
with every migration applied (tests/pg)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

OWNER = "owner@platform.test"
LEAD = "lead@platform.test"          # added before detailed permissions: may do everything on the desk
SHADOW = "shadow@platform.test"      # reads tickets and writes internal notes only
SALES = "sales@platform.test"        # customers only: no announcements, no desk
ALL = ["reply", "notes", "manage", "close", "books", "contacts", "replies", "help", "bugs", "visits"]


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class DeskPermissions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.ws.sql(f"insert into public.platform_admins(email, name) values ('{OWNER}', 'Owner');")
        cls.owner = cls.ws.as_user(OWNER)
        cls.owner.rpc("console_staff_save", p={"email": LEAD, "name": "Lead", "perms": {"support": True, "notices": True}})
        cls.owner.rpc("console_staff_save", p={"email": SHADOW, "name": "New Hire", "perms": {"support": True, "desk": {"notes": True}}})
        cls.owner.rpc("console_staff_save", p={"email": SALES, "name": "Sales", "perms": {"customers": True}})
        cls.lead, cls.shadow, cls.sales = cls.ws.as_user(LEAD), cls.ws.as_user(SHADOW), cls.ws.as_user(SALES)
        cls.owner.rpc("billing_set_config", p={"enabled": True, "trial_full": False, "support_mode": "free"})
        cls.cust = cls.ws.as_user("boss@client.test")
        cls.cid = cls.cust.rpc("create_company", p_name="Client Co")["id"]

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def ticket(self, subject):
        return self.cust.rpc("support_open", p_company=self.cid, p_kind="issue", p_subject=subject, p_body="Details", p_diag={"browser": "x"},
                             p_log=[], p_files=[], p_app_user="")

    def test_what_each_member_may_do(self):
        me = self.shadow.rpc("console_me")
        self.assertEqual(me["desk"], {k: k == "notes" for k in ALL})
        self.assertEqual(self.lead.rpc("console_me")["desk"], {k: True for k in ALL})          # earlier members keep everything
        self.assertEqual(self.sales.rpc("console_me")["desk"], {k: False for k in ALL})        # no support area, nothing
        t = self.ticket("Shadow test")
        got = self.shadow.rpc("support_get", p_ticket=t["id"])                                # a shadow reads the ticket…
        self.assertEqual(got["email"], "b•••@c•••")                                            # …without the customer's address
        self.assertEqual(got["diag"], {})
        self.assertTrue(all("@client" not in m["email"] for m in got["messages"]))
        row = next(r for r in self.shadow.rpc("console_queue")["rows"] if r["id"] == t["id"])
        self.assertEqual(row["email"], "b•••@c•••")
        self.assertEqual(next(r for r in self.lead.rpc("console_queue")["rows"] if r["id"] == t["id"])["email"], "boss@client.test")
        self.shadow.rpc("support_reply", p_ticket=t["id"], p_body="Looks like the print setting", p_files=[], p_internal=True)
        for fn, kw in [("support_reply", dict(p_ticket=t["id"], p_body="Hello", p_files=[], p_internal=False)),
                       ("console_ticket_set", dict(p_ticket=t["id"], p={"status": "pending"})),
                       ("console_ticket_close", dict(p_ticket=t["id"], p={"reply": "Fixed"})),
                       ("console_ticket_customer", dict(p_ticket=t["id"])),
                       ("console_replies_save", dict(p=[])), ("console_bugs", {}), ("console_visits", dict(p_status="")),
                       ("console_help_faq", dict(p={"category": "sales", "q": "Q", "a": "A"}))]:
            with self.assertRaisesRegex(pgtest.SQLError, "not allowed to do this", msg=fn):
                self.shadow.rpc(fn, **kw)
        seen = [m["body"] for m in self.cust.rpc("support_get", p_ticket=t["id"])["messages"]]
        self.assertNotIn("Looks like the print setting", seen)                                  # the customer never sees the note
        self.assertIn("Looks like the print setting", [m["body"] for m in self.lead.rpc("support_get", p_ticket=t["id"])["messages"]])
        self.lead.rpc("support_reply", p_ticket=t["id"], p_body="Hello", p_files=[], p_internal=False)
        self.lead.rpc("console_ticket_close", p_ticket=t["id"], p={"reply": "Fixed now."})

    def test_switching_one_permission_on(self):
        self.owner.rpc("console_staff_save", p={"email": SHADOW, "name": "New Hire", "perms": {"support": True, "desk": {"notes": True, "reply": True}}})
        try:
            t = self.ticket("Reply test")
            self.shadow.rpc("support_reply", p_ticket=t["id"], p_body="On it", p_files=[], p_internal=False)
            with self.assertRaises(pgtest.SQLError):
                self.shadow.rpc("support_chat_end", p_ticket=t["id"], p_solved=True)            # ending chats is "manage"
        finally:
            self.owner.rpc("console_staff_save", p={"email": SHADOW, "name": "New Hire", "perms": {"support": True, "desk": {"notes": True}}})

    def test_customers_can_only_let_in_members_allowed_into_books(self):
        t = self.ticket("Books test")
        self.owner.rpc("console_staff_save", p={"email": SHADOW, "name": "New Hire", "perms": {"support": True, "desk": {"notes": True, "reply": True}}})
        try:
            self.shadow.rpc("support_reply", p_ticket=t["id"], p_body="Can I look?", p_files=[], p_internal=False)
            with self.assertRaisesRegex(pgtest.SQLError, "wait until a team member answers"):
                self.cust.rpc("support_access_grant", p_ticket=t["id"], p_hours=2, p_mode="view")
        finally:
            self.owner.rpc("console_staff_save", p={"email": SHADOW, "name": "New Hire", "perms": {"support": True, "desk": {"notes": True}}})

    def test_access_ends_by_itself_and_only_the_owner_sets_it(self):
        temp = "temp@platform.test"
        with self.assertRaisesRegex(pgtest.SQLError, "in the future"):
            self.owner.rpc("console_staff_save", p={"email": temp, "perms": {"support": True}, "until": "2020-01-01T00:00:00Z"})
        with self.assertRaisesRegex(pgtest.SQLError, "unknown support permission"):
            self.owner.rpc("console_staff_save", p={"email": temp, "perms": {"support": True, "desk": {"delete_everything": True}}})
        with self.assertRaises(pgtest.SQLError):
            self.lead.rpc("console_staff_save", p={"email": temp, "perms": {"support": True}})   # not the owner
        self.owner.rpc("console_staff_save", p={"email": temp, "perms": {"support": True}, "until": "2099-01-01T00:00:00Z"})
        who = self.ws.as_user(temp)
        self.assertTrue(who.rpc("console_me")["until"].startswith("2099"))
        self.ws.sql(f"update platform_admins set until = now() - interval '1 minute' where email = '{temp}';")
        with self.assertRaisesRegex(pgtest.SQLError, "not a member"):
            who.rpc("console_me")
        with self.assertRaises(pgtest.SQLError):
            who.rpc("console_queue")
        staff = {s["email"]: s for s in self.owner.rpc("console_staff_save", p={"email": OWNER, "name": "Owner", "full": True, "until": "2099-01-01T00:00:00Z"})}
        self.assertIsNone(staff[OWNER]["until"])                                               # full access never ends by itself


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class AnnouncementEmails(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.ws.sql(f"insert into public.platform_admins(email, name) values ('{OWNER}', 'Owner');")
        cls.owner = cls.ws.as_user(OWNER)
        cls.owner.rpc("console_staff_save", p={"email": LEAD, "name": "Lead", "perms": {"support": True, "notices": True}})
        cls.owner.rpc("console_staff_save", p={"email": SALES, "name": "Sales", "perms": {"customers": True}})
        cls.lead, cls.sales = cls.ws.as_user(LEAD), cls.ws.as_user(SALES)
        cls.owner.rpc("billing_set_config", p={"enabled": True, "trial_full": False})
        a = cls.ws.as_user("a@one.test"); cls.one = a.rpc("create_company", p_name="One")["id"]       # on trial
        b = cls.ws.as_user("b@two.test"); cls.two = b.rpc("create_company", p_name="Two")["id"]
        cls.owner.rpc("console_record_payment", p={"company": cls.two, "plan": "business", "months": 1, "amount": 0})
        a.rpc("set_member", p_company=cls.one, p_email="staff@one.test", p_is_admin=False, p_gateway=False, p_active=False)
        b.rpc("set_member", p_company=cls.two, p_email="a@one.test", p_is_admin=False, p_gateway=False, p_active=False)  # in both
        for who, cid in (("staff@one.test", cls.one), ("a@one.test", cls.two)):
            cls.ws.sql(f"update members set pending = false, active = true where company_id = '{cid}' and email = '{who}';")

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def test_who_receives_it(self):
        aud = lambda **a: self.owner.rpc("console_mail_audience", p=a)["people"]
        self.assertEqual(aud(), 3)                                       # a@ is in both companies: one email
        self.assertEqual(aud(who="admins"), 2)
        self.assertEqual(aud(status="paying"), 2)
        self.assertEqual(aud(status="trial", who="admins"), 1)
        self.assertEqual(aud(status="paying", plans=["starter"]), 0)
        with self.assertRaises(pgtest.SQLError):
            self.sales.rpc("console_mail_audience", p={})                # no announcements permission
        with self.assertRaises(pgtest.SQLError):
            self.ws.as_user("a@one.test").rpc("console_mail_audience", p={})

    def test_sending_stopping_and_retrying(self):
        with self.assertRaisesRegex(pgtest.SQLError, "no one matches"):
            self.lead.rpc("console_mail_create", p={"subject": "x", "body": "y", "audience": {"status": "paying", "plans": ["starter"]}})
        m = self.lead.rpc("console_mail_create", p={"subject": "Maintenance tonight", "subject_ar": "صيانة الليلة", "body": "From 1 to 2 am.", "audience": {}})
        self.assertEqual(m["total"], 3)
        self.assertEqual(self.lead.rpc("console_mail_me"), LEAD)
        n = self.lead.rpc("console_mail_next", p_mailing=m["id"], p_n=2)
        self.assertEqual((len(n["to"]), n["subject_ar"]), (2, "صيانة الليلة"))
        self.assertEqual(len(self.lead.rpc("console_mail_next", p_mailing=m["id"], p_n=5)["to"]), 1)   # not the claimed ones again
        self.lead.rpc("console_mail_state", p_mailing=m["id"], p_state="stop")
        self.assertEqual(self.lead.rpc("console_mail_next", p_mailing=m["id"], p_n=5)["to"], [])
        self.lead.rpc("console_mail_state", p_mailing=m["id"], p_state="resume")
        to = self.lead.rpc("console_mail_next", p_mailing=m["id"], p_n=5)["to"]
        self.assertEqual(len(to), 3)
        r = self.lead.rpc("console_mail_done", p_mailing=m["id"], p=[{"email": to[0], "ok": True}, {"email": to[1], "ok": True},
                                                                       {"email": to[2], "ok": False, "error": "mailbox full"}])
        self.assertEqual((r["sent"], r["failed"], r["status"]), (2, 1, "done"))
        self.lead.rpc("console_mail_done", p_mailing=m["id"], p=[{"email": to[0], "ok": False}])       # finished results stay
        self.assertEqual(self.lead.rpc("console_mailings")[0]["sent"], 2)
        r = self.lead.rpc("console_mail_state", p_mailing=m["id"], p_state="retry")
        self.assertEqual((r["failed"], r["status"]), (0, "sending"))
        self.assertEqual(self.lead.rpc("console_mail_next", p_mailing=m["id"], p_n=5)["to"], [to[2]])
        with self.assertRaises(pgtest.SQLError):
            self.sales.rpc("console_mail_next", p_mailing=m["id"], p_n=5)


if __name__ == "__main__":
    unittest.main()
