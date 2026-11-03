"""Entry models (built-in and the company's own, several lines, last used first, deletable) and saved fill-ins for
forms: through the server, in the workbook and JSON backups, and in the industry templates."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from erp import accounting as acc, auth, entries, industries, interchange, presets, server  # noqa: E402
from erp.db import Database  # noqa: E402

try:
    import openpyxl  # noqa: F401
    HAVE_XL = True
except ImportError:
    HAVE_XL = False


class App:
    pass


def session(db, admin=True):
    auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
    clerk = auth.create_user(db, {"username": "clerk", "password": "pw123456"})
    app = App()
    app.db, app.backup = db, (lambda *a, **k: None)
    tok, _ = auth.login(db, "boss", "pw123456")
    tok_clerk, _ = auth.login(db, "clerk", "pw123456")

    def call(m, p, b=None, tok=tok):
        r = server.handle(app, m, p, {"x-session": tok, "x-requested-with": "BeyondERP", "content-type": "application/json"},
                          json.dumps(b or {}).encode())
        return r.status, json.loads(r.body or b"{}")
    return call, tok_clerk, clerk


class Models(unittest.TestCase):
    def test_work_on_credit_owes_the_supplier_and_costs_the_project(self):
        db = Database(":memory:")
        bu = db.conn.execute("INSERT INTO business_units(code,name) VALUES ('P1','Villa')").lastrowid
        with self.assertRaisesRegex(acc.ValidationError, "party"):
            entries.post(db, "work_on_credit", {"date": "2026-11-03", "amount": 5000, "debit_account": db.acct("materials"), "bu_id": bu})
        with self.assertRaisesRegex(acc.ValidationError, "business unit"):
            entries.post(db, "work_on_credit", {"date": "2026-11-03", "amount": 5000, "debit_account": db.acct("materials"), "party": "Acme"})
        eid = entries.post(db, "work_on_credit", {"date": "2026-11-03", "amount": 5000, "debit_account": db.acct("materials"),
                                                  "party": "Acme", "bu_id": bu})
        self.assertEqual(acc.party_balance(db, db.acct("ap"), "Acme"), 5000)        # owed, nothing paid
        e = acc.get_entry(db, eid)
        self.assertEqual((e["kind"], e["bu_id"]), ("Direct Cost", bu))

    def test_save_use_delete_through_the_server(self):
        db = Database(":memory:")
        call, tok_clerk, clerk = session(db)
        st, r = call("POST", "/api/entry-models", {"name": "Cement on credit", "keep_amounts": True, "keep_details": True,
                                                    "memo": "Cement", "party": "Acme",
                                                    "lines": [{"account": "5010", "side": "debit", "amount": 1000},
                                                              {"account": "1320", "side": "debit", "amount": 140},
                                                              {"account": "2010", "side": "credit", "amount": 1140}]})
        self.assertEqual(st, 200, r)
        mid = r["model"]["id"]
        self.assertEqual(r["entry_types"][0]["id"], mid)                               # newest on top
        # the Settings table saves only names and sides: the model keeps its three lines
        types = [{k: t[k] for k in ("id", "name", "name_ar", "kind", "debit", "credit", "party", "bu", "active")} for t in r["entry_types"]]
        self.assertEqual(call("PUT", "/api/entry-types", {"types": types})[0], 200)
        m = next(t for t in entries.load(db) if t["id"] == mid)
        self.assertEqual((len(m["lines"]), m["memo"], m["party_name"]), (3, "Cement", "Acme"))
        # using a model brings it to the top for everyone
        st, _ = call("POST", "/api/entry/interest", {"date": "2026-11-03", "amount": 10, "credit_account": db.acct("bank")})
        self.assertEqual(st, 200)
        self.assertEqual(entries.for_client(db)[0]["id"], "interest")
        st, _ = call("POST", "/api/journal", {"date": "2026-11-03", "template": mid, "lines": [
            {"account": "5010", "debit": 1000}, {"account": "1320", "debit": 140}, {"account": "2010", "credit": 1140, "party": "Acme"}]})
        self.assertEqual(st, 200)
        self.assertEqual(entries.for_client(db)[0]["id"], mid)
        # a clerk without journal rights can neither save nor delete
        db.x("UPDATE users SET perms=? WHERE id=?", (json.dumps({"accounting.journal": "view"}), clerk))
        self.assertEqual(call("POST", "/api/entry-models", {"name": "x", "lines": []}, tok=tok_clerk)[0], 403)
        self.assertEqual(call("DELETE", "/api/entry-models/" + mid, tok=tok_clerk)[0], 403)
        st, r = call("DELETE", "/api/entry-models/" + mid)
        self.assertEqual(st, 200)
        self.assertNotIn(mid, [t["id"] for t in r["entry_types"]])

    def test_a_model_must_balance_sides(self):
        db = Database(":memory:")
        with self.assertRaisesRegex(acc.ValidationError, "debit line and a credit line"):
            entries.add_model(db, {"name": "Bad", "lines": [{"account": "5010", "side": "debit"}, {"account": "6090", "side": "debit"}]})
        with self.assertRaisesRegex(acc.ValidationError, "Unknown account"):
            entries.add_model(db, {"name": "Bad", "lines": [{"account": "9999", "side": "debit"}, {"account": "2010", "side": "credit"}]})


class FillIns(unittest.TestCase):
    def test_save_use_delete_and_permissions(self):
        db = Database(":memory:")
        call, tok_clerk, clerk = session(db)
        st, r = call("POST", "/api/presets", {"key": "doc:expense", "name": "Office rent",
                                              "values": {"amount": "5000", "description": "Rent", "date": "2026-01-01", "_files": "x"}})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["preset"]["values"], {"amount": "5000", "description": "Rent"})   # no date, no files
        st, r = call("POST", "/api/presets", {"key": "doc:expense", "name": "office RENT", "values": {"amount": "5500"}})
        self.assertEqual(len(r["presets"]["doc:expense"]), 1)                                # same name replaces
        pid = r["preset"]["id"]
        self.assertEqual(call("POST", "/api/presets/use", {"key": "doc:expense", "id": pid})[0], 200)
        self.assertEqual(call("POST", "/api/presets", {"key": "doc:nope", "name": "x", "values": {"a": "1"}})[0], 400)
        db.x("UPDATE users SET perms=? WHERE id=?", (json.dumps({"purchasing.expenses": "view"}), clerk))
        self.assertEqual(call("POST", "/api/presets", {"key": "doc:expense", "name": "x", "values": {"a": "1"}}, tok=tok_clerk)[0], 403)
        st, r = call("DELETE", f"/api/presets/{pid}?key=doc:expense")
        self.assertEqual((st, r["presets"].get("doc:expense")), (200, None))

    def test_invoices_and_contracts_follow_their_own_permissions(self):
        db = Database(":memory:")
        call, tok_clerk, clerk = session(db)
        lines = [{"item_no": str(i), "description": f"Work item {i}", "unit": "m2", "qty": i, "price": 12.5} for i in range(1, 81)]
        st, r = call("POST", "/api/presets", {"key": "invoice:new", "name": "Monthly maintenance",
                                              "values": {"customer": "Acme", "vat_code": "VAT14", "fees": '{"*": "5%"}', "lines": lines}})
        self.assertEqual(st, 200, r)
        self.assertEqual(len(r["preset"]["values"]["lines"]), 80)                           # a long invoice fits
        st, r = call("POST", "/api/presets", {"key": "contract:new", "name": "Standard sub",
                                              "values": {"kind": "sub", "party": "Beta", "retention_pct": "5",
                                                         "lines": [{"description": "Plastering", "unit": "m2", "rate": "40"}]}})
        self.assertEqual(st, 200, r)
        db.x("UPDATE users SET perms=? WHERE id=?", (json.dumps({"sales.invoices": "view", "contracts.contracts": "view"}), clerk))
        for key in ("invoice:new", "contract:new"):
            self.assertEqual(call("POST", "/api/presets", {"key": key, "name": "x", "values": {"a": "1"}}, tok=tok_clerk)[0], 403)
        db.x("UPDATE users SET perms=? WHERE id=?", (json.dumps({"sales.invoices": "add"}), clerk))
        self.assertEqual(call("POST", "/api/presets", {"key": "invoice:new", "name": "x", "values": {"a": "1"}}, tok=tok_clerk)[0], 200)
        self.assertEqual(call("POST", "/api/presets", {"key": "contract:new", "name": "x", "values": {"a": "1"}}, tok=tok_clerk)[0], 403)

    def test_order_lines_are_kept(self):
        db = Database(":memory:")
        p = presets.save(db, "po:new", "Diesel", {"supplier": "Fuel Co", "lines": [{"kind": "expense", "description": "Diesel", "qty": "100"}]})
        self.assertEqual(p["values"]["lines"][0]["description"], "Diesel")


class Carried(unittest.TestCase):
    def books(self):
        db = Database(":memory:")
        entries.add_model(db, {"name": "Cement on credit", "keep_amounts": True, "lines": [
            {"account": "5010", "side": "debit", "amount": 1000}, {"account": "2010", "side": "credit"}]})
        entries.delete_model(db, "interest")
        presets.save(db, "doc:expense", "Office rent", {"amount": "5000"})
        return db

    def check(self, db2):
        ids = [t["id"] for t in entries.load(db2)]
        self.assertIn("m_cement_on_credit", ids)
        self.assertNotIn("interest", ids)                                       # removed stays removed
        self.assertEqual(presets.load(db2)["doc:expense"][0]["values"], {"amount": "5000"})

    def test_json_backup(self):
        db2 = Database(":memory:")
        interchange.import_data(db2, interchange.read_json(interchange.to_json(self.books()).encode()), mode="replace")
        self.check(db2)

    @unittest.skipUnless(HAVE_XL, "openpyxl not installed")
    def test_workbook_both_languages(self):
        from erp import workbook
        for lang in ("en", "ar"):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "w.xlsx"
                p.write_bytes(workbook.build_workbook(self.books(), lang=lang))
                data = interchange.read_xlsx(p)
            self.assertFalse(any(r.get("key") in ("entry_types", "form_presets") for r in data["Settings"]))
            db2 = Database(":memory:")
            interchange.import_data(db2, data, mode="replace")
            self.check(db2)


class Industries(unittest.TestCase):
    def test_every_industry_applies_with_its_models(self):
        self.assertEqual(len(industries.ORDER), 13)
        for k in industries.ORDER:
            db = Database(":memory:")
            r = industries.apply(db, k)
            for mid in r["models_added"]:
                m = next(t for t in entries.load(db) if t["id"] == mid)
                specs = [ln["account"] for ln in m.get("lines", [])] or [m["debit"], m["credit"]]
                for sp in specs:                                                # every fixed account exists in these books
                    if ":" not in sp:
                        self.assertTrue(db.one("SELECT 1 FROM accounts WHERE code=?", (sp,)), (k, mid, sp))
            self.assertEqual(industries.apply(db, k)["models_added"], [])        # applying again adds nothing twice
        cat = {c["key"]: c for c in industries.catalogue()}
        for k in ("restaurant", "services", "manufacturing", "logistics"):
            self.assertTrue(cat[k]["name_ar"] and cat[k]["guide_ar"] and cat[k]["bu_ar"], k)

    def test_a_clashing_account_never_gets_a_model(self):
        db = Database(":memory:")
        acc.save_account(db, {"code": "5011", "name": "Something else entirely", "type": "COGS", "subtype": "cogs"})
        r = industries.apply(db, "restaurant")
        self.assertNotIn("ind_food_on_credit", r["models_added"])            # 5011 means something else here


if __name__ == "__main__":
    unittest.main()
