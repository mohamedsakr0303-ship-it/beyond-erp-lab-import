"""Groups of the chart of accounts (the tree): headings that total the accounts under them and are never posted to.

Every place that touches the chart is checked here: the rules, posting, the screens' API and permissions, the reports,
import and export (whole books, one sheet, CSV, the Excel workbook in both languages, a database backup), erase, the demo,
the shared workspace and books made by older versions.
"""
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import accounting as acc, auth, cloud, demo, interchange, reports, server, workbook  # noqa: E402
from erp.db import Database  # noqa: E402


class App:
    pass


def tree(db):
    acc.save_group(db, {"code": "1", "name": "Assets", "name_ar": "الأصول"})
    acc.save_group(db, {"code": "12", "name": "Cash and banks", "name_ar": "النقدية والبنوك", "type": "Asset", "parent": "1"})
    acc.save_group(db, {"code": "3", "name": "Expenses"})
    for c in [r["code"] for r in db.q("SELECT code FROM accounts WHERE subtype='cash'")]:
        a = db.one("SELECT * FROM accounts WHERE code=?", (c,))
        acc.save_account(db, {**a, "parent": "12"}, old_code=c)


class Rules(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        tree(self.db)

    def test_an_account_follows_its_groups_type(self):
        with self.assertRaises(acc.ValidationError):
            acc.save_account(self.db, {"code": "9999", "name": "Rent", "type": "Expense", "subtype": "general_admin", "parent": "12"})
        acc.save_account(self.db, {"code": "9999", "name": "Rent", "type": "Expense", "subtype": "general_admin", "parent": "3"})
        with self.assertRaises(acc.ValidationError):        # unknown group
            acc.save_account(self.db, {"code": "9998", "name": "X", "type": "Expense", "subtype": "general_admin", "parent": "77"})

    def test_no_loops_and_no_shared_codes(self):
        acc.save_group(self.db, {"code": "121", "name": "Banks", "type": "Asset", "parent": "12"})
        with self.assertRaises(acc.ValidationError):
            acc.save_group(self.db, {"code": "12", "name": "Cash and banks", "type": "Asset", "parent": "121"}, old_code="12")
        with self.assertRaises(acc.ValidationError):
            acc.save_group(self.db, {"code": "1010", "name": "Same as an account"})
        with self.assertRaises(acc.ValidationError):
            acc.save_account(self.db, {"code": "12", "name": "Same as a group", "type": "Asset", "subtype": "cash"})
        with self.assertRaises(acc.ValidationError):        # a liability group under a group holding assets
            acc.save_group(self.db, {"code": "122", "name": "Loans", "type": "Liability", "parent": "12"})

    def test_a_group_cannot_take_a_type_its_accounts_do_not_have(self):
        with self.assertRaises(acc.ValidationError):
            acc.save_group(self.db, {"code": "1", "name": "Assets", "type": "Liability"}, old_code="1")
        acc.save_group(self.db, {"code": "1", "name": "Assets", "type": "Asset"}, old_code="1")

    def test_renaming_a_group_moves_what_is_under_it(self):
        acc.save_group(self.db, {"code": "12A", "name": "Cash and banks", "type": "Asset", "parent": "1"}, old_code="12")
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM accounts WHERE parent='12'")["n"], 0)
        self.assertGreater(self.db.one("SELECT COUNT(*) n FROM accounts WHERE parent='12A'")["n"], 0)

    def test_a_group_with_something_under_it_is_not_deleted(self):
        with self.assertRaises(acc.ValidationError):
            acc.delete_group(self.db, "12")
        with self.assertRaises(acc.ValidationError):
            acc.delete_group(self.db, "1")
        acc.save_group(self.db, {"code": "4", "name": "Empty"})
        acc.delete_group(self.db, "4")

    def test_a_group_is_never_posted_to(self):
        with self.assertRaises(acc.ValidationError):
            acc.record_journal(self.db, {"date": "2026-10-01", "lines": [{"account": "12", "debit": 10}, {"account": self.db.acct("cash"), "credit": 10}]})

    def test_callers_that_do_not_know_groups_never_move_an_account(self):
        c = self.db.acct("cash")
        a = self.db.one("SELECT * FROM accounts WHERE code=?", (c,))
        acc.save_account(self.db, {k: a[k] for k in ("code", "name", "name_ar", "type", "subtype")}, old_code=c)   # no "parent" key
        self.assertEqual(self.db.one("SELECT parent FROM accounts WHERE code=?", (c,))["parent"], "12")

    def test_the_tree_totals_what_is_under_each_group(self):
        cash = self.db.acct("cash")
        acc.record_journal(self.db, {"date": "2026-10-01", "lines": [{"account": cash, "debit": 250}, {"account": self.db.acct("capital"), "credit": 250}]})
        net = {r["code"]: r["net"] for r in acc.account_totals(self.db)}
        t = {g["code"]: g for g in acc.chart_tree(self.db, net)}
        self.assertEqual(t["12"]["total"], 25000)
        self.assertEqual(t["1"]["total"], 25000)             # rolled up to the top
        self.assertEqual((t["1"]["depth"], t["12"]["depth"]), (0, 1))
        self.assertEqual(sorted(acc.group_accounts(self.db, "1")), sorted(r["code"] for r in self.db.q("SELECT code FROM accounts WHERE parent='12'")))


class Files(unittest.TestCase):
    """Whole books, one sheet, CSV and the workbook carry the tree; an older file without it changes nothing."""

    @classmethod
    def setUpClass(cls):
        cls.db = Database(":memory:")
        demo.load(cls.db, "admin")
        auth.create_user(cls.db, {"username": "boss", "password": "pw123456"}, first=True)   # a backup holds its people
        tree(cls.db)
        acc.save_group(cls.db, {"code": "121", "name": "Banks", "type": "Asset", "parent": "12"})   # a third level, empty

    def check(self, db):
        self.assertEqual({(g["code"], g["parent"], g["type"]) for g in db.q("SELECT * FROM account_groups")},
                         {(g["code"], g["parent"], g["type"]) for g in self.db.q("SELECT * FROM account_groups")})
        want = {r["code"]: r["parent"] for r in self.db.q("SELECT code, parent FROM accounts")}
        got = {r["code"]: r["parent"] for r in db.q("SELECT code, parent FROM accounts")}
        self.assertEqual({k: got.get(k) for k in want}, want)

    def test_json_backup(self):
        db = Database(":memory:")
        interchange.import_data(db, interchange.read_json(interchange.to_json(self.db).encode()))
        self.check(db)

    def test_workbook_in_both_languages(self):
        for lang in ("en", "ar"):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "b.xlsx"
                p.write_bytes(workbook.build_workbook(self.db, template=False, lang=lang))
                sheets = interchange.read_xlsx(str(p))
                sheets = sheets[0] if isinstance(sheets, tuple) else sheets
                db = Database(":memory:")
                interchange.import_data(db, sheets)
                self.check(db)

    def test_template_keeps_the_tree(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.xlsx"
            p.write_bytes(workbook.build_workbook(self.db, template=True, lang="en"))
            sheets = interchange.read_xlsx(str(p))
            sheets = sheets[0] if isinstance(sheets, tuple) else sheets
            self.assertTrue(sheets.get("Account Groups"))

    def test_database_backup_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.sqlite3"
            self.db.backup_to(p)
            db = Database(":memory:")
            interchange.import_data(db, interchange.read_sqlite(p.read_bytes()))
            self.check(db)

    def test_csv_of_one_sheet(self):
        text = interchange.to_csv(self.db, "Account Groups")
        text = text.decode("utf-8-sig") if isinstance(text, bytes) else text
        self.assertIn("Cash and banks", text)
        db = Database(":memory:")
        interchange.import_data(db, interchange.read_csv("Account Groups", text.encode("utf-8")))
        self.assertEqual(db.one("SELECT parent FROM account_groups WHERE code='12'")["parent"], "1")

    def test_children_listed_before_parents_still_import(self):
        rows = list(reversed(interchange.export_data(self.db)["Account Groups"]))
        db = Database(":memory:")
        interchange.import_data(db, {"Account Groups": rows})
        self.assertEqual(db.one("SELECT parent FROM account_groups WHERE code='121'")["parent"], "12")

    def test_an_older_file_without_groups_moves_nothing(self):
        db = Database(":memory:")
        interchange.import_data(db, interchange.read_json(interchange.to_json(self.db).encode()))
        old = [{k: v for k, v in r.items() if k != "parent"} for r in interchange.export_data(db)["Chart of Accounts"]]
        interchange.import_data(db, {"Chart of Accounts": old})
        self.check(db)


class Screens(unittest.TestCase):
    """The API, permissions (whole books and the chart screen), reports and what the forms are offered."""

    @classmethod
    def setUpClass(cls):
        cls.db = db = Database(":memory:")
        demo.load(db, "admin")
        tree(db)
        auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        auth.create_user(db, {"username": "viewer", "password": "pw123456", "perms": {"accounting.coa": "view"}})
        auth.create_user(db, {"username": "sales", "password": "pw123456", "perms": {"sales": "edit"}})
        code = db.one("SELECT code FROM business_units ORDER BY id")["code"]
        auth.create_user(db, {"username": "site", "password": "pw123456", "projects": [code], "perms": {"accounting": "edit"}})
        cls.app = App()
        cls.app.db, cls.app.backup, cls.app.backups = db, (lambda *a, **k: None), (lambda: [])
        cls.tok = {u: auth.login(db, u, "pw123456")[0] for u in ("boss", "viewer", "sales", "site")}

    def call(self, who, m, p, b=None, raw=None):
        r = server.handle(self.app, m, p, {"x-session": self.tok[who], "x-requested-with": "BeyondERP",
                                           "content-type": "text/csv" if raw is not None else "application/json"},
                          raw if raw is not None else json.dumps(b or {}).encode())
        return r.status, (json.loads(r.body) if r.body and r.body[:1] in (b"{", b"[") else r.body)

    def test_permissions(self):
        self.assertEqual(self.call("viewer", "GET", "/api/account-groups")[0], 200)
        self.assertEqual(self.call("viewer", "POST", "/api/account-groups", {"code": "5", "name": "X"})[0], 403)
        self.assertEqual(self.call("sales", "GET", "/api/account-groups")[0], 403)
        st, r = self.call("boss", "POST", "/api/account-groups", {"code": "5", "name": "Liabilities", "type": "Liability"})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.call("boss", "DELETE", "/api/account-groups/5")[0], 200)

    def test_project_limited_people_see_no_company_totals(self):
        st, rows = self.call("site", "GET", "/api/account-groups")
        self.assertEqual(st, 200, rows)
        self.assertTrue(all(r["total"] is None for r in rows))
        st, rows = self.call("boss", "GET", "/api/account-groups")
        self.assertTrue(any(r["total"] for r in rows))

    def test_forms_are_never_offered_a_group(self):
        st, b = self.call("boss", "GET", "/api/bootstrap")
        groups = {g["code"] for g in b["account_groups"]}
        self.assertTrue(groups)
        self.assertFalse(groups & {a["code"] for a in b["accounts"]})
        st, accounts = self.call("boss", "GET", "/api/accounts")
        self.assertFalse(groups & {a["code"] for a in accounts})

    def test_reports_by_group(self):
        full = reports.build(self.db, "trial_balance", {})
        for level in ("1", "2", "3"):
            t = reports.build(self.db, "trial_balance", {"level": level})
            self.assertEqual(t["rows"][-2]["cells"][1:], full["rows"][-2]["cells"][1:])      # same totals at every level
        t = reports.build(self.db, "trial_balance", {"level": "2", "date_from": "2026-01-01"})
        self.assertTrue(any(r["style"] == "subtotal" for r in t["rows"]))
        gl = acc.general_ledger(self.db, group="1", limit=100000)
        cash = set(acc.group_accounts(self.db, "1"))
        self.assertTrue(gl["rows"] and all(r["account"] in cash for r in gl["rows"]))
        st, r = self.call("boss", "GET", "/api/reports/general_ledger?group=12")
        self.assertEqual(st, 200, r)

    def test_the_chart_screen_exports_the_chart(self):
        t = reports.build(self.db, "chart_of_accounts", {"lang": "en"})
        tb = reports.build(self.db, "trial_balance", {})
        self.assertEqual(t["rows"][-1]["cells"][3:], tb["rows"][-2]["cells"][1:])          # same totals as the trial balance
        groups = [r["cells"][0] for r in t["rows"] if r["style"] in ("section", "subtotal") and r["cells"][0]]
        self.assertEqual(sorted(groups), sorted(g["code"] for g in self.db.q("SELECT code FROM account_groups")))
        listed = [r["cells"][0] for r in t["rows"] if r["style"] == "item"]
        self.assertEqual(sorted(listed), sorted(a["code"] for a in self.db.q("SELECT code FROM accounts WHERE active=1")))
        self.assertIn(f"{len(groups)} groups", t["subtitle"])
        # each account sits under its own group: the nearest group row above it is its parent
        parent = {a["code"]: a["parent"] for a in self.db.q("SELECT code, parent FROM accounts")}
        stack = []
        for r in t["rows"]:
            if r["style"] in ("section", "subtotal"):
                stack = stack[:r["indent"]] + [r["cells"][0]]
            elif r["style"] == "item" and parent.get(r["cells"][0]):
                self.assertEqual(stack[r["indent"] - 1], parent[r["cells"][0]], r["cells"])
        for fmt in ("xlsx", "pdf", "csv", "docx"):
            st, body = self.call("viewer", "GET", f"/api/reports/chart_of_accounts?format={fmt}&lang=ar")
            self.assertEqual(st, 200, (fmt, body[:200] if isinstance(body, bytes) else body))
        self.assertEqual(self.call("sales", "GET", "/api/reports/chart_of_accounts")[0], 403)
        self.assertEqual(self.call("site", "GET", "/api/reports/chart_of_accounts")[0], 403)    # company-wide balances

    def test_one_sheet_import_follows_the_chart_screen(self):
        csv = "Group Code,Group Name\n7,Seven\n".encode()
        st, r = self.call("sales", "POST", "/api/import-sheet?sheet=Account%20Groups&filename=g.csv", raw=csv)
        self.assertEqual(st, 403)
        st, r = self.call("boss", "POST", "/api/import-sheet?sheet=Account%20Groups&filename=g.csv", raw=csv)
        self.assertEqual(st, 200, r)
        self.assertTrue(self.db.one("SELECT 1 FROM account_groups WHERE code='7'"))

    def test_whole_books_export_needs_its_permission_and_carries_the_tree(self):
        self.assertEqual(self.call("viewer", "GET", "/api/export/json")[0], 403)
        st, body = self.call("boss", "GET", "/api/export/json")
        self.assertEqual(st, 200)
        data = json.loads(body) if isinstance(body, (bytes, bytearray)) else body
        self.assertTrue(data["sheets"]["Account Groups"])

    def test_the_audit_log_names_it(self):
        from erp import auditlabels
        self.assertEqual(auditlabels.label("account_group.add", "ar"), "إضافة مجموعة حسابات")


class Lifecycle(unittest.TestCase):
    def test_books_from_an_older_version_gain_the_tree(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "old.sqlite3"
            Database(p).close()
            c = sqlite3.connect(p)          # make it look like a book from before the tree
            c.execute("DROP TABLE account_groups")
            c.execute("CREATE TABLE a2 AS SELECT code,name,name_ar,type,subtype,normal,role,active FROM accounts")
            c.execute("DROP TABLE accounts")
            c.execute("ALTER TABLE a2 RENAME TO accounts")
            c.commit()
            c.close()
            db = Database(p)
            self.assertIn("parent", [r["name"] for r in db.q("PRAGMA table_info(accounts)")])
            acc.save_group(db, {"code": "1", "name": "Assets"})
            db.close()

    def test_erase_all_starts_a_new_tree(self):
        db = Database(":memory:")
        tree(db)
        server._erase(db, "all")
        self.assertEqual(db.one("SELECT COUNT(*) n FROM account_groups")["n"], 0)

    def test_a_tree_alone_does_not_make_the_books_look_used(self):
        db = Database(":memory:")
        tree(db)
        self.assertTrue(demo.is_empty(db))

    def test_the_shared_workspace_carries_every_change(self):
        for m, p in (("POST", "/api/account-groups"), ("PUT", "/api/account-groups/12"), ("DELETE", "/api/account-groups/12")):
            self.assertTrue(cloud.replicated(m, p), p)
        db = Database(":memory:")
        tree(db)
        import base64
        import gzip
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "s.db"
            f.write_bytes(gzip.decompress(base64.b64decode(cloud.snapshot_native(db))))
            c = sqlite3.connect(f)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM account_groups").fetchone()[0], 3)
            c.close()


if __name__ == "__main__":
    unittest.main()
