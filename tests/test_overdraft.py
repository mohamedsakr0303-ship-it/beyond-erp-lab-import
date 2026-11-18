"""A bank account with an overdraft facility (erp/overdraft.py) — SWAG's Crédit Agricole current account, which goes below
zero when the bank lends:

* the setting belongs to a cash / bank account only; limit and rate are checked;
* below zero it is a bank overdraft under current liabilities (the balance sheet still balances); above zero, cash;
* the books' checks stop asking where the money came from (it came from the bank) and flag only going over the limit;
  a bank without a facility is still flagged;
* the unused part of the limit counts as money available (money position, cash forecast);
* the interest estimate is day by day at the rate; the bank's interest is booked once per month and account;
* import / export carry it, and the Excel workbook's balance sheet shows the same overdraft as the app.
"""
import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from erp import accounting as acc, cashplan, interchange, overdraft, workbook  # noqa: E402
from erp.db import Database  # noqa: E402

try:
    import openpyxl  # noqa: F401
    HAVE_XL = True
except ImportError:
    HAVE_XL = False


def books(limit=3000000, rate=24):
    db = Database(":memory:")
    acc.save_account(db, {"code": "1025", "name": "Crédit Agricole — current", "name_ar": "كريدي أجريكول — جاري", "type": "Asset",
                          "subtype": "cash", "od_on": 1, "od_limit": limit, "od_rate": rate})
    acc.record_journal(db, {"date": "2026-04-01", "lines": [{"account": "1025", "debit": 100000}, {"account": "3010", "credit": 100000}]}, user="t")
    acc.record_journal(db, {"date": "2026-04-10", "lines": [{"account": "2010", "debit": 2350000, "party": "S"},
                                                            {"account": "1025", "credit": 2350000}]}, user="t")
    return db


class Overdraft(unittest.TestCase):
    def test_setting_belongs_to_a_bank_account(self):
        db = books()
        a = db.one("SELECT od_on, od_limit, od_rate FROM accounts WHERE code='1025'")
        self.assertEqual((a["od_on"], a["od_limit"], a["od_rate"]), (1, 300000000, 24.0))
        acc.save_account(db, {"code": "1025", "name": "Crédit Agricole — current", "type": "Asset", "subtype": "cash"}, old_code="1025")
        self.assertEqual(db.one("SELECT od_on FROM accounts WHERE code='1025'")["od_on"], 1)          # left out: kept
        acc.save_account(db, {"code": "6999", "name": "Not a bank", "type": "Expense", "subtype": "general_admin", "od_on": 1, "od_limit": 5})
        self.assertEqual(db.one("SELECT od_on FROM accounts WHERE code='6999'")["od_on"], 0)
        for bad in ({"od_limit": -1}, {"od_rate": 150}):
            with self.subTest(bad=bad), self.assertRaises(acc.ValidationError):
                acc.save_account(db, {"code": "1026", "name": "X", "type": "Asset", "subtype": "cash", "od_on": 1, **bad})

    def test_balance_sheet_shows_a_bank_overdraft(self):
        db = books()
        bs = acc.balance_sheet(db, "2026-04-30")
        od = [x for x in bs["current_liabilities"] if x.get("overdraft")]
        self.assertEqual([(x["code"], x["amount"]) for x in od], [("1025", 2250000.0)])
        self.assertFalse([x for x in bs["current_assets"] if x["code"] == "1025"])
        self.assertTrue(bs["balanced"])
        from erp import reports
        t = reports.balance_sheet_table(db, "2026-04-30", lang="ar")
        self.assertTrue(any("سحب على المكشوف — كريدي أجريكول — جاري" in str(r["cells"][0]) for r in t["rows"]))
        bs0 = acc.balance_sheet(db, "2026-04-05")                                                   # above zero: cash
        self.assertEqual([x["amount"] for x in bs0["current_assets"] if x["code"] == "1025"], [100000.0])

    def test_books_checks(self):
        db = books()
        texts = lambda: [f["flag"] for f in acc.review_flags(db, date_to="2026-04-30")]  # noqa: E731
        self.assertFalse([t for t in texts() if "Crédit Agricole" in t])                           # within the facility
        db.x("UPDATE accounts SET od_limit=100000000 WHERE code='1025'")                           # limit 1,000,000
        self.assertTrue([t for t in texts() if "over its overdraft limit" in t])
        db.x("UPDATE accounts SET od_on=0 WHERE code='1025'")                                      # no facility: as before
        self.assertTrue([t for t in texts() if "went below zero" in t and "Crédit Agricole" in t])

    def test_room_counts_as_money(self):
        db = books()
        self.assertEqual(acc.cash_position(db)["overdraft_room"], 750000.0)
        cp = cashplan.build(db, days=30, today="2026-04-30")
        self.assertEqual(cp["overdrafts"]["room"], 750000.0)
        self.assertEqual(cp["cash_now"], 750000.0)            # what can be spent: −2,250,000 in the bank + its 3,000,000 limit
        self.assertEqual(acc.cash_position(db)["overdraft_limits"], 3000000.0)
        db.x("UPDATE accounts SET od_limit=0 WHERE code='1025'")                                   # limit not recorded: no room
        self.assertEqual(overdraft.facilities(db, "2026-04-30")[0]["room"], None)

    def test_interest_estimate_and_booking(self):
        db = books()
        est = overdraft.interest_estimate(db, "1025", "2026-04-01", "2026-04-30")
        self.assertEqual(est, round(225000000 * 0.24 / 365 * 21))                                  # 21 days at −2,250,000
        f = overdraft.facilities(db, "2026-04-30")[0]
        self.assertEqual((f["interest_est"], f["interest_booked"], f["peak"], f["peak_on"]), (31068.49, False, 2250000.0, "2026-04-10"))
        eid = overdraft.book_interest(db, {"account": "1025", "month": "2026-04", "amount": 31000}, "t")
        e = acc.get_entry(db, eid)
        self.assertEqual((e["date"], e["kind"], [(x["account"], x["debit"], x["credit"]) for x in e["lines"]]),
                         ("2026-04-30", "Bank Interest", [("7030", 31000.0, 0.0), ("1025", 0.0, 31000.0)]))
        self.assertTrue(overdraft.facilities(db, "2026-04-30")[0]["interest_booked"])
        with self.assertRaisesRegex(acc.ValidationError, "already booked"):
            overdraft.book_interest(db, {"account": "1025", "month": "2026-04", "amount": 1}, "t")
        with self.assertRaisesRegex(acc.ValidationError, "overdraft facility"):
            overdraft.book_interest(db, {"account": "1020", "month": "2026-04", "amount": 1}, "t")

    def test_import_export_round_trip(self):
        db = books()
        data = interchange.export_data(db)
        row = [r for r in data["Chart of Accounts"] if r["code"] == "1025"][0]
        self.assertEqual((row["od_on"], row["od_limit"], row["od_rate"]), ("Yes", 3000000.0, 24.0))
        db2 = Database(":memory:")
        interchange.import_data(db2, copy.deepcopy(data), mode="replace")
        self.assertEqual(overdraft.facilities(db2, "2026-04-30"), overdraft.facilities(db, "2026-04-30"))

    @unittest.skipUnless(HAVE_XL and shutil.which("soffice"), "LibreOffice not installed")
    def test_excel_balance_sheet_matches(self):
        import subprocess
        import openpyxl
        db = books()
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "b.xlsx"
            src.write_bytes(workbook.build_workbook(db))
            subprocess.run(["soffice", "--headless", "--convert-to", "xlsx", "--outdir", str(Path(d) / "out"), str(src)],
                           capture_output=True, timeout=600)
            wb = openpyxl.load_workbook(Path(d) / "out" / "b.xlsx", data_only=True)
        rows = {str(r[0]).split(" / ")[0]: r[1] for r in wb["Balance Sheet"].iter_rows(values_only=True) if r[0]}
        bs = acc.balance_sheet(db)
        self.assertAlmostEqual(rows["Bank overdrafts"], 2250000.0, places=2)
        self.assertAlmostEqual(rows["TOTAL ASSETS"], bs["total_assets"], places=2)
        self.assertAlmostEqual(rows["TOTAL LIABILITIES"], bs["total_liabilities"], places=2)
        self.assertAlmostEqual(rows["Check: assets − (liabilities + equity)"] or 0, 0, places=2)


if __name__ == "__main__":
    unittest.main()
