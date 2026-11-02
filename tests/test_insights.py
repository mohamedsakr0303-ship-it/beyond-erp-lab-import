"""Insights (erp/insights.py): the figures match the reports, every change splits exactly into volume and value, the
month's pace and target, the deal funnel, capacity in use, and only what a person may see."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from erp import accounting as acc  # noqa: E402
from erp import demo, insights  # noqa: E402
from erp.db import Database  # noqa: E402

ALL = lambda m, lvl="view": True  # noqa: E731


def screens(*granted):
    """can() as the server has it: 'a|b' is either; a section counts as granted when any of its screens is."""
    def can(key, lvl="view"):
        return any(k in granted or (("." not in k) and any(g.startswith(k + ".") for g in granted)) for k in key.split("|"))
    return can


class Insights(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Database(":memory:")
        demo.load(cls.db, "admin")

    def test_measures_match_the_income_statement(self):
        f, t = "2026-01-01", acc.today()
        inc = acc.income_statement(self.db, f, t)
        rev = insights.totals(self.db, "revenue", f, t)[""][0]
        self.assertEqual(rev, inc["net_sales"])                               # operating revenue, other income apart
        cost = insights.totals(self.db, "costs", f, t)[""][0]                 # every cost, financing included
        other = acc.to_major(self.db.one("""SELECT SUM(l.credit-l.debit) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
            JOIN accounts a ON a.code=l.account WHERE e.status='posted' AND a.subtype='other_income' AND e.date>=? AND e.date<=?""", (f, t))["n"] or 0)
        self.assertAlmostEqual(rev + other - cost - inc["income_tax"], inc["net_profit"], places=1)

    def test_every_change_splits_exactly_into_volume_and_value(self):
        for m in insights.MEASURES:
            for d in insights.MEASURES[m][5]:
                br = insights.bridge(self.db, m, d, ("2026-09-01", "2026-09-30"), ("2026-10-01", "2026-10-31"))
                self.assertAlmostEqual(br["volume_effect"] + br["value_effect"], br["change"], places=1, msg=f"{m} by {d}")
                for r in br["rows"]:
                    self.assertAlmostEqual(r["volume_effect"] + r["value_effect"], r["change"], places=1)
                self.assertAlmostEqual(br["after"] - br["before"], br["change"], places=1)
        # the arithmetic itself: 2 documents of 100 → 3 of 120
        self.assertEqual(insights._split((200.0, 2), (360.0, 3)), (160.0, 100.0, 60.0))
        self.assertEqual(insights._split((0.0, 0), (50.0, 1)), (50.0, 50.0, 0.0))       # new: all volume
        self.assertEqual(insights._split((80.0, 2), (0.0, 0)), (-80.0, -80.0, 0.0))     # stopped: all volume

    def test_collections_count_cash_that_came_in(self):
        rows = self.db.q("""SELECT e.kind FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                            WHERE e.status='posted' AND a.subtype='cash' AND e.kind IN ('Receipt Voucher','Cheque Cleared','Cheque Bounced')""")
        self.assertTrue(rows)
        for m in insights.monthly(self.db, "collections", 12):
            self.assertGreaterEqual(m["value"], 0, m)                            # a cheque received is collected when it clears
        f, to = "2026-01-01", acc.today()
        kinds = {r["kind"] for r in self.db.q("""SELECT DISTINCT e.kind FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
            JOIN accounts a ON a.code=l.account WHERE e.status='posted' AND """ + insights.MEASURES["collections"][2])}
        self.assertIn("E-Receipt", kinds)                                           # cash sales at the counter
        self.assertTrue(kinds.isdisjoint({"Cheque Received", "Rental Deposit", "Loan Received", "Capital Contribution", "Partner Loan",
                                           "Direct Cost", "Payment Voucher"}), kinds)   # a supplier paid with a saving booked as revenue is not one

    def test_pace_and_target(self):
        p = insights.pace(self.db, "revenue", today="2026-10-10")
        self.assertEqual((p["day"], p["days"], p["days_left"], p["month"]), (10, 31, 21, "2026-10"))
        self.assertAlmostEqual(p["projected"], round(p["so_far"] / 10 * 31, 2), places=1)
        self.assertEqual(p["target_source"], "budget")                            # the demo budgets revenue every month
        budget = sum(json.loads(r["months"])[9] for r in self.db.q(
            "SELECT b.months FROM budgets b JOIN accounts a ON a.code=b.account WHERE b.year=2026 AND a.type='Revenue'")) / 100
        self.assertEqual(p["target"], budget)
        self.assertAlmostEqual(p["need_per_day"], round((budget - p["so_far"]) / 21, 2), places=1)
        q = insights.pace(self.db, "revenue", target=1000, today="2026-10-10")
        self.assertEqual((q["target"], q["target_source"]), (1000.0, "set"))
        self.assertTrue(insights.pace(self.db, "costs", today="2026-10-03")["early"])
        part = insights.pace(self.db, "revenue", today="2026-10-10", scope=insights.MODULES["rentals"]["scope"])
        self.assertIsNone(part["target"])                                           # the company budget is not rentals' target
        self.assertTrue(insights.pace(self.db, "costs")["lower_is_better"])

    def test_trend_words(self):
        self.assertEqual(insights.trend([100, 110, 121, 133, 146])["direction"], "up")
        self.assertEqual(insights.trend([100, 90, 81, 73])["direction"], "down")
        self.assertEqual(insights.trend([100, 130, 95, 128, 99, 131, 100])["direction"], "swings")
        self.assertEqual(insights.trend([100, 101, 100, 101])["direction"], "flat")
        self.assertEqual(insights.trend([0, 0, 5])["direction"], "short")

    def test_funnel(self):
        db = Database(":memory:")
        for stage, src, why in [("lead", "Web", ""), ("qualified", "Web", ""), ("proposal", "Referral", ""), ("won", "Referral", ""),
                                ("won", "Web", ""), ("lost", "Web", "Price"), ("lost", "Referral", "Price"), ("lost", "Web", "Timing")]:
            db.x("INSERT INTO crm_deals(title, stage, value, source, owner, lost_reason, created_at) VALUES ('d', ?, 10000, ?, 'Sam', ?, '2026-10-01')",
                 (stage, src, why))
        f = insights.funnel(db)
        self.assertEqual((f["deals"], f["won"], f["lost"], f["open"], f["win_rate"]), (8, 2, 3, 3, 0.4))
        self.assertEqual([s["count"] for s in f["stages"]], [5, 4, 3, 2, 2])       # at or past each stage
        self.assertEqual(f["lost_reasons"][0], {"reason": "Price", "count": 2})
        self.assertEqual(f["won_value"], 200.0)                                     # stored in minor units: 2 × 100.00
        self.assertEqual({r["group"]: r["win_rate"] for r in f["by_source"]}, {"Web": round(1 / 3, 4), "Referral": 0.5})

    def test_capacity(self):
        c = insights.capacity_rentals(self.db)
        self.assertTrue(c["total"] > 0 and 0 <= c["share"] <= 1)
        self.assertEqual(c["used"], sum(g["used"] for g in c["groups"]))
        self.assertTrue(all(g["group"] and g["group_ar"] for g in c["groups"]))   # names, not internal codes
        p = insights.capacity_people(self.db, "2026-10-15")
        self.assertEqual(p["total"], self.db.one("SELECT COUNT(*) n FROM employees WHERE status='active'")["n"])

    def test_each_part_shows_only_what_the_person_may_see(self):
        sales_only = screens("sales.invoices")
        pg = insights.page(self.db, "sales", sales_only)
        self.assertEqual([m["key"] for m in pg["modules"]], ["sales"])
        self.assertEqual({m["measure"] for m in pg["measures"]}, {"revenue"})       # collections need receipts or receivables
        self.assertEqual({m["measure"] for m in insights.page(self.db, "sales", screens("sales.invoices", "sales.receipts"))["measures"]},
                         {"revenue", "collections"})
        with self.assertRaises(PermissionError):
            insights.page(self.db, "business", sales_only)
        with self.assertRaises(PermissionError):
            insights.page(self.db, "purchasing", sales_only)
        quotes_only = screens("sales.offers")                 # the "sales" section counts as granted, but not invoices
        with self.assertRaises(PermissionError):
            insights.page(self.db, "sales", quotes_only)
        self.assertNotIn("sales", [m for m in insights.MODULES if insights.allowed(quotes_only, m)])
        rentals_only = screens("rentals.contracts")
        self.assertEqual({m["measure"] for m in insights.page(self.db, "rentals", rentals_only)["measures"]}, {"revenue"})
        with self.assertRaises(PermissionError):
            insights.page(self.db, "business", rentals_only)
        full = insights.page(self.db, "business", ALL)
        self.assertIn("profit", full)
        rentals = insights.page(self.db, "rentals", ALL)
        self.assertIn("capacity", rentals)
        # rentals' revenue: only what rental entries earned
        f, t = "2026-01-01", acc.today()
        mine = insights.totals(self.db, "revenue", f, t, scope=insights.MODULES["rentals"]["scope"]).get("", (0, 0))[0]
        direct = acc.to_major(self.db.one("""SELECT SUM(l.credit-l.debit) n FROM journal_lines l JOIN accounts a ON a.code=l.account
            WHERE a.code IN ('4045','4046','4047')""")["n"] or 0)            # the demo's rental, installation and transport revenue
        self.assertEqual(mine, direct)
        self.assertGreater(mine, 0)


class InsightCards(unittest.TestCase):
    def test_dashboard_cards(self):
        from erp import dashboards as D
        db = Database(":memory:")
        demo.load(db, "admin")
        c = D.Ctx(db, {"username": "admin"}, {}, ALL)
        for w in ("kpi_target", "month_pace", "why_changed", "deal_funnel", "capacity"):
            self.assertIn(w, D.WIDGETS)
            D.WIDGETS[w]["fn"](c, {})
        r = D.WIDGETS["why_changed"]["fn"](c, {"measure": "costs", "split": "account"})
        self.assertEqual((r["measure"], r["dim"], r["lower_is_better"]), ("costs", "account", True))
        no = D.Ctx(db, {"username": "x"}, {}, screens("crm.deals", "sales.offers"))
        with self.assertRaises(PermissionError):
            D.WIDGETS["kpi_target"]["fn"](no, {"measure": "revenue"})


class PeopleCost(unittest.TestCase):
    def test_payroll_and_daily_labour_not_other_costs(self):
        db = Database(":memory:")
        demo.load(db, "admin")
        y = ("2026-01-01", "2026-12-31")
        run = acc.to_major(db.one("""SELECT SUM(l.debit-l.credit) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
            WHERE e.status='posted' AND e.kind='Payroll Accrual' AND a.type IN ('COGS','Expense')""")["n"])
        labour = acc.to_major(db.one("""SELECT SUM(l.debit-l.credit) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
            WHERE e.status='posted' AND e.kind<>'Payroll Accrual' AND l.account='5030'""")["n"])        # daily labour by voucher
        self.assertGreater(labour, 0)
        self.assertEqual(insights.totals(db, "payroll", *y)[""][0], round(run + labour, 2))
        # a non-staff cost booked to an account the payroll also uses (project indirect costs) is not people cost
        before = insights.totals(db, "payroll", *y)[""][0]
        acc._insert_entry(db, date="2026-10-15", kind="Operating Expense", memo="Site toilets hire", lines=[
            {"account": "6070", "debit": 500}, {"account": "1020", "credit": 500}])
        self.assertEqual(insights.totals(db, "payroll", *y)[""][0], before)


class OtherBusinesses(unittest.TestCase):
    def test_a_clinic(self):
        db = Database(":memory:")
        demo.load(db, "admin", industry="healthcare")
        kinds = {r["kind"] for r in db.q("""SELECT DISTINCT e.kind FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
            JOIN accounts a ON a.code=l.account WHERE e.status='posted' AND """ + insights.MEASURES["collections"][2])}
        self.assertTrue({"Medical Invoice", "Insurance Claim"} <= kinds, kinds)    # paid at the visit; insurers settling claims
        f, to = "2026-01-01", acc.today()
        self.assertEqual(insights.totals(db, "revenue", f, to)[""][0], acc.income_statement(db, f, to)["net_sales"])
        db.set_setting("hidden_modules", json.dumps(["rentals", "equipment"]))
        tabs = [m["key"] for m in insights.page(db, "business", ALL)["modules"]]
        self.assertNotIn("rentals", tabs)
        self.assertNotIn("equipment", tabs)
        with self.assertRaises(PermissionError):
            insights.page(db, "rentals", ALL)


class CorrectionsAndEdges(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        demo.load(self.db, "admin")

    def test_a_reversal_is_not_another_document(self):
        e = self.db.one("""SELECT e.id, e.date FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id JOIN accounts a ON a.code=l.account
                           WHERE e.status='posted' AND a.type='Revenue' AND e.reversal_of IS NULL AND e.reversed_by IS NULL
                           GROUP BY e.id HAVING SUM(l.credit - l.debit) > 0 ORDER BY e.date DESC LIMIT 1""")   # a sale, not a return
        m = e["date"][:7]
        f, t = m + "-01", insights._month_end(m)
        before = insights.totals(self.db, "revenue", f, t)[""]
        acc.reverse_entry(self.db, e["id"], date=e["date"])
        after = insights.totals(self.db, "revenue", f, t)[""]
        self.assertEqual(after[1], before[1])                                       # same number of documents
        self.assertLess(after[0], before[0])                                        # the amount is corrected

    def test_lift_and_drag_follow_lower_is_better(self):
        br = insights.bridge(self.db, "costs", "unit", ("2026-09-01", "2026-09-30"), ("2026-10-01", "2026-10-31"))
        rows = {r["group"]: r["change"] for r in br["rows"]}
        if br["lift"]:
            self.assertLess(rows[br["lift"]], 0)                                    # for a cost, a lift is a fall
        if br["drag"]:
            self.assertGreater(rows[br["drag"]], 0)
        for h in insights.months_table(self.db, "costs", "unit", 4, "2026-10"):
            if h["lift"]:
                self.assertLess(h["lift"]["change"], 0)

    def test_bad_values_in_the_address_are_refused_or_ignored(self):
        pg = insights.page(self.db, "business", ALL, {"month": "x'; drop", "vs": "2026-13"})
        self.assertRegex(pg["month"], r"^\d{4}-\d{2}$")
        with self.assertRaises(acc.ValidationError):
            insights.page(self.db, "business", ALL, {"bu_id": "1 or 1=1"})

    def test_equipment_out_on_a_site_is_in_use(self):
        self.db.x("UPDATE equipment SET status='available'")
        self.db.x("UPDATE equipment SET status='in_use' WHERE id=(SELECT MIN(id) FROM equipment)")
        c = insights.capacity_equipment(self.db, "2026-12-15")                    # a month with no hours booked
        self.assertEqual(c["used"], 1)


if __name__ == "__main__":
    unittest.main()
