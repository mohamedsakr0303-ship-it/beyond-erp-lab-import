"""Owner console calls no other test reached, on a real PostgreSQL with every migration applied (tests/pg): the companies
list, payment methods (list, add, delete; a method used by an order is switched off, not deleted), announcements list and
Help & FAQ categories (add, rename, delete) — and that people outside the team are refused."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

OWNER = "owner@platform.test"


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class ConsoleCalls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.ws.sql(f"insert into public.platform_admins(email, name) values ('{OWNER}', 'Owner');")
        cls.owner = cls.ws.as_user(OWNER)
        cls.customer = cls.ws.as_user("boss@co.test")
        cls.cid = cls.customer.rpc("create_company", p_name="Listed Co")["id"]

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def test_companies_list(self):
        rows = self.owner.rpc("console_companies")
        self.assertIn(self.cid, [r.get("id") for r in rows])
        with self.assertRaises(pgtest.SQLError):
            self.customer.rpc("console_companies")

    def test_payment_methods(self):
        out = self.owner.rpc("console_method_save", p={"name": "Test wallet", "kind": "instapay", "details": "01000000000"})
        m = next(x for x in out if x["name"] == "Test wallet")
        self.assertIn(m["id"], [x["id"] for x in self.owner.rpc("console_methods")])
        out = self.owner.rpc("console_method_delete", p_id=m["id"])
        self.assertNotIn(m["id"], [x["id"] for x in out])                    # never used: deleted
        with self.assertRaises(pgtest.SQLError):
            self.customer.rpc("console_methods")
        with self.assertRaises(pgtest.SQLError):
            self.customer.rpc("console_method_delete", p_id=1)

    def test_a_method_used_by_an_order_is_switched_off_not_deleted(self):
        out = self.owner.rpc("console_method_save", p={"name": "Used wallet", "kind": "instapay"})
        m = next(x for x in out if x["name"] == "Used wallet")
        self.owner.rpc("billing_set_config", p={"enabled": True})
        self.customer.rpc("billing_order_open", p_plan="business", p_months=1, p_lines=[{"company_id": self.cid}], p_method=m["id"])
        out = self.owner.rpc("console_method_delete", p_id=m["id"])
        kept = next(x for x in out if x["id"] == m["id"])
        self.assertFalse(kept["active"])

    def test_announcements_list(self):
        self.owner.rpc("console_notice_save", p={"title": "Maintenance tonight", "body": "Short pause", "level": "info"})
        self.assertIn("Maintenance tonight", [n["title"] for n in self.owner.rpc("console_notices")])
        with self.assertRaises(pgtest.SQLError):
            self.customer.rpc("console_notices")

    def test_help_categories(self):
        out = self.owner.rpc("console_help_category", p={"key": "Billing-Test", "name": "Billing", "name_ar": "الفواتير", "icon": "💳"})
        cats = out.get("categories", out) if isinstance(out, dict) else out
        self.assertIn("billing-test", [c["key"] for c in cats])
        out = self.owner.rpc("console_help_category", p={"key": "billing-test", "name": "Billing & plans"})
        cats = out.get("categories", out) if isinstance(out, dict) else out
        self.assertEqual(next(c for c in cats if c["key"] == "billing-test")["name"], "Billing & plans")
        out = self.owner.rpc("console_help_category", p={"key": "billing-test", "delete": True})
        cats = out.get("categories", out) if isinstance(out, dict) else out
        self.assertNotIn("billing-test", [c["key"] for c in cats])
        with self.assertRaises(pgtest.SQLError):
            self.owner.rpc("console_help_category", p={"key": "x", "name": ""})
        with self.assertRaises(pgtest.SQLError):
            self.customer.rpc("console_help_category", p={"key": "x", "name": "X"})


if __name__ == "__main__":
    unittest.main()
