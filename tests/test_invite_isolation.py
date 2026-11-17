"""Inviting someone who already has a company (tests/pg, a real PostgreSQL with every migration), the way the sign-in
function adds them (the server's own key, as supabase/functions/manage-user does):

* the inviting company's seats are a hard limit: an invitation past them is refused;
* the person's own company, their membership there and their username stay exactly as they were — they only gain a
  company they can switch to;
* belonging to someone else's company is not a company of their own: the plan's number of companies they may create
  is unchanged (they cannot get round it by being invited).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

OWNER = "owner@platform.test"


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class InviteIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.ws.sql(f"insert into public.platform_admins(email, name) values ('{OWNER}', 'Owner');"
                   "grant all on all tables in schema public to service_role;"            # (as Supabase gives its server key)
                   "grant all on all sequences in schema public to service_role;")
        cls.owner = cls.ws.as_user(OWNER)
        cls.server = cls.ws.as_user("", role="service_role")
        cls.layla, cls.boss = cls.ws.as_user("layla@a.test"), cls.ws.as_user("boss@b.test")
        cls.mine = cls.layla.rpc("create_company", p_name="Layla Co")["id"]          # her own company
        cls.ws.sql("update public.people set username = 'layla' where email = 'layla@a.test';")
        cls.theirs = cls.boss.rpc("create_company", p_name="Boss Co")["id"]          # the company inviting her

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def invite(self, cid, email):
        """What the sign-in function does for an existing account: the person row untouched, a membership here."""
        self.server.execute(f"insert into public.people(email) values ('{email}') on conflict (email) do nothing;")
        self.server.execute(f"insert into public.members(company_id, email, active, pending, added_by) values "
                            f"('{cid}', '{email}', true, false, 'boss@b.test') on conflict (company_id, email) do update set "
                            f"active = excluded.active, pending = excluded.pending;")

    def snapshot(self):
        return self.ws.sql("select string_agg(c.name || ':' || m.email || ':' || m.is_admin || ':' || m.active, ',' order by c.name) "
                           "from public.members m join public.companies c on c.id = m.company_id where m.email = 'layla@a.test';").strip(), \
            self.ws.sql("select username from public.people where email = 'layla@a.test';").strip()

    def test_1_an_invitation_adds_a_company_and_changes_nothing_of_hers(self):
        before_members, before_name = self.snapshot()
        self.assertEqual(before_members, "Layla Co:layla@a.test:true:true")
        self.invite(self.theirs, "layla@a.test")
        after_members, after_name = self.snapshot()
        self.assertEqual(after_members, "Boss Co:layla@a.test:false:true,Layla Co:layla@a.test:true:true")   # her own as it was
        self.assertEqual(after_name, before_name)
        cos = sorted(c["name"] for c in self.layla.rpc("my_companies"))
        self.assertEqual(cos, ["Boss Co", "Layla Co"])                                 # one more to switch to
        self.assertEqual(self.ws.sql(f"select created_by from public.companies where id = '{self.mine}';").strip(), "layla@a.test")

    def test_2_the_inviting_company_s_seats_are_a_hard_limit(self):
        self.owner.rpc("billing_set_company", p_company=self.theirs, p={"overrides": {"users": 2}})   # boss + layla: full
        with self.assertRaisesRegex(pgtest.SQLError, "seat_limit"):
            self.invite(self.theirs, "omar@c.test")
        self.owner.rpc("billing_set_company", p_company=self.theirs, p={"overrides": {}})

    def test_3_being_invited_does_not_raise_her_own_company_limit(self):
        self.owner.rpc("billing_set_config", p={"max_companies": 1})
        try:
            with self.assertRaisesRegex(pgtest.SQLError, "up to 1 compan"):
                self.layla.rpc("create_company", p_name="Layla Two")                   # still one of her own at most
            self.assertEqual(self.ws.sql("select count(*) from public.companies where created_by = 'layla@a.test';").strip(), "1")
        finally:
            self.owner.rpc("billing_set_config", p={"max_companies": 0})


if __name__ == "__main__":
    unittest.main()
