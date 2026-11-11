"""Payroll, leave and custody: the checks found by the payroll inspection (each test is one of its findings)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import accounting as acc, custody, demo, hrrequests, payroll as P  # noqa: E402
from erp.accounting import ValidationError  # noqa: E402
from erp.db import Database, to_major  # noqa: E402


def _bal(db, role, party):
    code = db.one("SELECT code FROM accounts WHERE role=?", (role,))["code"]
    return to_major(db.one("SELECT COALESCE(SUM(l.debit-l.credit),0) b FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                           "WHERE e.status='posted' AND l.account=? AND l.party=?", (code, party))["b"])


class PayrollChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Database(":memory:")
        demo.load(cls.base, "admin")

    def setUp(self):
        self.db = Database(":memory:")
        self.base.conn.backup(self.db.conn)

    def test_daily_worker_without_attendance_is_not_paid_a_month(self):
        eid = P.save_employee(self.db, {"name": "New Daily", "emp_type": "daily", "daily_rate": 500,
                                        "cost_type": "Administrative Salaries (Shared Overhead)"})
        rid = P.create_run(self.db, {"period": "2026-12"})
        ln = next(x for x in P.get_run(self.db, rid)["lines"] if x["employee_id"] == eid)
        self.assertEqual((ln["units"], ln["result"]["gross"]), (0, 0))

    def test_period_and_date_are_checked(self):
        for bad in ("2026-13", "abcd-ef"):
            with self.assertRaises(ValidationError):
                P.create_run(self.db, {"period": bad})
        with self.assertRaises(ValidationError):
            P.create_run(self.db, {"period": "2026-12", "date": "2027-01-15"})

    def test_joiner_and_leaver_are_prorated(self):
        j = P.save_employee(self.db, {"name": "Joiner", "emp_type": "monthly", "basic": 9000, "hire_date": "2026-12-25",
                                      "insured": "No", "taxed": "No"})
        lv = P.save_employee(self.db, {"name": "Leaver", "emp_type": "monthly", "basic": 9000, "hire_date": "2020-01-01",
                                       "end_date": "2026-12-20", "insured": "No", "taxed": "No"})
        rid = P.create_run(self.db, {"period": "2026-12"})
        g = {x["employee_id"]: x["result"]["gross"] for x in P.get_run(self.db, rid)["lines"]}
        self.assertEqual(g[j], 9000 * 6 / 30)          # 25–31 December: 7 calendar days, 24 outside → 6 of 30
        self.assertEqual(g[lv], 9000 * 19 / 30)         # paid up to the 20th: 11 days outside

    def test_advance_is_recovered_once_across_two_draft_runs(self):
        e = P.get_employee(self.db, "EMP-D04")
        start = _bal(self.db, "employee_advances", e["name"])
        r1 = P.create_run(self.db, {"period": "2026-12"})
        r2 = P.create_run(self.db, {"period": "2027-01"})
        P.post_run(self.db, r1)
        P.post_run(self.db, r2)
        self.assertGreaterEqual(_bal(self.db, "employee_advances", e["name"]), 0)
        self.assertLessEqual(_bal(self.db, "employee_advances", e["name"]), start)

    def test_rename_keeps_the_advance_and_pay_owed(self):
        e = P.get_employee(self.db, "EMP-D04")
        before = _bal(self.db, "employee_advances", e["name"])
        P.save_employee(self.db, {**{k: v for k, v in e.items() if k != "username"}, "name": "Sophie Turner-Hill"}, e["id"])
        self.assertEqual(_bal(self.db, "employee_advances", "Sophie Turner-Hill"), before)
        self.assertEqual(_bal(self.db, "employee_advances", e["name"]), 0)

    def test_posted_run_entries_cannot_be_voided_one_by_one(self):
        rid = P.create_run(self.db, {"period": "2026-12"})
        P.post_run(self.db, rid)
        eid = self.db.one("SELECT entry_id FROM payroll_lines WHERE run_id=? AND entry_id IS NOT NULL", (rid,))["entry_id"]
        with self.assertRaises(ValidationError):
            acc.void_entry(self.db, eid, "x")
        P.unpost_run(self.db, rid)                       # unposting the whole run still works
        self.assertEqual(self.db.one("SELECT status FROM journal_entries WHERE id=?", (eid,))["status"], "void")

    def test_unpost_refused_once_the_pay_is_paid_out(self):
        rid = P.create_run(self.db, {"period": "2026-12"})
        P.post_run(self.db, rid)
        name = "Sophie Turner"
        owed = -_bal(self.db, "accrued_payroll", name)
        accrued = self.db.one("SELECT code FROM accounts WHERE role='accrued_payroll'")["code"]
        cash = self.db.one("SELECT code FROM accounts WHERE subtype='cash' ORDER BY code LIMIT 1")["code"]
        acc._insert_entry(self.db, date="2026-12-31", kind="Payment Voucher", memo="salary", party=name,
                          lines=[{"account": accrued, "debit": owed, "party": name}, {"account": cash, "credit": owed}])
        with self.assertRaises(ValidationError):
            P.unpost_run(self.db, rid)

    def test_crew_sheet_paid_once_and_locked(self):
        cid = P.save_crew(self.db, {"date": "2026-12-01", "crew": "Crew A", "lines": [{"item": "Painting", "count": 10, "rate": 30}]})
        P.pay_crew(self.db, cid, {"method": "Cash"})
        with self.assertRaises(ValidationError):
            P.pay_crew(self.db, cid, {"method": "Cash"})
        with self.assertRaises(ValidationError):
            P.save_crew(self.db, {"date": "2026-12-01", "crew": "Crew B", "lines": []}, cid)

    def test_numeric_code_is_not_taken_for_an_id(self):
        eid = P.save_employee(self.db, {"code": "1", "name": "Num Code", "emp_type": "monthly", "basic": 5000})
        self.assertEqual(P.get_employee(self.db, "1")["id"], eid)

    def test_insurance_label_follows_the_rate(self):
        P.save_settings(self.db, {"si_emp_rate": 0.12})
        c = P.compute(P.get_employee(self.db, "EMP-D04"), {}, P.settings(self.db))
        self.assertIn("12 %", next(d["name"] for d in c["deductions"] if d["code"] == "si_emp"))

    def test_arabic_payslip_uses_arabic_name_and_currency(self):
        self.db.x("UPDATE employees SET name_ar='صوفي تيرنر' WHERE code='EMP-D04'")
        rid = P.create_run(self.db, {"period": "2026-12"})
        h = P.payslips_html(self.db, rid, "EMP-D04", "ar")
        self.assertIn("صوفي تيرنر", h)
        self.assertNotIn(">EGP<", h.replace(" ", ""))
        self.assertIn("صوفي تيرنر", "".join(r["label"] for r in P.register_table(self.db, rid, "ar")["rows"]))

    def test_sheet_import_posts_only_its_rows(self):
        P.create_run(self.db, {"period": "2026-12"})       # a draft with every employee
        P.run_sheet_import(self.db, [(5, {"date": "2026-12-31", "employee": "EMP-D03", "units": 10})])
        rid = self.db.one("SELECT id FROM payroll_runs WHERE period='2026-12'")["id"]
        self.assertEqual([x["code"] for x in P.get_run(self.db, rid)["lines"]], ["EMP-D03"])


class LeaveAndCustody(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        demo.load(self.db, "admin")

    def test_rejecting_leave_puts_the_days_back(self):
        e = P.get_employee(self.db, "EMP-D04")
        self.db.x("DELETE FROM attendance WHERE employee_id=? AND date BETWEEN '2026-12-01' AND '2026-12-03'", (e["id"],))
        for d in ("2026-12-01", "2026-12-02"):
            self.db.x("INSERT INTO attendance(date, employee_id, status, hours) VALUES (?,?,'present',8)", (d, e["id"]))
        rid = self.db.x("INSERT INTO hr_requests(username, employee_id, kind, date_from, date_to, status, created_at) "
                        "VALUES ('someone', ?, 'annual', '2026-12-01', '2026-12-03', 'pending', '2026-11-01')", (e["id"],)).lastrowid
        boss = {"username": "admin", "is_super": True}
        hrrequests.decide(self.db, boss, rid, True)
        hrrequests.decide(self.db, boss, rid, False)
        rows = {r["date"]: (r["status"], r["hours"]) for r in
                self.db.q("SELECT date, status, hours FROM attendance WHERE employee_id=? AND date BETWEEN '2026-12-01' AND '2026-12-03'", (e["id"],))}
        self.assertEqual(rows.get("2026-12-01"), ("present", 8))
        self.assertEqual(rows.get("2026-12-02"), ("present", 8))
        self.assertNotIn("2026-12-03", rows)            # a day the leave added is removed again

    def test_custody_edit_keeps_fields_it_does_not_send(self):
        c = custody.get(self.db, "CU-D01")
        custody.save(self.db, {"holder": c["holder"]}, c["id"])
        after = custody.get(self.db, "CU-D01")
        self.assertEqual((after["due_date"], after["purpose"]), (c["due_date"], c["purpose"]))


if __name__ == "__main__":
    unittest.main()
