"""Documents that lead into each other: a purchase order raised from an approved purchase request (in parts, edited
before saving), custody given for a request or order, and payments made out of a custody from any payment screen."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from erp import accounting as acc, custody, demo, interchange, procurement as prq, purchasing as po  # noqa: E402
from erp.db import Database  # noqa: E402


def books():
    db = Database(":memory:")
    demo.load(db, "admin")
    return db


def approved_request(db, lines=None):
    bu = db.one("SELECT code FROM business_units LIMIT 1")["code"]
    rid = prq.save(db, {"date": "2026-11-03", "bu_code": bu, "lines": lines or [
        {"description": "Cement bags", "qty": 10, "unit": "bag", "est_rate": 100},
        {"description": "Sand", "qty": 5, "unit": "m3", "est_rate": 50}]}, "maker")
    prq.set_status(db, rid, "submitted")
    prq.set_status(db, rid, "approved", "boss")
    return rid


class OrderFromRequest(unittest.TestCase):
    def test_only_an_approved_request_gives_an_order(self):
        db = books()
        bu = db.one("SELECT code FROM business_units LIMIT 1")["code"]
        rid = prq.save(db, {"date": "2026-11-03", "bu_code": bu, "lines": [{"description": "Tiles", "qty": 1, "est_rate": 10}]}, "u")
        with self.assertRaisesRegex(acc.ValidationError, "approve it before ordering"):
            prq.po_draft(db, rid)
        with self.assertRaisesRegex(acc.ValidationError, "approve it before ordering"):
            po.save(db, {"supplier": "Acme", "pr_id": rid, "bu_code": bu, "lines": [{"description": "Tiles", "qty": 1, "rate": 10}]}, "u")

    def test_order_in_parts_then_everything_is_ordered(self):
        db = books()
        rid = approved_request(db)
        d = prq.po_draft(db, rid)
        self.assertEqual([(ln["description"], ln["qty"], ln["rate"]) for ln in d["lines"]], [("Cement bags", 10, 100), ("Sand", 5, 50)])
        # the buyer edits before saving: 6 bags at a better price, no sand yet
        first = po.save(db, {**d, "supplier": "Acme", "lines": [dict(d["lines"][0], qty=6, rate=95)]}, "buyer")
        r = prq.get(db, rid)
        self.assertEqual(r["status"], "ordered")
        self.assertEqual({ln["description"]: ln["left"] for ln in r["lines"]}, {"Cement bags": 4, "Sand": 5})
        self.assertEqual(po.get(db, first)["pr_code"], r["code"])
        d2 = prq.po_draft(db, rid)
        self.assertEqual([(ln["description"], ln["qty"]) for ln in d2["lines"]], [("Cement bags", 4), ("Sand", 5)])
        second = po.save(db, {**d2, "supplier": "Beta"}, "buyer")
        r = prq.get(db, rid)
        self.assertFalse(r["left_to_order"])
        self.assertEqual([o["code"] for o in r["order_list"]], [po.get(db, first)["code"], po.get(db, second)["code"]])
        with self.assertRaisesRegex(acc.ValidationError, "has been ordered"):
            prq.po_draft(db, rid)
        # cancelling an order frees its lines again; the request stays ordered while another order is live
        po.set_status(db, second, "cancelled")
        r = prq.get(db, rid)
        self.assertEqual((r["status"], r["orders"]), ("ordered", po.get(db, first)["code"]))
        self.assertEqual({ln["description"]: ln["left"] for ln in r["lines"]}, {"Cement bags": 4, "Sand": 5})
        po.set_status(db, first, "cancelled")
        self.assertEqual(prq.get(db, rid)["status"], "approved")

    def test_draft_uses_the_best_quote_and_awards_keep_the_link(self):
        db = books()
        rid = approved_request(db)
        r = prq.get(db, rid)
        ids = [ln["id"] for ln in r["lines"]]
        prq.save_quote(db, rid, {"supplier": "Cheap Co", "date": "2026-11-03", "rates": {str(ids[0]): 90, str(ids[1]): 45}})
        d = prq.po_draft(db, rid)
        self.assertEqual((d["supplier"], [ln["rate"] for ln in d["lines"]]), ("Cheap Co", [90, 45]))
        codes = prq.award(db, rid, {"strategy": "best_full"}, "boss")
        p = po.get(db, codes[0])
        self.assertEqual((p["pr_code"], all(ln["pr_line_id"] for ln in p["lines"])), (r["code"], True))
        self.assertFalse(prq.get(db, rid)["left_to_order"])

    def test_backup_keeps_the_links(self):
        db = books()
        rid = approved_request(db)
        d = prq.po_draft(db, rid)
        po.save(db, {**d, "supplier": "Acme", "lines": [dict(d["lines"][0], qty=6)]}, "buyer")
        cancelled = po.save(db, {**prq.po_draft(db, rid), "supplier": "Beta"}, "buyer")
        po.set_status(db, cancelled, "cancelled")
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(interchange.to_json(db).encode()), mode="replace")
        code = prq.get(db, rid)["code"]
        r2 = prq.get(db2, code)
        self.assertEqual({ln["description"]: ln["ordered"] for ln in r2["lines"]}, {"Cement bags": 6, "Sand": 0})
        self.assertEqual(r2["orders"], prq.get(db, rid)["orders"])


class CustodyLinks(unittest.TestCase):
    def setUp(self):
        self.db = books()
        self.rid = approved_request(self.db)
        self.pr_code = prq.get(self.db, self.rid)["code"]
        self.cid = custody.save(self.db, {"holder": "Omar Site", "amount": 1000, "for_doc": self.pr_code}, user="u")
        self.code = custody.get(self.db, self.cid)["code"]

    def test_a_payment_from_custody_comes_off_its_balance(self):
        db = self.db
        eid = acc.record_expense(db, {"date": "2026-11-03", "amount": 300, "description": "Brushes", "method": "CU:" + self.code}, "u")
        line = db.one("SELECT party, credit FROM journal_lines WHERE entry_id=? AND credit>0", (eid,))
        self.assertEqual((line["party"], line["credit"]), ("Omar Site", 30000))
        st = custody.status(db, custody.get(db, self.cid))
        self.assertEqual((st["balance"], st["paid"]), (700, 300))
        self.assertIn(eid, [r["id"] for r in custody.statement(db, self.cid)["rows"]])
        self.assertEqual(prq.get(db, self.rid)["custodies"][0]["balance"], 700)
        # the same through the paid-from account field, e.g. a supplier payment
        acc.record_payment(db, {"date": "2026-11-03", "party": "Omar Site", "amount": 50, "paid_from": "CU:" + self.code,
                                "against": db.acct("ap"), "allow_overpay": True}, "u")
        self.assertEqual(custody.status(db, custody.get(db, self.cid))["balance"], 650)
        # voiding it gives the money back to the custody
        acc.void_entry(db, eid, "wrong", "u")
        self.assertEqual(custody.status(db, custody.get(db, self.cid))["balance"], 950)

    def test_nothing_else_is_tagged_and_a_closed_custody_cannot_pay(self):
        db = self.db
        with self.assertRaises(acc.ValidationError):                       # fails after choosing the custody
            acc.record_expense(db, {"date": "2026-11-03", "amount": 20, "method": "CU:" + self.code}, "u")
        eid = acc.record_expense(db, {"date": "2026-11-03", "amount": 20, "description": "Water", "method": "Cash"}, "u")
        self.assertNotIn("paid_by_custody", acc.get_entry(db, eid)["meta"])
        other = custody.save(db, {"holder": "Mona", "booking": "none"}, user="u")
        custody.set_status(db, other, True)
        with self.assertRaisesRegex(acc.ValidationError, "closed"):
            acc.record_expense(db, {"date": "2026-11-03", "amount": 20, "description": "x",
                                    "method": "CU:" + custody.get(db, other)["code"]}, "u")

    def test_for_doc_must_exist_and_survives_a_backup(self):
        with self.assertRaisesRegex(acc.ValidationError, "not a purchase request or order"):
            custody.save(self.db, {"holder": "X", "booking": "none", "for_doc": "PR-9999"}, user="u")
        acc.record_expense(self.db, {"date": "2026-11-03", "amount": 300, "description": "Brushes", "method": "CU:" + self.code}, "u")
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(interchange.to_json(self.db).encode()), mode="replace")
        c2 = custody.get(db2, self.code)
        self.assertEqual((c2["for_doc"], custody.status(db2, c2)["balance"]), (self.pr_code, 700))


if __name__ == "__main__":
    unittest.main()
