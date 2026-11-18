"""Being told you were added to a company (tests/pg, a real PostgreSQL with every migration):

* memberships that already exist, and those made any other way (a company's creator), count as seen: nobody gets a
  card for a company they have been in all along;
* a membership the sign-in function has just added (welcome_seen = false) shows in my_companies with who added them;
* welcome_seen marks only the caller's own membership, once; someone else cannot clear (or see) another person's.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class JoinNotice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.ws.sql("grant all on all tables in schema public to service_role;"
                   "grant all on all sequences in schema public to service_role;")
        cls.server = cls.ws.as_user("", role="service_role")
        cls.layla, cls.boss, cls.omar = cls.ws.as_user("layla@a.test"), cls.ws.as_user("boss@b.test"), cls.ws.as_user("omar@c.test")
        cls.mine = cls.layla.rpc("create_company", p_name="Layla Co")["id"]
        cls.theirs = cls.boss.rpc("create_company", p_name="Boss Co")["id"]
        cls.ws.sql("update public.people set username = 'boss' where email = 'boss@b.test';")
        # what supabase/functions/manage-user does for someone it has just added
        cls.server.execute(f"insert into public.members(company_id, email, active, pending, added_by, welcome_seen) values "
                           f"('{cls.theirs}', 'layla@a.test', true, false, 'boss@b.test', false);")

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def cos(self, who):
        return {c["name"]: c for c in who.rpc("my_companies")}

    def test_1_own_company_has_no_card_the_new_one_does(self):
        cos = self.cos(self.layla)
        self.assertNotIn("welcome", cos["Layla Co"])                       # her own, from the start
        self.assertEqual(cos["Boss Co"]["welcome"]["by"], "boss")          # who added her (their username)
        self.assertNotIn("welcome", self.cos(self.boss)["Boss Co"])        # the creator: never

    def test_2_someone_else_cannot_clear_it(self):
        self.omar.rpc("welcome_seen", p_company=self.theirs)               # not a member: changes nothing
        self.boss.rpc("welcome_seen", p_company=self.theirs)               # the administrator: only their own row
        self.assertIn("welcome", self.cos(self.layla)["Boss Co"])

    def test_3_seen_once_it_stays_seen(self):
        self.layla.rpc("welcome_seen", p_company=self.theirs)
        self.assertNotIn("welcome", self.cos(self.layla)["Boss Co"])
        self.assertEqual(self.ws.sql(f"select welcome_seen from public.members where company_id = '{self.theirs}' "
                                     "and email = 'layla@a.test';").strip(), "t")

    def test_4_existing_memberships_default_to_seen(self):
        self.assertEqual(self.ws.sql("select count(*) from public.members where not welcome_seen and email <> 'layla@a.test';").strip(), "0")

    def test_5_anon_cannot_call_it(self):
        with self.assertRaises(pgtest.SQLError):
            self.ws.as_user("", role="anon").rpc("welcome_seen", p_company=self.theirs)


if __name__ == "__main__":
    unittest.main()
