"""Referrals and upgrades on a real PostgreSQL with every migration applied (tests/pg).

A share link links a new person to whoever shared it; the invited person gets 20 % off their first payment; when they
pay, the person who shared earns one reward: 20 % off their own first subscription or 30 % off an upgrade. Discounts
never add up (the largest applies), rewards wait for a payment they apply to, and an upgrade pays only the difference
for the days left in the paid period.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

OWNER = "owner@platform.test"


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class Referrals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.ws.sql(f"insert into public.platform_admins(email, name) values ('{OWNER}', 'Owner');")
        cls.owner = cls.ws.as_user(OWNER)
        cfg = cls.owner.rpc("billing_admin_overview")["config"]
        plans = [dict(p, modules=["sales"]) if p["code"] == "starter" else p for p in cfg["plans"]]
        cls.owner.rpc("billing_set_config", p={"enabled": True, "plans": plans, "module_prices": {"stock": 300}, "trial_full": False,
                                                "extra_company_pct": 100})
        cls.method = cls.owner.rpc("console_method_save", p={"kind": "instapay", "name": "InstaPay", "details": "x", "needs_proof": False})[0]["id"]
        cls.price = {p["code"]: p["price"] for p in plans}

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def person(self, email, company):
        who = self.ws.as_user(email)
        return who, who.rpc("create_company", p_name=company)["id"]

    def buy(self, who, cid, plan="starter", **line):
        o = who.rpc("billing_order_open", p_plan=plan, p_months=1, p_lines=[{"company_id": cid, **line}], p_method=self.method)
        self.owner.rpc("console_order_decide", p_order=o["id"], p_approve=True, p_note="")
        return o

    def test_the_whole_journey(self):
        a, ca = self.person("sharer@x.test", "Sharer Co")
        self.buy(a, ca)                                                        # the person who shares is a customer
        code = a.rpc("referral_my")["code"]
        self.assertRegex(code, r"^[A-Z2-9]{8}$")
        self.assertEqual(a.rpc("referral_claim", p_code=code)["reason"], "own")
        self.assertEqual(a.rpc("referral_claim", p_code="NOPE1234")["reason"], "unknown")

        # the invited person: 20 % off their first payment, once
        b, cb = self.person("friend@x.test", "Friend Co")
        self.assertTrue(b.rpc("referral_claim", p_code=code)["ok"])
        self.assertEqual(b.rpc("referral_claim", p_code=code)["reason"], "already")
        q = b.rpc("billing_order_quote", p_plan="starter", p_months=1, p_lines=[{"company_id": cb}])
        self.assertEqual((q["referral"]["source"], q["referral"]["pct"]), ("friend", 20))
        self.assertEqual(q["amount"], round(self.price["starter"] * 0.8, 2))
        mine = a.rpc("referral_my")
        self.assertEqual((mine["joined"], mine["paid"], mine["available"]), (1, 0, 0))   # joined is not a reward yet
        self.assertEqual(mine["people"][0]["who"], "fr***@x.test")
        self.buy(b, cb)
        mine = a.rpc("referral_my")
        self.assertEqual((mine["paid"], mine["available"], mine["unseen"]), (1, 1, 1))
        self.assertEqual(a.rpc("referral_my")["unseen"], 0)                    # seen once shown
        q = b.rpc("billing_order_quote", p_plan="starter", p_months=1, p_lines=[{"company_id": cb}])
        self.assertNotIn("referral", q)                                         # the friend's discount was once only

        # the reward: not on a plain renewal, 30 % on an upgrade
        q = a.rpc("billing_order_quote", p_plan="starter", p_months=1, p_lines=[{"company_id": ca}])
        self.assertNotIn("referral", q)
        up = a.rpc("billing_upgrade_quote", p_lines=[{"company_id": ca, "plan": "business"}])
        line = up["lines"][0]
        diff = self.price["business"] - self.price["starter"]
        self.assertEqual(line["pre"], round(diff * line["days"] / 30, 2))      # the difference for the days left
        self.assertEqual((up["referral"]["source"], up["referral"]["pct"]), ("reward", 30))
        self.assertEqual(up["amount"], round(line["pre"] * 0.7, 2))
        until = a.rpc("billing_status", p_company=ca)["paid_until"]
        o = a.rpc("billing_upgrade_open", p_lines=[{"company_id": ca, "plan": "business", "modules": ["stock"]}], p_method=self.method)
        self.assertEqual(o["kind"], "upgrade")
        self.owner.rpc("console_order_decide", p_order=o["id"], p_approve=True, p_note="")
        st = a.rpc("billing_status", p_company=ca)
        self.assertEqual((st["plan"], st["paid_until"]), ("business", until))  # bigger plan now, same paid period
        mine = a.rpc("referral_my")
        self.assertEqual((mine["available"], mine["used"], mine["rewards"][0]["pct"]), (0, 1, 30))
        with self.assertRaisesRegex(pgtest.SQLError, "more than it has now"):
            a.rpc("billing_upgrade_quote", p_lines=[{"company_id": ca, "plan": "business"}])

    def test_a_first_subscription_reward_and_discounts_that_never_add_up(self):
        c, cc = self.person("newsharer@x.test", "New Sharer")          # shares before ever paying
        code = c.rpc("referral_my")["code"]
        d, cd = self.person("buyer@x.test", "Buyer Co")
        d.rpc("referral_claim", p_code=code)
        self.buy(d, cd)
        q = c.rpc("billing_order_quote", p_plan="starter", p_months=1, p_lines=[{"company_id": cc}])
        self.assertEqual((q["referral"]["source"], q["referral"]["pct"]), ("reward", 20))    # first subscription: 20 %
        # a bigger standing discount wins, and the reward waits
        self.owner.rpc("billing_set_company", p_company=cc, p={"discount_pct": 50})
        q = c.rpc("billing_order_quote", p_plan="starter", p_months=1, p_lines=[{"company_id": cc}])
        self.assertNotIn("referral", q)
        self.assertEqual(q["amount"], round(self.price["starter"] * 0.5, 2))
        self.buy(c, cc)
        self.assertEqual(c.rpc("referral_my")["available"], 1)

    def test_a_customer_cannot_be_claimed_and_the_owner_can_void(self):
        e, ce = self.person("vet@x.test", "Veteran")
        self.buy(e, ce)
        f, _ = self.person("other@x.test", "Other")
        self.assertEqual(e.rpc("referral_claim", p_code=f.rpc("referral_my")["code"])["reason"], "customer")
        g, cg = self.person("g@x.test", "G Co")
        g.rpc("referral_claim", p_code=f.rpc("referral_my")["code"])
        self.buy(g, cg)
        rows = self.owner.rpc("console_referrals")["rows"]
        rid = next(r["id"] for r in rows if r["referred"] == "g@x.test")
        self.owner.rpc("console_referral_set", p_id=rid, p_void=True)
        self.assertEqual(f.rpc("referral_my")["available"], 0)
        self.owner.rpc("console_referral_settings", p={"on": False})
        h, _ = self.person("h@x.test", "H Co")
        self.assertEqual(h.rpc("referral_claim", p_code=f.rpc("referral_my")["code"])["reason"], "off")
        self.owner.rpc("console_referral_settings", p={"on": True})
        with self.assertRaises(pgtest.SQLError):
            f.rpc("console_referrals")

    # ------------------------------------------------------------------ ways to misuse it, closed
    def test_no_earning_from_your_own_company(self):
        a, ca = self.person("boss@x.test", "Boss Co")
        self.buy(a, ca)
        code = a.rpc("referral_my")["code"]
        sock, _ = self.person("boss.alt@x.test", "Alt Co")
        self.assertTrue(sock.rpc("referral_claim", p_code=code)["ok"])
        a.rpc("set_member", p_company=ca, p_email="boss.alt@x.test", p_is_admin=True, p_gateway=False, p_active=True)
        q = sock.rpc("billing_order_quote", p_plan="starter", p_months=1, p_lines=[{"company_id": ca}])
        self.assertNotIn("referral", q)                                          # no invitation discount on the sharer's company
        self.buy(sock, ca)
        self.assertEqual(a.rpc("referral_my")["available"], 0)                  # and no reward for paying it
        # sharing a company already: the link does not count
        t, _ = self.person("team@x.test", "Team Own")
        a.rpc("set_member", p_company=ca, p_email="team@x.test", p_is_admin=False, p_gateway=False, p_active=True)
        self.assertEqual(t.rpc("referral_claim", p_code=code)["reason"], "team")

    def test_no_claiming_each_others_links(self):
        p, _ = self.person("p1@x.test", "P1")
        q, _ = self.person("p2@x.test", "P2")
        self.assertTrue(q.rpc("referral_claim", p_code=p.rpc("referral_my")["code"])["ok"])
        self.assertEqual(p.rpc("referral_claim", p_code=q.rpc("referral_my")["code"])["reason"], "mutual")

    def test_the_invitation_discount_is_used_once(self):
        s, _ = self.person("s1@x.test", "S1")
        f, c1 = self.person("f1@x.test", "F1")
        c2 = f.rpc("create_company", p_name="F2")["id"]
        f.rpc("referral_claim", p_code=s.rpc("referral_my")["code"])
        o1 = f.rpc("billing_order_open", p_plan="starter", p_months=1, p_lines=[{"company_id": c1}], p_method=self.method)
        f.rpc("billing_order_submit", p_order=o1["id"], p_proof=[], p_ref="", p_amount=o1["amount"], p_note="")
        o2 = f.rpc("billing_order_open", p_plan="starter", p_months=1, p_lines=[{"company_id": c2}], p_method=self.method)
        self.assertEqual((o1["discount"]["source"], o2["discount"]), ("friend", None))   # held by the first order
        self.owner.rpc("console_order_decide", p_order=o1["id"], p_approve=True, p_note="")
        self.assertEqual(s.rpc("referral_my")["available"], 1)

    def test_a_reward_is_used_once_and_rechecked_on_approval(self):
        s, cs = self.person("r1@x.test", "R1")
        self.buy(s, cs)
        f, cf = self.person("r2@x.test", "R2")
        f.rpc("referral_claim", p_code=s.rpc("referral_my")["code"])
        self.buy(f, cf)
        up = [{"company_id": cs, "plan": "business"}]
        o1 = s.rpc("billing_upgrade_open", p_lines=up, p_method=self.method)
        self.assertEqual(o1["discount"]["source"], "reward")
        # the first order is rejected, a new one takes the reward, then the old one cannot be sent again
        self.owner.rpc("console_order_decide", p_order=o1["id"], p_approve=False, p_note="unclear")
        o2 = s.rpc("billing_upgrade_open", p_lines=up, p_method=self.method)       # replaces the rejected one
        self.assertEqual(o2["discount"]["source"], "reward")
        with self.assertRaises(pgtest.SQLError):
            s.rpc("billing_order_submit", p_order=o1["id"], p_proof=[], p_ref="", p_amount=1, p_note="")
        self.owner.rpc("console_order_decide", p_order=o2["id"], p_approve=True, p_note="")
        self.assertEqual(s.rpc("referral_my")["used"], 1)

    def test_an_upgrade_never_lowers_the_plan_or_users(self):
        a, ca = self.person("lower@x.test", "Lower Co")
        self.buy(a, ca, plan="business", extra_users=2)
        with self.assertRaisesRegex(pgtest.SQLError, "current plan or a bigger one"):
            a.rpc("billing_upgrade_quote", p_lines=[{"company_id": ca, "plan": "starter", "modules": ["stock"]}])
        with self.assertRaisesRegex(pgtest.SQLError, "extra users or adds more"):
            a.rpc("billing_upgrade_quote", p_lines=[{"company_id": ca, "extra_users": 0, "ai": True}])

    def test_paying_through_the_platform_team_counts_as_a_customer(self):
        a, ca = self.person("offline@x.test", "Offline Co")
        self.owner.rpc("console_record_payment", p={"company": ca, "plan": "starter", "months": 1, "amount": 500})
        s, _ = self.person("anyone@x.test", "Anyone")
        self.assertEqual(a.rpc("referral_claim", p_code=s.rpc("referral_my")["code"])["reason"], "customer")


if __name__ == "__main__":
    unittest.main()
