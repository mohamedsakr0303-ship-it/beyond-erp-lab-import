"""The role change a device sends through the log ("its account is this company's super administrator") is stored
only from an account the workspace counts as one (tests/pg, a real PostgreSQL with every migration)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

OP = dict(p_app_user="", p_device="pc", p_method="POST", p_path="/api/users/workspace-role", p_body="{}",
          p_content_type="application/json", p_clock="2026-11-16 10:00:00", p_version="1.3.0")


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class WorkspaceRole(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.boss, cls.clerk = cls.ws.as_user("boss@one.test"), cls.ws.as_user("clerk@one.test")
        cls.cid = cls.boss.rpc("create_company", p_name="One")["id"]
        cls.ws.sql(f"insert into public.members(company_id, email, is_admin, added_by) values ('{cls.cid}', 'clerk@one.test', true, 'boss@one.test');")

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def test_only_the_company_s_super_administrator_says_so(self):
        # Direct RPC calls must not be able to poison the shared log with a future or malformed version.
        for version in ("999.0.0", "not-a-version"):
            bad = {**OP, "p_version": version, "p_path": "/api/contacts"}
            with self.assertRaisesRegex(pgtest.SQLError, "workspace client version|invalid workspace client version"):
                self.boss.rpc("append_op", p_company=self.cid, p_seq=1, **bad)
        with self.assertRaisesRegex(pgtest.SQLError, "device-local"):
            self.boss.rpc("append_op", p_company=self.cid, p_seq=1, **{**OP, "p_path": "/api/einvoice/submit"})
        with self.assertRaisesRegex(pgtest.SQLError, "workspace client version|invalid workspace client version"):
            self.boss.rpc("put_snapshot", p_company=self.cid, p_seq=0, p_version="999.0.0", p_data="poison")
        with self.assertRaises(pgtest.SQLError) as cm:                    # an administrator, not a super administrator
            self.clerk.rpc("append_op", p_company=self.cid, p_seq=1, **OP)
        self.assertIn("super administrator", str(cm.exception))
        self.assertEqual(self.boss.rpc("append_op", p_company=self.cid, p_seq=1, **OP)["seq"], 1)   # its creator
        self.assertFalse(self.clerk.rpc("my_super", p_company=self.cid))
        self.assertTrue(self.boss.rpc("my_super", p_company=self.cid))
        ok = {**OP, "p_path": "/api/bus"}                                  # every other change as before
        self.assertEqual(self.clerk.rpc("append_op", p_company=self.cid, p_seq=2, **ok)["seq"], 2)


if __name__ == "__main__":
    unittest.main()
