"""Accounting corrections from the books inspection: each test is one finding, on the demo books."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import accounting as acc, assets, demo, entries, equity, wip, yearend  # noqa: E402
from erp.accounting import ValidationError  # noqa: E402
from erp.db import Database, tax_of, to_minor  # noqa: E402


class Books(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Database(":memory:")
        demo.load(cls.base, "admin")

    def setUp(self):
        self.db = Database(":memory:")
        self.base.conn.backup(self.db.conn)

    def test_vat_settlements_are_not_part_of_the_return(self):
        f = lambda: {k: v for k, v in acc.tax_summary(self.db, "2026-10-01", "2026-10-31").items()  # noqa: E731
                     if k in ("output_vat", "input_vat", "wht_withheld_from_suppliers")}
        before = f()
        bank = self.db.acct("bank")
        entries.post(self.db, "vat_pay", {"date": "2026-10-20", "amount": 5000, "credit_account": bank}, "t")
        entries.post(self.db, "vat_offset", {"date": "2026-10-31", "amount": 1000}, "t")
        entries.post(self.db, "wht_pay", {"date": "2026-10-20", "amount": 300, "credit_account": bank}, "t")
        self.assertEqual(f(), before)

    def test_disposal_depreciation_is_dated_at_the_disposal(self):
        a = self.db.q("SELECT * FROM fixed_assets ORDER BY id")[0]
        assets.dispose(self.db, a["id"], {"date": "2026-11-05", "proceeds": 1000, "method": "Cash"}, "t")
        last = self.db.one("SELECT MAX(date) d FROM journal_entries WHERE kind='Depreciation' AND ref LIKE ?", (f"FA:{a['code']}:%",))["d"]
        self.assertLessEqual(last, "2026-11-05")

    def test_catch_up_depreciation_stays_in_its_financial_year(self):
        self.db.set_setting("fiscal_year_start", "09-01")
        a = self.db.q("SELECT * FROM fixed_assets ORDER BY id")[0]
        plan = assets.plan_depreciation(self.db, "2027-02", [a["id"]])
        years = {yearend.year_of(self.db, r["to"] + "-01") for r in plan["rows"]}
        self.assertEqual(len(years), len(plan["rows"]))       # one entry per financial year
        for r in plan["rows"]:
            self.assertEqual(yearend.year_of(self.db, r["from"] + "-01"), yearend.year_of(self.db, r["to"] + "-01"))

    def test_trial_balance_after_close_opens_without_last_years_income(self):
        self.db.set_setting("fiscal_year_start", "09-01")
        yearend.close(self.db, "2026-08-31", "admin")
        tb = acc.trial_balance(self.db, "2026-09-01", "2026-11-10")
        pl = [r for r in tb["rows"] if r["type"] in ("Revenue", "COGS", "Expense")]
        self.assertEqual(round(sum(r["opening_debit"] - r["opening_credit"] for r in pl), 2), 0)
        self.assertTrue(tb["balanced"])
        self.assertEqual(round(tb["totals"]["opening_debit"] - tb["totals"]["opening_credit"], 2), 0)

    def test_closed_year_invoice_is_not_reopened_by_a_reversal(self):
        self.db.set_setting("fiscal_year_start", "09-01")
        yearend.close(self.db, "2026-08-31", "admin")
        x = self.db.q("SELECT entry_id FROM extracts WHERE status='posted' AND date<='2026-08-31'")[0]
        with self.assertRaises(ValidationError):
            acc.reverse_entry(self.db, x["entry_id"], None, "t")

    def test_profit_of_a_closed_year_can_be_shared(self):
        self.db.set_setting("fiscal_year_start", "09-01")
        yearend.close(self.db, "2026-08-31", "admin")
        r = equity.post(self.db, "appropriate", {"date_to": "2026-08-31"}, "t")
        eid = r["id"] if isinstance(r, dict) else r
        self.assertEqual(acc.get_entry(self.db, eid)["date"], "2026-09-01")

    def test_late_cost_in_a_shared_period_is_shared_next_time(self):
        equity.post(self.db, "appropriate", {"date_to": "2026-09-30"}, "t")
        acc.record_expense(self.db, {"date": "2026-09-15", "amount": 50000, "description": "late bill", "method": "Cash"}, "t")
        p = equity.PLANS["appropriate"](self.db, {"date_to": "2026-10-31"})
        self.assertEqual(p["meta"]["late"], -50000)
        self.assertTrue(any("after it was shared" in n for n in p["notes"]))

    def test_inactive_account_and_mistyped_year_are_refused(self):
        acc.save_account(self.db, {"code": "6999", "name": "Old", "type": "Expense", "subtype": "general_admin"})
        acc.save_account(self.db, {"code": "6999", "name": "Old", "type": "Expense", "subtype": "general_admin", "active": 0}, "6999")
        with self.assertRaises(ValidationError):
            acc.record_journal(self.db, {"date": "2026-11-01", "lines": [{"account": "6999", "debit": 10},
                                                                       {"account": self.db.acct("cash"), "credit": 10}]}, "t")
        for d in ("0206-11-01", "2206-11-01"):
            with self.assertRaises(ValidationError):
                acc.clean_date(d)

    def test_rounding_is_half_up_in_decimal(self):
        self.assertEqual([to_minor(x) for x in (1.005, 2.675, 0.125, 8.325)], [101, 268, 13, 833])
        self.assertEqual(to_minor("90071992547409.93"), 9007199254740993)
        self.assertEqual((tax_of(475, 0.14), tax_of(-475, 0.14), tax_of(2075, 0.14)), (67, -67, 291))

    def test_asset_cost_is_locked_once_its_purchase_is_booked(self):
        a = next(x for x in self.db.q("SELECT * FROM fixed_assets") if self.db.one(
            "SELECT 1 FROM journal_entries WHERE ref=? AND status='posted'", (f"FA:{x['code']}",)))
        d = {k: a[k] for k in ("code", "name", "account", "accum_account", "expense_account", "acquired", "life_months")}
        with self.assertRaises(ValidationError):
            assets.save(self.db, {**d, "cost": 99999}, a["id"])

    def test_cash_account_keeps_its_line_once_used(self):
        row = self.db.one("SELECT * FROM accounts WHERE code=?", (self.db.acct("bank"),))
        with self.assertRaises(ValidationError):
            acc.save_account(self.db, {**row, "subtype": "prepaid"}, row["code"])

    def test_reversal_cannot_predate_its_entry(self):
        eid = acc.record_expense(self.db, {"date": "2026-11-02", "amount": 100, "description": "x", "method": "Cash"}, "t")
        with self.assertRaises(ValidationError):
            acc.reverse_entry(self.db, eid, "2026-11-01", "t")


class WorkInProgressByMonth(unittest.TestCase):
    def test_earlier_months_keep_their_entries(self):
        db = Database(":memory:")
        db.x("INSERT INTO business_units(code,name,basis,fee_pct,wip_mode) VALUES ('CP','Cost plus','Cost-Plus %',0.25,'cost_plus')")
        cash = db.acct("cash")
        acc.record_journal(db, {"date": "2026-09-10", "bu_code": "CP", "lines": [{"account": "5010", "debit": 1000}, {"account": cash, "credit": 1000}]})
        acc.record_journal(db, {"date": "2026-10-10", "bu_code": "CP", "lines": [{"account": "5010", "debit": 400}, {"account": cash, "credit": 400}]})
        rows = db.q("SELECT e.date, l.debit FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id "
                    "WHERE e.ref=? AND e.status='posted' AND l.account=? ORDER BY e.date", (wip.ref("CP"), db.acct("unbilled_revenue")))
        self.assertEqual([(r["date"][:7], r["debit"]) for r in rows], [("2026-09", 125000), ("2026-10", 50000)])


if __name__ == "__main__":
    unittest.main()
