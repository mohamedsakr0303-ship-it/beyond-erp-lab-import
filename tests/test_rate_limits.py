"""Limits on doing the same thing over and over (tests/pg, a real PostgreSQL with every migration): tickets, sign-in
tries and AI requests stop at their limit with HTTP 429 and a message in both languages; the team is not limited;
a sign-in that works clears the wrong tries; only the server may ask the sign-in and AI gates."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

OWNER = "owner@platform.test"


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class RateLimits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.ws.sql(f"insert into public.platform_admins(email, name) values ('{OWNER}', 'Owner');")
        cls.boss = cls.ws.as_user("boss@one.test")
        cls.cid = cls.boss.rpc("create_company", p_name="One")["id"]
        cls.server = cls.ws.as_user("", role="service_role")

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def ticket(self, who, n):
        return who.rpc("support_open", p_company=self.cid, p_kind="issue", p_subject=f"T{n}", p_body="b", p_diag={},
                       p_log=[], p_files=[], p_app_user="")

    def test_the_eleventh_ticket_in_an_hour_waits(self):
        for n in range(10):
            self.ticket(self.boss, n)
        with self.assertRaises(pgtest.SQLError) as cm:
            self.ticket(self.boss, 11)
        self.assertIn("Too many requests", str(cm.exception))
        self.assertIn("طلبات كثيرة", str(cm.exception))                    # and in Arabic, for an Arabic screen
        with self.assertRaises(pgtest.SQLError) as cm:                     # the code PostgREST answers as HTTP 429
            self.boss.execute("\\set VERBOSITY verbose\nselect public.support_open('%s', 'issue', 's', 'b', '{}', '[]', '[]', '');" % self.cid)
        self.assertIn("PT429", str(cm.exception))

    def test_the_team_is_not_limited(self):
        owner = self.ws.as_user(OWNER)
        tid = self.ws.sql(f"select min(id) from support_tickets where company_id = '{self.cid}'").strip()
        if not tid:
            tid = str(self.ticket(self.boss, 0)["id"])
        for n in range(70):                                        # more than any customer may send in 10 minutes
            owner.rpc("support_reply", p_ticket=int(tid), p_body=f"answer {n}", p_files=[], p_internal=True)

    def test_sign_in_stops_after_ten_wrong_tries_and_a_good_one_clears_them(self):
        gate = lambda r, u="ali", ip="10.0.0.1": self.server.rpc("login_gate", p_username=u, p_ip=ip, p_result=r)
        for _ in range(10):
            self.assertTrue(gate("check")["ok"])
            gate("failed")
        self.assertFalse(gate("check")["ok"])
        self.assertFalse(gate("check", "ALI")["ok"])                       # the same username, any case
        self.assertTrue(gate("check", "omar", "10.0.0.9")["ok"])          # someone else is not locked out
        gate("ok")
        self.assertTrue(gate("check")["ok"])
        for n in range(50):                                                 # one address trying many usernames
            gate("failed", f"user{n}", "10.0.0.66")
        self.assertFalse(gate("check", "fresh", "10.0.0.66")["ok"])
        with self.assertRaises(pgtest.SQLError):
            self.boss.rpc("login_gate", p_username="ali", p_ip="", p_result="ok")     # only the server

    def test_the_ai_takes_twelve_a_minute_per_person(self):
        got = [self.server.rpc("ai_rate", p_company=self.cid, p_email="boss@one.test")["ok"] for _ in range(13)]
        self.assertEqual(got, [True] * 12 + [False])
        self.assertTrue(self.server.rpc("ai_rate", p_company=self.cid, p_email="clerk@one.test")["ok"])
        with self.assertRaises(pgtest.SQLError):
            self.boss.rpc("ai_rate", p_company=self.cid, p_email="x@one.test")


if __name__ == "__main__":
    unittest.main()
