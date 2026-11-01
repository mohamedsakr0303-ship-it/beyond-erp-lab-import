"""The support desk on a real PostgreSQL with every migration applied (tests/pg): tickets queued by the plan's support
priority, the live chat the team starts by answering and only the team ends, what the customer sees of the team, the
support console's customer card and saved replies."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

OWNER = "owner@platform.test"
AGENT = "agent@platform.test"


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class SupportDesk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.ws.sql(f"insert into public.platform_admins(email, name) values ('{OWNER}', 'Owner');")
        cls.owner = cls.ws.as_user(OWNER)
        cls.owner.rpc("console_staff_save", p={"email": AGENT, "name": "Sara Adel", "perms": {"support": True}})
        cls.agent = cls.ws.as_user(AGENT)
        cfg = cls.owner.rpc("billing_admin_overview")["config"]
        cls.plans = {p["code"]: p for p in cfg["plans"]}
        cls.owner.rpc("billing_set_config", p={"enabled": True, "trial_full": False, "support_mode": "free"})

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def customer(self, email, company, plan=None):
        who = self.ws.as_user(email)
        cid = who.rpc("create_company", p_name=company)["id"]
        if plan:
            self.owner.rpc("console_record_payment", p={"company": cid, "plan": plan, "months": 1, "amount": 0})
        return who, cid

    def ticket(self, who, cid, subject):
        return who.rpc("support_open", p_company=cid, p_kind="issue", p_subject=subject, p_body="Details", p_diag={}, p_log=[],
                       p_files=[], p_app_user="")

    def test_plans_get_a_priority_from_their_price(self):
        ranks = {c: p.get("support_rank") for c, p in self.plans.items()}
        paid = sorted((p["price"], c) for c, p in self.plans.items() if not p.get("contact"))
        self.assertEqual([ranks[c] for _, c in paid], list(range(len(paid))))          # cheapest 0, then one more each
        for c, p in self.plans.items():
            if p.get("contact"):
                self.assertEqual(ranks[c], len(paid))                                     # "contact us" plans on top

    def test_the_queue_puts_bigger_plans_first_and_the_chat_runs_until_the_team_ends_it(self):
        small, cs = self.customer("small@x.test", "Small Co", "starter")
        big, cb = self.customer("big@x.test", "Big Co", "business")
        trial, ct = self.customer("trial@x.test", "Trial Co")
        t1 = self.ticket(small, cs, "Starter problem")
        t2 = self.ticket(trial, ct, "Trial problem")
        t3 = self.ticket(big, cb, "Business problem")
        q = [r["ref"] for r in self.agent.rpc("console_queue")["rows"] if r["ref"] in (t1["ref"], t2["ref"], t3["ref"])]
        self.assertEqual(q, [t3["ref"], t1["ref"], t2["ref"]])                          # by plan, then by waiting time

        # the team answers: a live chat opens for the customer, with the agent's first name and no email
        self.agent.rpc("support_reply", p_ticket=t3["id"], p_body="Hello, looking at it now", p_files=[], p_internal=False)
        self.agent.rpc("support_reply", p_ticket=t3["id"], p_body="(internal) check the log", p_files=[], p_internal=True)
        live = big.rpc("support_live", p_after=0)
        self.assertEqual(len(live), 1)
        self.assertEqual((live[0]["chat"], live[0]["unread"], live[0]["agent"]), ("live", 1, "Sara"))
        self.assertEqual([m["body"] for m in live[0]["new"]], ["Hello, looking at it now"])  # never the internal note
        tk = big.rpc("support_get", p_ticket=t3["id"])
        staff_msgs = [m for m in tk["messages"] if m["staff"]]
        self.assertTrue(all(m["email"] == "" and m["name"] == "Sara" for m in staff_msgs))
        self.assertEqual(big.rpc("support_live", p_after=0)[0]["unread"], 0)            # read once opened
        q = self.agent.rpc("console_queue")["rows"]
        row = next(r for r in q if r["id"] == t3["id"])
        self.assertEqual((row["turn"], row["assignee"], row["chat"]), ("customer", AGENT, "live"))
        self.assertEqual(q[0]["turn"], "team")                                            # the answered one leaves the top

        # the customer writes back: the ticket is the team's turn again; only the team can end the chat
        big.rpc("support_reply", p_ticket=t3["id"], p_body="Thanks, here is a voice note", p_files=[], p_internal=False)
        self.assertEqual(next(r for r in self.agent.rpc("console_queue")["rows"] if r["id"] == t3["id"])["turn"], "team")
        with self.assertRaises(pgtest.SQLError):
            big.rpc("support_chat_end", p_ticket=t3["id"], p_solved=True)
        self.agent.rpc("support_chat_end", p_ticket=t3["id"], p_solved=True)
        live = big.rpc("support_live", p_after=0)
        self.assertEqual((live[0]["chat"], live[0]["status"]), ("ended", "resolved"))   # the customer sees it ended, once
        big.rpc("support_seen", p_ticket=t3["id"])
        self.assertEqual(big.rpc("support_live", p_after=0), [])
        with self.assertRaisesRegex(pgtest.SQLError, "no live chat"):
            self.agent.rpc("support_chat_end", p_ticket=t3["id"], p_solved=False)
        # writing again resumes the chat
        self.agent.rpc("support_reply", p_ticket=t3["id"], p_body="One more thing", p_files=[], p_internal=False)
        self.assertEqual(big.rpc("support_live", p_after=0)[0]["chat"], "live")

    def test_a_first_reply_target_and_what_support_sees_of_the_customer(self):
        plans = [dict(p, reply_hours=4) if p["code"] == "pro" else p for p in self.plans.values()]
        self.owner.rpc("billing_set_config", p={"plans": plans})
        c, cid = self.customer("pro@x.test", "Pro Co", "pro")
        t = self.ticket(c, cid, "Pro problem")
        row = next(r for r in self.agent.rpc("console_queue")["rows"] if r["id"] == t["id"])
        self.assertIsNotNone(row["due_at"])
        card = self.agent.rpc("console_ticket_customer", p_ticket=t["id"])
        self.assertEqual((card["company"]["plan"], card["company"]["users"], card["priority"]["reply_hours"]), ("pro", 1, 4))
        self.assertNotIn("payments", card)
        with self.assertRaises(pgtest.SQLError):                                          # customers cannot use it
            c.rpc("console_ticket_customer", p_ticket=t["id"])

    def test_saved_replies(self):
        r = [{"title": "Greeting", "body": "Hello, thanks for writing."}]
        self.assertEqual(self.agent.rpc("console_replies_save", p=r), r)
        self.assertEqual(self.agent.rpc("console_queue")["replies"], r)
        with self.assertRaises(pgtest.SQLError):
            self.agent.rpc("console_replies_save", p=[{"title": "", "body": "x"}])
        with self.assertRaises(pgtest.SQLError):
            self.ws.as_user("someone@x.test").rpc("console_replies_save", p=r)


if __name__ == "__main__":
    unittest.main()
