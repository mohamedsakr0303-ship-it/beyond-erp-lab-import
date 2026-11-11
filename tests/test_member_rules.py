"""Company members on a real PostgreSQL (tests/pg): administrators add people; a super administrator changes people
already in the company, makes administrators and gateways; nobody takes another company's person's username."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class MemberRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.boss, cls.adm, cls.evil = (cls.ws.as_user(e) for e in ("boss@a.test", "adm@a.test", "evil@b.test"))
        cls.cid = cls.boss.rpc("create_company", p_name="A")["id"]
        cls.boss.rpc("set_member", p_company=cls.cid, p_email="adm@a.test", p_is_admin=True, p_gateway=False, p_active=True)
        cls.boss.rpc("set_member", p_company=cls.cid, p_email="clerk@a.test", p_is_admin=False, p_gateway=False, p_active=True)
        cls.boss.rpc("link_member", p_company=cls.cid, p_email="clerk@a.test", p_username="clerk", p_active=True)
        cls.bid = cls.evil.rpc("create_company", p_name="B")["id"]

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def member(self, email, cid=None):
        out = self.ws.sql(f"select is_admin, gateway, active from members where company_id='{cid or self.cid}' and email='{email}'")
        return out.strip().splitlines()[-1].strip()

    def refused(self, fn):
        with self.assertRaises(pgtest.SQLError):
            fn()

    def test_an_administrator_cannot_make_themselves_a_gateway(self):
        self.refused(lambda: self.adm.rpc("set_member", p_company=self.cid, p_email="adm@a.test", p_is_admin=True, p_gateway=True, p_active=True))
        self.refused(lambda: self.adm.rpc("set_member", p_company=self.cid, p_email="new@a.test", p_is_admin=True, p_gateway=False, p_active=True))
        self.refused(lambda: self.adm.rpc("set_member", p_company=self.cid, p_email="new2@a.test", p_is_admin=False, p_gateway=True, p_active=True))
        self.adm.rpc("set_member", p_company=self.cid, p_email="worker@a.test", p_is_admin=False, p_gateway=False, p_active=True)  # adding: fine

    def test_an_administrator_cannot_switch_people_off(self):
        self.refused(lambda: self.adm.rpc("link_member", p_company=self.cid, p_email="clerk@a.test", p_username=None, p_active=False))
        self.refused(lambda: self.adm.rpc("link_member", p_company=self.cid, p_email="boss@a.test", p_username=None, p_active=False))
        self.refused(lambda: self.boss.rpc("link_member", p_company=self.cid, p_email="boss@a.test", p_username=None, p_active=False))
        self.assertIn("t", self.member("boss@a.test"))

    def test_another_companys_admin_cannot_take_a_username(self):
        self.evil.rpc("link_member", p_company=self.bid, p_email="clerk@a.test", p_username="gone", p_active=True)
        name = self.ws.sql("select username from people where email='clerk@a.test'").strip().splitlines()[-1].strip()
        self.assertEqual(name, "clerk")


if __name__ == "__main__":
    unittest.main()
