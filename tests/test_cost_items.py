"""Cost items: catalogue, tagging cost lines (with their own descriptions), budget against actual, import / export."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import accounting as acc, costitems, interchange  # noqa: E402
from erp.accounting import ValidationError  # noqa: E402
from erp.db import Database  # noqa: E402


def book():
    db = Database(":memory:")
    db.x("INSERT INTO business_units(code,name) VALUES ('P1','Tower'),('P2','Villa')")
    costitems.load_standard(db)
    costitems.save(db, {"code": "03.01", "name": "Rebar", "name_ar": "حديد التسليح", "keywords": "حديد تسليح"})
    return db


def pay(db, bu, lines, memo="site payments"):
    cash = db.acct("cash")
    total = sum(x[1] for x in lines)
    return acc.record_journal(db, {"date": "2026-10-05", "bu_code": bu, "memo": memo,
                                   "lines": [{"account": a, "debit": amt, "memo": m, **({"cost_item": it} if it else {})} for a, amt, m, it in
                                             [(x[0], x[1], x[2], x[3] if len(x) > 3 else "") for x in lines]]
                                   + [{"account": cash, "credit": total}]})


class CostItems(unittest.TestCase):
    def test_lines_keep_their_description_and_item(self):
        db = book()
        eid = pay(db, "P1", [("5010", 5000, "توريد طن حديد", "03.01"), ("5030", 2000, "نقاش الدور الثاني")])
        lines = acc.get_entry(db, eid)["lines"]
        self.assertEqual((lines[0]["memo"], lines[0]["cost_item"]), ("توريد طن حديد", "03.01"))
        self.assertNotIn("cost_item", lines[1])
        with self.assertRaises(ValidationError):
            pay(db, "P1", [("5010", 10, "x", "99.99")])

    def test_suggestions_read_each_lines_own_description(self):
        db = book()
        pay(db, "P1", [("5010", 5000, "توريد طن حديد للأعمدة"), ("5030", 2000, "مصطفى النقاش دهانات")])
        s = {x["memo"]: x["suggested"] for x in costitems.suggest(db)}
        self.assertEqual(s["توريد طن حديد للأعمدة"], "03.01")      # the deeper item wins
        self.assertEqual(s["مصطفى النقاش دهانات"], "07")
        self.assertEqual(costitems.classify(db, "رسوم حكومية"), "")   # nothing matches: no guess

    def test_report_rolls_up_and_compares_with_budget(self):
        db = book()
        pay(db, "P1", [("5010", 5000, "حديد", "03.01"), ("5020", 1000, "صب سقف", "03"), ("5030", 700, "يومية عمال")])
        pay(db, "P2", [("5010", 900, "حديد", "03.01")])
        costitems.save_budget(db, "P1", "03", 8000)
        r = costitems.report(db, "P1")
        row = {x["code"]: x for x in r["rows"]}
        self.assertEqual((row["03"]["actual"], row["03.01"]["actual"], row["03"]["budget"], row["03"]["left"]), (6000, 5000, 8000, 2000))
        self.assertEqual((r["total"], r["untagged"]), (6700, 700))
        self.assertEqual(costitems.report(db)["total"], 7600)          # all projects

    def test_tagging_and_catalogue_rules(self):
        db = book()
        pay(db, "P1", [("5030", 700, "يومية عمال")])
        line = costitems.suggest(db)[0]["line_id"]
        self.assertEqual(costitems.tag_lines(db, [{"line_id": line, "item": "04"}]), 1)
        with self.assertRaises(ValidationError):
            costitems.delete(db, "04")                                  # tagged: deactivate instead
        with self.assertRaises(ValidationError):
            costitems.save(db, {"code": "77.01", "name": "orphan"})     # its trade does not exist
        costitems.save(db, {"code": "14", "name": "Site works"}, old_code="04")   # renaming moves the tags
        self.assertEqual(db.one("SELECT cost_item FROM journal_lines WHERE id=?", (line,))["cost_item"], "14")

    def test_journal_vouchers_and_catalogue_go_out_and_back(self):
        db = book()
        pay(db, "P1", [("5010", 5000, "توريد طن حديد", "03.01")])
        costitems.save_budget(db, "P1", "03.01", 12000)
        out = interchange.export_data(db)
        self.assertTrue(any(r.get("cost_item") == "03.01" and r.get("line_memo") == "توريد طن حديد" for r in out["Journal Vouchers"]))
        db2 = Database(":memory:")
        interchange.import_data(db2, {k: out[k] for k in ("Business Units", "Cost Items", "Cost Budgets", "Journal Vouchers")})
        self.assertEqual(costitems.report(db2, "P1")["rows"][-1], costitems.report(db, "P1")["rows"][-1])


class OldBooks(unittest.TestCase):
    def test_a_book_from_before_line_parties_opens(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "old.db")
            c = sqlite3.connect(p)
            c.executescript("CREATE TABLE journal_lines (id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL, account TEXT NOT NULL, "
                            "debit INTEGER NOT NULL DEFAULT 0, credit INTEGER NOT NULL DEFAULT 0);")
            c.close()
            db = Database(p)
            cols = {r[1] for r in db.conn.execute("PRAGMA table_info(journal_lines)")}
            self.assertTrue({"party", "memo", "cost_item"} <= cols)


if __name__ == "__main__":
    unittest.main()
