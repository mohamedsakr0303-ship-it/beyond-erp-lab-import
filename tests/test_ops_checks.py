"""Sales, purchasing and projects: the operations review's findings, each one a test, on the demo books."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import accounting as acc, auth, demo, purchasing, server  # noqa: E402
from erp.accounting import ValidationError  # noqa: E402
from erp.db import Database  # noqa: E402


class App:
    pass


class Ops(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Database(":memory:")
        demo.load(cls.base, "admin")
        auth.create_user(cls.base, {"username": "boss", "password": "pw123456"}, first=True)
        auth.create_user(cls.base, {"username": "junior", "password": "pw123456", "needs_approval": True,
                                    "perms": {"sales": "edit", "purchasing": "edit", "accounting": "add"}})

    def setUp(self):
        self.db = Database(":memory:")
        self.base.conn.backup(self.db.conn)
        self.app = App()
        self.app.db, self.app.backup = self.db, (lambda *a, **k: None)
        self.tok = {u: auth.login(self.db, u, p)[0] for u, p in (("junior", "pw123456"), ("boss", "pw123456"))}
        auth._fails.clear()

    def call(self, m, p, b=None, who="junior"):
        r = server.handle(self.app, m, p, {"x-session": self.tok[who], "x-requested-with": "BeyondERP",
                                           "content-type": "application/json"}, json.dumps(b or {}).encode())
        return r.status, json.loads(r.body or b"{}")

    def test_someone_who_needs_approval_cannot_post_from_any_screen(self):
        x = self.db.one("SELECT id FROM extracts WHERE status='draft' LIMIT 1")["id"]
        st, r = self.call("POST", f"/api/extracts/{x}/post", {"confirm_over": 1})
        self.assertEqual(st, 403, r)
        po = self.db.one("SELECT id FROM purchase_orders WHERE status='approved' LIMIT 1")["id"]
        ln = purchasing.get(self.db, po)["lines"][0]
        st, r = self.call("POST", f"/api/purchase-orders/{po}/receive", {"date": "2026-11-01", "lines": [{"line_id": ln["id"], "qty": 1}]})
        self.assertEqual(st, 403, r)
        before = self.db.one("SELECT COUNT(*) n FROM journal_entries")["n"]
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM journal_entries")["n"], before)

    def test_the_hold_reads_in_the_readers_language(self):
        x = self.db.one("SELECT id FROM extracts WHERE status='draft' LIMIT 1")["id"]
        h = {"x-session": self.tok["junior"], "x-requested-with": "BeyondERP", "content-type": "application/json"}
        ar = json.loads(server.handle(self.app, "POST", f"/api/extracts/{x}/post", {**h, "x-lang": "ar"}, b'{"confirm_over": 1}').body)
        en = json.loads(server.handle(self.app, "POST", f"/api/extracts/{x}/post", h, b'{"confirm_over": 1}').body)
        self.assertIn("اعتماد", ar["error"])
        self.assertIn("approval", en["error"])
        self.assertNotIn("error_ar", en)

    def test_billing_past_the_boq_needs_a_confirmation(self):
        x = acc.get_extract(self.db, self.db.one("SELECT id FROM extracts WHERE number='INV-D003'")["id"])
        lines = [{**{k: ln[k] for k in ("category", "item_no", "description", "unit", "price")}, "qty": (ln.get("contract_qty") or ln["qty"]) * 5}
                 for ln in x["lines"]]
        acc.save_extract(self.db, {**{k: x[k] for k in ("bu_id", "boq_id", "number", "date", "customer")}, "lines": lines}, x["id"])
        with self.assertRaises(acc.NeedsConfirm):
            acc.post_extract(self.db, x["id"])
        tok = acc.OVER_OK.set(True)
        try:
            acc.post_extract(self.db, x["id"])
        finally:
            acc.OVER_OK.reset(tok)

    def test_an_order_cannot_take_more_than_its_request_without_a_confirmation(self):
        pr = self.db.one("SELECT id, code FROM purchase_requests WHERE status='approved'")
        from erp import procurement
        pl = procurement.get(self.db, pr["id"])["lines"][0]
        d = {"supplier": "Acme", "date": "2026-11-01", "pr_id": pr["id"],
             "lines": [{"pr_line_id": pl["id"], "kind": "cost", "description": pl["description"], "qty": pl["qty"] * 100, "rate": 1,
                        "account": "5010"}], "bu_id": pl.get("bu_id") or 1}
        with self.assertRaises(acc.NeedsConfirm):
            purchasing.save(self.db, d, "admin")

    def test_voiding_a_receipt_puts_the_order_back(self):
        po = self.db.one("SELECT id FROM purchase_orders WHERE status='approved' ORDER BY id LIMIT 1")["id"]
        ln = purchasing.get(self.db, po)["lines"][0]
        purchasing.receive(self.db, po, {"date": "2026-11-01", "lines": [{"line_id": ln["id"], "qty": ln["left"]}]}, "admin")
        eid = self.db.one("SELECT entry_id FROM po_receipts WHERE po_id=? ORDER BY id DESC", (po,))["entry_id"]
        with self.assertRaises(ValidationError):
            acc.edit_entry(self.db, eid, {"memo": "changed"})          # changed from the order, not the journal
        acc.void_entry(self.db, eid, "wrong", "admin")
        after = purchasing.get(self.db, po)
        self.assertNotEqual(after["status"], "received")
        self.assertEqual(after["lines"][0]["left"], ln["left"])

    def test_paid_now_above_what_the_receipt_owes_is_refused(self):
        po = self.db.one("SELECT id FROM purchase_orders WHERE status='approved' ORDER BY id LIMIT 1")["id"]
        ln = purchasing.get(self.db, po)["lines"][0]
        with self.assertRaises(ValidationError):
            purchasing.receive(self.db, po, {"date": "2026-11-01", "lines": [{"line_id": ln["id"], "qty": 1}], "paid": 10 ** 9,
                                             "method": "Cash"}, "admin")

    def test_a_sales_invoice_is_changed_from_its_sale(self):
        x = self.db.one("SELECT extract_id FROM sales_orders WHERE extract_id IS NOT NULL LIMIT 1")["extract_id"]
        st, r = self.call("POST", f"/api/extracts/{x}/reopen", {}, "boss")
        self.assertEqual(st, 400, r)
        self.assertIn("sale", r["error"])

    def test_renaming_a_contact_keeps_its_contracts_and_lines(self):
        p = self.db.one("SELECT * FROM parties WHERE name='Rapid Plumbing'")
        acc.save_party(self.db, {**p, "name": "Rapid Plumbing Co"}, p["id"])
        self.assertEqual(self.db.one("SELECT party FROM contracts WHERE code='SC-D001'")["party"], "Rapid Plumbing Co")
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM journal_lines WHERE party='Rapid Plumbing'")["n"], 0)

    def test_save_and_post_together_leave_no_draft_when_posting_fails(self):
        bu = self.db.one("SELECT id FROM business_units LIMIT 1")["id"]
        n = self.db.one("SELECT COUNT(*) n FROM extracts")["n"]
        st, r = self.call("POST", "/api/extracts", {"bu_id": bu, "number": "ZERO-1", "date": "2026-11-01", "customer": "X", "post": True,
                                                    "lines": [{"description": "nothing", "qty": 0, "price": 0}]}, "boss")
        self.assertEqual(st, 400, r)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM extracts")["n"], n)

if __name__ == "__main__":
    unittest.main()
