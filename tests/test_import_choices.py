"""Import templates speak the user's language: the drop-downs of fixed choices show words (Arabic in an Arabic
template), statement lines follow the row's Type and say what goes there, and every word imports as its code."""
import io
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import openpyxl  # noqa: E402

from erp import choices, demo, importkit, interchange  # noqa: E402
from erp.db import SUBTYPES, Database  # noqa: E402


class ImportChoices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Database(":memory:")
        demo.load(cls.db, "admin")

    def test_an_arabic_template_has_no_codes_in_its_drop_downs(self):
        for name in importkit.importable():
            wb = openpyxl.load_workbook(io.BytesIO(importkit.template_xlsx(self.db, name, "ar")))
            for col in wb["Lists"].iter_cols(min_row=2, values_only=True):
                for v in col:
                    with self.subTest(sheet=name, value=v):
                        self.assertFalse(isinstance(v, str) and re.fullmatch(r"[a-z][a-z0-9_]+", v))

    def test_every_word_in_either_language_imports_as_its_code(self):
        for s in interchange.SHEETS:
            for c in interchange.input_cols(s):
                for v in [x for x in c.get("choices") or [] if isinstance(x, str) and x]:
                    for w in (choices.label(s["name"], c["key"], v), choices.label(s["name"], c["key"], v, ar=True),
                              choices.label(s["name"], c["key"], v, ar=True, long=True), v):
                        with self.subTest(sheet=s["name"], col=c["key"], word=w):
                            self.assertEqual(choices.to_code(s["name"], c, w), v)
                            self.assertEqual(interchange._cell(w, c), v)

    def test_every_statement_line_says_what_goes_there(self):
        self.assertEqual(set(choices.SUBTYPE_INFO), {s for v in SUBTYPES.values() for s in v})
        self.assertEqual(choices.label("Chart of Accounts", "subtype", "other_current_asset", ar=True), "أصول متداولة أخرى")
        self.assertIn("العهد النقدية لدى العاملين", choices.SUBTYPE_INFO["other_current_asset"][3])

    def test_the_statement_line_list_follows_the_rows_type_and_a_guide_sheet_explains_it(self):
        wb = openpyxl.load_workbook(io.BytesIO(importkit.template_xlsx(self.db, "Chart of Accounts", "ar")))
        self.assertIn("دليل البنود", wb.sheetnames)
        dv = [d for d in wb.worksheets[0].data_validations.dataValidation if "INDIRECT" in (d.formula1 or "")]
        self.assertEqual(len(dv), 1)
        self.assertTrue(dv[0].showInputMessage)
        names = set(wb.defined_names.keys())
        self.assertTrue({f"ST_{k}" for k in range(1, len(SUBTYPES) + 1)} | {"ST_ALL"} <= names)
        guide = "\n".join(str(c) for row in wb["دليل البنود"].iter_rows(values_only=True) for c in row if c)
        self.assertIn("عهد نقدية لدى العاملين", guide)

    def test_a_filled_arabic_template_imports(self):
        wb = openpyxl.load_workbook(io.BytesIO(importkit.template_xlsx(self.db, "Chart of Accounts", "ar")))
        ws = wb.worksheets[0]
        heads = [c.value for c in ws[interchange.HEADER_ROW]]
        line = choices.label("Chart of Accounts", "subtype", "other_current_asset", ar=True, long=True)
        vals = {"رقم الحساب": "1036", "اسم الحساب": "عهدة م. أحمد", "النوع": "أصول", "بند القوائم المالية": line, "نشط": "نعم"}
        for i, h in enumerate(heads, start=1):
            if h in vals:
                ws.cell(interchange.FIRST_DATA_ROW, i, vals[h])
        buf = io.BytesIO()
        wb.save(buf)
        a = importkit.analyze(self.db, "Chart of Accounts", buf.getvalue(), "coa.xlsx")
        out, _ = importkit.read(self.db, "Chart of Accounts", buf.getvalue(), "coa.xlsx", table=a["table"], header_row=a["header_row"])
        self.assertEqual({k: out[0][k] for k in ("type", "subtype", "active")}, {"type": "Asset", "subtype": "other_current_asset", "active": "Yes"})

    def test_a_word_that_is_no_choice_is_refused_from_a_spreadsheet_but_a_backup_keeps_its_own(self):
        import copy
        from erp import textl10n
        data = interchange.export_data(self.db)
        bus = data["Business Units"]
        bus[0]["status"] = "Maybe"
        for sheet in (interchange.SheetData(copy.deepcopy(data)),):        # filled in by someone: refused, nothing kept
            with self.assertRaises(interchange.ImportErrors) as cm:
                interchange.import_data(Database(":memory:"), sheet, mode="replace")
            msg = next(e["error"] for e in cm.exception.errors if e["sheet"] == "Business Units")
            self.assertIn("Status: 'Maybe' is not one of the choices (Tendering, In Progress", msg)
            ar = textl10n.import_row_error(msg, "Business Units", "ar")
            self.assertIn("الحالة", ar)
            self.assertIn("ليس من الاختيارات المتاحة", ar)
        good = interchange.SheetData(copy.deepcopy(data))
        good["Business Units"][0]["status"] = choices.label("Business Units", "status", "In Progress", ar=True)   # its Arabic word: the code
        db2 = Database(":memory:")
        try:
            interchange.import_data(db2, good, mode="replace")
        except interchange.ImportErrors as e:
            self.fail(str(e.errors[:4]))
        self.assertEqual(db2.one("SELECT status FROM business_units WHERE code=?", (bus[0]["code"],))["status"], "In Progress")
        db3 = Database(":memory:")                                          # a backup restores whatever the books held
        interchange.import_data(db3, copy.deepcopy(data), mode="replace")
        self.assertEqual(db3.one("SELECT status FROM business_units WHERE code=?", (bus[0]["code"],))["status"], "Maybe")

    def test_a_spreadsheet_stays_one_through_the_shared_workspace(self):
        import gzip
        d = interchange.SheetData({"Business Units": [{"code": "X1", "name": "X", "status": "Maybe"}]})
        src, back = interchange.read_parsed(gzip.decompress(interchange.pack_read(d, "xlsx")))
        self.assertEqual(src, "xlsx")
        self.assertTrue(getattr(back, "strict", False))                     # every device refuses the same rows
        src, back = interchange.read_parsed(gzip.decompress(interchange.pack_read(dict(d), "json")))
        self.assertFalse(getattr(back, "strict", False))                    # a backup stays a backup

    def test_a_line_that_disagrees_with_its_documents_first_line_is_refused(self):
        import copy
        from erp import textl10n
        data = interchange.export_data(self.db)
        self.assertEqual(interchange.multi_line_conflicts(data), [])            # the app's own files always agree
        jv = data["Journal Vouchers"]
        v0 = jv[0]["voucher"]
        i = next(n for n, r in enumerate(jv) if n and r["voucher"] == v0)
        jv[i]["date"] = "2026-06-02"
        with self.assertRaises(interchange.ImportErrors) as cm:
            interchange.import_data(Database(":memory:"), interchange.SheetData(copy.deepcopy(data)), mode="replace")
        e = cm.exception.errors[0]
        self.assertIn("is '2026-06-02' on this line but", e["error"])
        self.assertIn("السطر الأول", textl10n.import_row_error(e["error"], e["sheet"], "ar"))
        jv[i]["date"] = ""                                                    # blank on a later line: fine
        interchange.import_data(Database(":memory:"), interchange.SheetData(copy.deepcopy(data)), mode="replace")

    def test_the_import_screen_shows_formats_and_allowed_values_in_both_languages(self):
        cols = {c["key"]: c for c in importkit.describe("Chart of Accounts")["columns"]}
        self.assertIn(["Yes", "نعم"], cols["active"]["choice_words"])
        self.assertEqual(cols["code"]["format_ar"], "نص")
        self.assertIn('c.choice_words.map(w => w[S.lang === "ar" ? 1 : 0])', (ROOT / "erp/static/app.js").read_text(encoding="utf-8"))



class TypedAsPeopleTypeThem(unittest.TestCase):
    """Numbers, dates and percentages as they arrive from real files, read the same on every import path."""
    def test_numbers(self):
        m = {"kind": "money"}
        for typed, want in (("12,50", "12.50"), ("1.250,50", "1250.50"), ("1,250.50", "1250.50"), ("١٬٢٥٠٫٥٠", "1250.50"),
                            ("(1,200)", "-1200"), ("12 500", "12500"), ("1,5,3", "1,5,3"), ("abc", "abc")):
            with self.subTest(typed=typed):
                self.assertEqual(interchange._cell(typed, m), want)

    def test_percentages(self):
        p = {"kind": "pct"}
        self.assertEqual(float(interchange._cell("25%", p)), 0.25)
        self.assertEqual(float(interchange._cell("٢٥٪", p)), 0.25)

    def test_dates(self):
        d = {"kind": "date"}
        for typed in ("٠٢/١٠/٢٠٢٦", "٢٠٢٦-١٠-٠٢", "02.10.2026", "2-Oct-2026", "02/10/26", "October 2, 2026", "2 أكتوبر 2026", "2026-10-02"):
            with self.subTest(typed=typed):
                self.assertEqual(interchange._cell(typed, d), "2026-10-02")
        self.assertEqual(interchange._cell("31/02/2026", d), "31/02/2026")      # not a real date: left for the error

    def test_statement_lines_typed_loosely(self):
        c = next(c for c in interchange.SHEET_BY_NAME["Chart of Accounts"]["cols"] if c["key"] == "subtype")
        for typed in ("Other current assets - employee cash custody", "أصول متداولة أخرى – العهد", "اصول متداوله اخري"):
            self.assertEqual(choices.to_code("Chart of Accounts", c, typed), "other_current_asset")
        self.assertEqual(choices.to_code("Chart of Accounts", c, "Property and equipment"), "fixed_asset")


class DemoAndExistingBooks(unittest.TestCase):
    def test_the_arabic_and_english_demo_files_import_into_any_books(self):
        from erp import textl10n
        for lang in ("ar", "en"):
            for books_ar in (False, True):
                with self.subTest(lang=lang, books_ar=books_ar):
                    db = Database(":memory:")
                    if books_ar:
                        textl10n.seed_names(db, "ar")
                    r = interchange.import_data(db, demo.translate_sheets(demo.sheets(), lang), mode="replace", dry_run=True)
                    self.assertGreater(sum(r["counts"].values()), 300)

    def test_a_lines_only_file_says_it_replaces_the_invoices_lines(self):
        db = Database(":memory:")
        demo.load(db, "admin")
        r = interchange.import_data(db, {"Invoice Lines": [{"number": "INV-D001", "description": "x", "qty": 1, "unit": "m", "price": 10}]},
                                    mode="append", dry_run=True)
        self.assertTrue(any("INV-D001" in n and "replaced" in n for n in r.get("notes", [])))

    def test_only_new_counts_only_what_it_adds(self):
        from erp import workbook
        db = Database(":memory:")
        demo.load(db, "admin")
        before = {t: db.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in ("loans", "custodies", "offers")}
        import tempfile
        path = Path(tempfile.mkdtemp()) / "books.xlsx"
        path.write_bytes(workbook.build_workbook(db))
        r = interchange.import_data(db, interchange.read_xlsx(path), mode="new")
        after = {t: db.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in before}
        self.assertEqual(before, after)
        for k in ("Loans", "Cash custody", "Offers"):
            self.assertNotIn(k, r["counts"])


if __name__ == "__main__":
    unittest.main()
