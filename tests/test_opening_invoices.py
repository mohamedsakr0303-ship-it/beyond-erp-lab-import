"""Opening balances brought in invoice by invoice: a journal line can stand for an earlier customer invoice / supplier bill
(its number, date and due date). The voucher stays on its own date (the books' start); what changes is how old the
balance is and when it falls due:

* aging and the customer's statement count from the invoice's own date; payments settle the oldest invoice first;
* the overdue-customers alert follows (it reads the aging);
* the cash forecast places what is still open on its due date and no longer counts it as undated;
* the voucher form, import and export carry the three fields; a round trip keeps them;
* nonsense is refused (an invoice dated after the voucher, a due date before the invoice).
"""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from erp import accounting as acc, automations, cashplan, interchange, statements, workbook  # noqa: E402
from erp.db import Database  # noqa: E402

OPENING = {"date": "2027-01-01", "memo": "Opening balances", "lines": [
    {"account": "1100", "debit": 100000, "party": "Client A", "doc_ref": "INV-7", "doc_date": "2026-06-01", "due_date": "2026-07-01"},
    {"account": "1100", "debit": 20000, "party": "Client A", "doc_ref": "INV-9", "doc_date": "2026-12-10", "due_date": "2027-02-10"},
    {"account": "1100", "debit": 5000, "party": "Client B"},                                   # a plain balance: as before
    {"account": "2010", "credit": 30000, "party": "Supplier X", "doc_ref": "B-1", "doc_date": "2026-11-20", "due_date": "2027-01-20"},
    {"account": "3010", "credit": 95000}]}


def books():
    db = Database(":memory:")
    acc.record_journal(db, copy.deepcopy(OPENING), user="t")
    return db


class OpeningInvoices(unittest.TestCase):
    def test_aging_counts_from_each_invoice(self):
        db = books()
        rows = {r["party"]: r for r in statements.aging(db, date_to="2027-01-15")["rows"]}
        self.assertEqual(rows["Client A"]["aging"], [0.0, 20000.0, 0.0, 0.0, 100000.0])   # 36 days and 228 days old
        self.assertEqual(rows["Client B"]["aging"], [5000.0, 0.0, 0.0, 0.0, 0.0])          # no invoice date: the voucher's
        self.assertEqual(rows["Supplier X"]["aging"], [0.0, -30000.0, 0.0, 0.0, 0.0])

    def test_payment_settles_the_oldest_invoice(self):
        db = books()
        acc.record_journal(db, {"date": "2027-01-10", "lines": [{"account": "1020", "debit": 100000},
                                                                {"account": "1100", "credit": 100000, "party": "Client A"}]}, user="t")
        a = [r for r in statements.aging(db, date_to="2027-01-15")["rows"] if r["party"] == "Client A"][0]
        self.assertEqual(a["aging"], [0.0, 20000.0, 0.0, 0.0, 0.0])
        left = [x for x in statements.open_items(db, date_to="2027-01-15") if x["party"] == "Client A"]
        self.assertEqual([(x["ref"], x["date"], x["due"], x["amount"]) for x in left], [("INV-9", "2026-12-10", "2027-02-10", 20000.0)])

    def test_statement_shows_each_invoice(self):
        db = books()
        st = statements.statement(db, "Client A", date_to="2027-01-15")
        self.assertEqual([(r["doc_ref"], r["doc_date"], r["due_date"], r["debit"], r["balance"]) for r in st["rows"]],
                         [("INV-7", "2026-06-01", "2026-07-01", 100000.0, 100000.0), ("INV-9", "2026-12-10", "2027-02-10", 20000.0, 120000.0)])
        self.assertTrue(all(r["date"] == "2027-01-01" for r in st["rows"]))                 # still posted on the voucher's day
        from erp import reports
        t = reports.party_statement_table(db, "Client A", None, "2027-01-15", "en")
        self.assertTrue(any("invoice INV-7 · 2026-06-01 · due 2026-07-01" in str(r["cells"][3]) for r in t["rows"]))

    def test_overdue_alert_sees_the_old_invoice(self):
        import datetime as dt
        today = dt.date.fromisoformat(acc.today())
        db = Database(":memory:")                     # (the alert reads today's aging: books that start today)
        acc.record_journal(db, {"date": today.isoformat(), "lines": [
            {"account": "1100", "debit": 1000, "party": "Client A", "doc_ref": "OLD", "doc_date": (today - dt.timedelta(days=200)).isoformat()},
            {"account": "1100", "debit": 1000, "party": "Client B"}, {"account": "3010", "credit": 2000}]}, user="t")
        out = list(automations._t_receivable_overdue(db, {"days": 180, "min_amount": 0}))
        self.assertTrue(any("Client A" in x["text"] for x in out), out)
        self.assertFalse(any("Client B" in x["text"] for x in out), out)

    def test_cash_forecast_places_due_dates(self):
        db = books()
        cp = cashplan.build(db, days=90, today="2027-01-15")
        got = sorted((i["date"], i["amount"], i["label"]) for i in cp["items"] + cp["overdue"]["rows"] if i["source"] == "invoices")
        self.assertEqual(got, [("2026-07-01", 100000.0, "Client A — INV-7"), ("2027-01-20", -30000.0, "Supplier X — B-1"),
                               ("2027-02-10", 20000.0, "Client A — INV-9")])
        self.assertEqual(cp["overdue"]["in"], 100000.0)                                  # INV-7 was due in July
        und = {u["key"]: u["amount"] for u in cp["undated"]}
        self.assertEqual((und["receivables"], und["payables"]), (5000.0, 0.0))            # only Client B's plain balance

    def test_nonsense_is_refused(self):
        db = books()
        for bad, msg in (({"doc_date": "2027-02-01"}, "after the voucher"), ({"doc_date": "2026-05-01", "due_date": "2026-04-01"}, "before the invoice"),
                         ({"doc_date": "soon"}, "invoice date")):
            with self.subTest(bad=bad), self.assertRaisesRegex(acc.ValidationError, msg):
                acc.record_journal(db, {"date": "2027-01-01", "lines": [{"account": "1100", "debit": 5, "party": "Z", **bad},
                                                                         {"account": "3010", "credit": 5}]}, user="t")

    def test_entry_keeps_the_fields_and_editing_keeps_them(self):
        db = books()
        e = acc.get_entry(db, 1)
        self.assertEqual({k: e["lines"][0][k] for k in ("doc_ref", "doc_date", "due_date")},
                         {"doc_ref": "INV-7", "doc_date": "2026-06-01", "due_date": "2026-07-01"})
        self.assertNotIn("doc_ref", e["lines"][2])
        self.assertEqual(acc.get_entries(db, [1])[0]["lines"], e["lines"])

    def test_import_export_round_trip(self):
        db = books()
        data = interchange.export_data(db)
        jv = [r for r in data["Journal Vouchers"] if r.get("doc_ref")]
        self.assertEqual(sorted((r["doc_ref"], r["doc_date"], r["due_date"]) for r in jv),
                         [("B-1", "2026-11-20", "2027-01-20"), ("INV-7", "2026-06-01", "2026-07-01"), ("INV-9", "2026-12-10", "2027-02-10")])
        db2 = Database(":memory:")
        interchange.import_data(db2, copy.deepcopy(data), mode="replace")
        self.assertEqual(statements.aging(db2, date_to="2027-01-15")["rows"], statements.aging(db, date_to="2027-01-15")["rows"])
        self.assertEqual(statements.open_items(db2, date_to="2027-01-15"), statements.open_items(db, date_to="2027-01-15"))

    def test_excel_workbook_round_trip(self):
        db = books()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "b.xlsx"
            path.write_bytes(workbook.build_workbook(db))
            db2 = Database(":memory:")
            r = interchange.import_data(db2, interchange.read_xlsx(path), mode="new")
            self.assertFalse(r.get("errors"), r.get("errors"))
        self.assertEqual(statements.open_items(db2, date_to="2027-01-15"), statements.open_items(db, date_to="2027-01-15"))

    def test_plain_books_unchanged(self):
        """Lines without the fields age exactly as before: by their voucher's date, newest charges left open."""
        db = Database(":memory:")
        for d, amt in (("2026-01-10", 300), ("2026-05-01", 200), ("2026-08-01", 100)):
            acc.record_journal(db, {"date": d, "lines": [{"account": "1100", "debit": amt, "party": "C"}, {"account": "4010", "credit": amt}]}, user="t")
        acc.record_journal(db, {"date": "2026-09-01", "lines": [{"account": "1020", "debit": 350}, {"account": "1100", "credit": 350, "party": "C"}]}, user="t")
        a = statements.aging(db, date_to="2026-09-15")["rows"][0]
        self.assertEqual(a["aging"], [0.0, 100.0, 0.0, 150.0, 0.0])


if __name__ == "__main__":
    unittest.main()
