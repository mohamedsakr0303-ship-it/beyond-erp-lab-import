"""Import any screen's data from your own spreadsheet, with a downloadable layout the app fully supports.

For every importable sheet (Direct Costs, Operating Expenses, Payroll, Receipts, Payments, Sales Invoices + lines,
BOQs, Price List, Customers & Suppliers, Products, Business Units, Fixed Assets, Stock, Journal Vouchers, Chart of
Accounts, Partners, Tax Codes…):

  template_xlsx / template_csv — the exact layout: bilingual headings, an example row, drop-downs for the allowed
                                 values (accounts, business units, tax codes, choices) and a "How to fill" sheet
                                 explaining every column (required?, format, allowed values)
  analyze                      — reads YOUR file (Excel or CSV, any sheet, heading row anywhere in the top 30
                                 rows), matches its columns to the app's by name in English or Arabic, common
                                 synonyms and spelling closeness, and says what is still missing
  rows                         — turns the file into rows with the column matching the user confirmed
then the normal all-or-nothing import runs (a check run first lists every problem with its row number).
"""
import csv
import datetime as _dt
import difflib
import io
import re

from . import accounting as acc, choices, xlsafe
from .interchange import FIRST_DATA_ROW, HEADER_ROW, SHEET_BY_NAME, SHEETS, _cell, header_map, input_cols

# which screen a sheet belongs to (permission to import = permission to add there)
SHEET_PERM = {
    "Direct Costs": "purchasing.costs", "Operating Expenses": "purchasing.expenses", "Payroll": "payroll", "Employees": "payroll.employees", "Payroll Run": "payroll.runs", "Payroll Settings": "payroll.settings",
    "Receipt Vouchers": "sales.receipts", "Payment Vouchers": "purchasing.payments", "Sales Invoices": "sales.invoices",
    "Invoice Lines": "sales.invoices", "BOQ Lines": "estimating.boqs", "Price List": "estimating.prices",
    "Parties": "sales.parties", "Business Units": "business_units.units", "Fixed Assets": "assets.register",
    "Stock Items": "stock.balances", "Stock Moves": "stock.moves", "Warehouses": "stock.warehouses",
    "Journal Vouchers": "accounting.journal", "Chart of Accounts": "accounting.coa", "Partners": "partners",
    "Partner Shares": "partners", "Partner Transactions": "partners.transactions", "Tax Codes": "tax.codes",
    "Products": "shop.products", "Settings": "settings", "Loans": "accounting.loans", "Loan Transactions": "accounting.loans",
    "Entry Models": "accounting.journal", "Saved Fill-ins": "settings",
    "Cash Custody": "purchasing.custody", "Custody Expenses": "purchasing.custody", "Rental Equipment": "rentals.fleet",
    "Rental Stock Lots": "rentals.fleet", "Rental Contracts": "rentals.contracts", "Sales Contracts": "shop.orders",
    "Bank Statement Lines": "accounting.bankrec", "Recurring Entries": "accounting.recurring", "Budgets": "accounting.budgets", "Purchase Orders": "purchasing.orders", "Service Plans": "shop.subscriptions", "Service Subscriptions": "shop.subscriptions",
}

# words people use for the same thing (English & Arabic); matched after the exact names
SYNONYMS = {
    "date": ["date", "day", "transaction date", "posting date", "invoice date", "doc date", "التاريخ", "تاريخ", "اليوم"],
    "description": ["description", "details", "item", "item description", "narration", "particulars", "memo", "البيان",
                    "الوصف", "التفاصيل", "البند", "بيان"],
    "memo": ["memo", "notes", "description", "remarks", "البيان", "ملاحظات"],
    "amount": ["amount", "value", "total", "sum", "net", "المبلغ", "القيمة", "الإجمالي", "الاجمالي", "مبلغ"],
    "vendor": ["vendor", "supplier", "supplier name", "vendor name", "paid to", "payee", "subcontractor", "المورد",
               "اسم المورد", "المقاول", "الجهة"],
    "party": ["party", "payee", "name", "paid to", "received from", "customer", "client", "supplier", "الجهة", "العميل",
              "المورد", "الاسم", "المستفيد"],
    "customer": ["customer", "client", "client name", "customer name", "العميل", "اسم العميل"],
    "worker": ["worker", "employee", "name", "staff", "الموظف", "العامل", "الاسم"],
    "bu_code": ["business unit", "project", "project id", "project code", "cost centre", "cost center", "job", "site",
                "المشروع", "كود المشروع", "مركز التكلفة", "الموقع"],
    "account": ["account", "account code", "gl account", "gl", "account no", "الحساب", "رقم الحساب", "كود الحساب"],
    "qty": ["qty", "quantity", "units", "no", "count", "days", "الكمية", "العدد", "كمية"],
    "rate": ["rate", "unit cost", "unit price", "price", "cost", "السعر", "سعر الوحدة", "الفئة", "التكلفة"],
    "price": ["price", "unit price", "selling price", "rate", "السعر", "سعر البيع", "سعر الوحدة"],
    "unit": ["unit", "uom", "unit of measure", "الوحدة", "وحدة"],
    "paid": ["paid", "paid now", "amount paid", "cash paid", "المدفوع", "المسدد", "مدفوع"],
    "method": ["method", "payment method", "paid by", "mode", "طريقة الدفع", "طريقة السداد"],
    "ref": ["ref", "reference", "ref no", "receipt no", "voucher no", "doc no", "cheque no", "المرجع", "رقم المرجع", "رقم الإيصال"],
    "debit": ["debit", "dr", "مدين"], "credit": ["credit", "cr", "دائن"],
    "name": ["name", "full name", "title", "الاسم", "اسم"],
    "code": ["code", "id", "no", "number", "sku", "item code", "الكود", "الرقم", "كود"],
    "category": ["category", "group", "type", "التصنيف", "الفئة", "المجموعة", "النوع"],
    "phone": ["phone", "mobile", "tel", "telephone", "الهاتف", "الموبايل", "رقم الهاتف", "تليفون"],
    "email": ["email", "e-mail", "mail", "البريد", "البريد الإلكتروني"],
    "tax_id": ["tax id", "tax no", "tax number", "tax registration", "trn", "vat no", "الرقم الضريبي", "رقم التسجيل الضريبي"],
    "number": ["number", "invoice no", "invoice number", "no", "رقم الفاتورة", "الرقم"],
    "item_no": ["item no", "item", "no", "code", "رقم البند", "البند", "م"],
    "vat_code": ["vat", "vat code", "tax code", "ضريبة القيمة المضافة", "كود الضريبة"],
    "wht_code": ["wht", "withholding", "withholding tax", "خصم وتحصيل", "ضريبة الخصم"],
    "notes": ["notes", "remarks", "comment", "comments", "ملاحظات"],
    "cost": ["cost", "unit cost", "purchase price", "التكلفة", "سعر الشراء"],
    "type": ["type", "kind", "النوع"], "status": ["status", "state", "الحالة"],
    "partner": ["partner", "owner", "الشريك"], "warehouse": ["warehouse", "store", "location", "المخزن"],
    "item": ["item", "product", "material", "الصنف", "المنتج", "الخامة"],
}
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")


def _norm(s):
    s = str(s or "").translate(_AR_DIGITS).strip().lower()
    s = s.split(" / ")[0] if " / " in s and not re.search(r"[؀-ۿ]", s.split(" / ")[0]) else s
    s = re.sub(r"[_\-.:#()\[\]*/\\]+", " ", s)
    s = re.sub(r"[إأآ]", "ا", s).replace("ة", "ه").replace("ى", "ي")
    s = re.sub(r"(^|\s)(?:وال|بال|ال)(?=[\u0600-\u06FF]{2})", r"\1", s)     # الحساب = حساب
    return re.sub(r"\s+", " ", s).strip()


def sheet(name):
    s = SHEET_BY_NAME.get(name)
    if not s:
        raise acc.ValidationError(f"Unknown sheet {name!r}")
    return s


def importable():
    return [s["name"] for s in SHEETS]


def describe(name):
    """The columns of a sheet as the user sees them (for the mapping screen and the 'How to fill' sheet)."""
    s = sheet(name)
    out = []
    for c in input_cols(s):
        fmt = {"date": "date (2026-10-02, 02/10/2026 or an Excel date)", "num": "number", "money": "amount (number)",
               "pct": "percentage (25% or 0.25)", "acct": "account code from the chart of accounts",
               "bu": "business unit code", "tax": "tax code (VAT14…)"}.get(c["kind"], "text")
        ch = [v for v in (c.get("choices") or []) if v not in ("", None)]
        out.append({"key": c["key"], "en": c["en"], "ar": c["ar"], "kind": c["kind"], "required": bool(c["required"]),
                    "choices": c.get("choices") or [], "note": c.get("note") or "", "format": fmt, "format_ar": _fmt(c["kind"], True),
                    "choice_words": [[choices.label(s["name"], c["key"], v), choices.label(s["name"], c["key"], v, ar=True)] for v in ch],
                    "default_role": c.get("role") or ""})
    return {"name": s["name"], "ar": s.get("ar", ""), "title": s.get("title", ""), "columns": out}


# ------------------------------------------------------------------ templates
def _lists(db):
    return {"acct": [r["code"] for r in db.q("SELECT code FROM accounts WHERE active=1 ORDER BY code")],
            "bu": [r["code"] for r in db.q("SELECT code FROM business_units ORDER BY code")],
            "tax": [r["code"] for r in db.q("SELECT code FROM tax_codes WHERE active=1 ORDER BY code")]}


FORMATS = {"date": ("date (2026-10-02, 02/10/2026 or an Excel date)", "تاريخ (2026-10-02 أو 02/10/2026 أو تاريخ إكسل)"),
           "num": ("number", "رقم"), "money": ("amount (number)", "مبلغ (رقم)"), "pct": ("percentage (25% or 0.25)", "نسبة (25% أو 0.25)"),
           "acct": ("account code from the chart of accounts", "كود حساب من دليل الحسابات"), "bu": ("business unit code", "كود وحدة العمل"),
           "tax": ("tax code (VAT14…)", "كود ضريبة (VAT14…)")}


def _fmt(kind, ar=False):
    return FORMATS.get(kind, ("text", "نص"))[1 if ar else 0]


def template_xlsx(db, name, lang="en", rows=None):
    """One sheet laid out exactly as the importer reads it, plus 'Lists' (drop-down values) and 'How to fill'."""
    import openpyxl
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter as L
    from openpyxl.worksheet.datavalidation import DataValidation
    from .workbook import EXAMPLES, EXAMPLES_AR
    s, d = sheet(name), describe(name)
    cols = input_cols(s)
    ar = lang == "ar"
    tr = {}
    if ar:              # one language per file: the Arabic template carries no English labels
        from .workbook_ar import translations
        tr = translations()
    T = lambda en, a: a if ar else en  # noqa: E731
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = (s.get("ar") if ar and s.get("ar") else s["name"])[:31]
    if ar:
        ws.sheet_view.rightToLeft = True
    navy, grey = PatternFill("solid", fgColor="1F2A44"), PatternFill("solid", fgColor="C0392B")
    ws.cell(1, 1, (s.get("ar") if ar else s["name"]) + " — " + ("قالب الاستيراد" if ar else "import template")).font = Font(bold=True, size=13)
    ws.cell(2, 1, "اكتب البيانات من الصف 5. الأعمدة الحمراء مطلوبة. لا تغيّر صف العناوين (الصف 4). الصف 3 مثال فقط ولا يُستورد."
            if ar else "Enter data from row 5. Red headings are required. Keep the heading row (row 4) as it is. "
            "Row 3 is only an example and is not imported. See 'How to fill' for every column.").font = Font(size=9, color="555555")
    ex = {**EXAMPLES.get(s["name"], {}), **(EXAMPLES_AR.get(s["name"], {}) if ar else {})}
    by_key = {c["key"]: c for c in cols}
    word = lambda c, v: choices.label(s["name"], c["key"], v, ar, long=c["key"] == "subtype") if c.get("choices") else v  # noqa: E731
    ex = {k: word(by_key[k], v) if k in by_key and by_key[k].get("choices") and v in by_key[k]["choices"]
          else (tr.get(v, v) if ar and isinstance(v, str) else v) for k, v in ex.items()}
    lists = _lists(db)
    lst = wb.create_sheet("Lists")
    lcol = 0
    for i, c in enumerate(cols, start=1):
        h = ws.cell(HEADER_ROW, i, c["ar"] if ar else c["en"])
        h.font = Font(bold=True, color="FFFFFF", size=9)
        h.fill = grey if c["required"] else navy
        h.alignment = Alignment(wrap_text=True, vertical="center")
        tip = next(x for x in d["columns"] if x["key"] == c["key"])
        note = (tr.get(tip["note"]) or "") if ar else tip["note"]
        allowed = [choices.label(s["name"], c["key"], v, ar) for v in tip["choices"] if v not in ("", None)]
        h.comment = Comment((T("Required. ", "مطلوب. ") if c["required"] else "") + _fmt(tip["kind"], ar) +
                            (f"{T('. Allowed: ', '. القيم: ')}{'، '.join(allowed) if ar else ', '.join(allowed)}" if allowed and len(allowed) <= 12 else
                             (T(". Pick from the list", ". اختر من القائمة") if allowed else "")) +
                            (f". {note}" if note else "") +
                            (T(". Blank = the default account", ". فارغ = الحساب الافتراضي") if tip["default_role"] else ""), "BeyondERP")
        ws.column_dimensions[L(i)].width = max(12, min(40, c["width"] + 2))
        if c["key"] in ex:
            e = ws.cell(3, i, ex[c["key"]])
            e.font = Font(italic=True, size=8, color="999999")
        # fixed choices show as words in the template's language (the importer turns them back into codes)
        values = [word(c, v) for v in c["choices"] if v not in ("", None)] if c.get("choices") else lists.get(c["kind"])
        if values:
            lcol += 1
            lst.cell(1, lcol, c["ar"] if ar else c["en"])
            for r, v in enumerate(values, start=2):
                lst.cell(r, lcol, v)
            src = f"=Lists!${L(lcol)}$2:${L(lcol)}${len(values) + 1}"
            if s["name"] == "Chart of Accounts" and c["key"] == "subtype" and "type" in by_key:
                src, lcol = _subtype_lists(wb, lst, lcol, cols, ar, L)     # only the lines of the row's Type
            dv = DataValidation(type="list", formula1=src, allow_blank=True)
            dv.showErrorMessage = bool(c.get("choices"))      # accounts / units may be new: warn only for fixed choices
            dv.errorStyle = "warning"                          # an old file's codes are still accepted
            if c["key"] == "subtype" and s["name"] == "Chart of Accounts":
                dv.promptTitle = T("Statement line", "بند القوائم المالية")
                dv.prompt = T("Choose the Type first: the list then shows only its lines, each with what goes there. "
                              "Full guide: the 'Statement lines' sheet.",
                              "اختر النوع أولاً: تظهر بنوده فقط، ومع كل بند ما يُسجَّل فيه. الدليل الكامل في ورقة «دليل البنود».")
                dv.showInputMessage = True
            ws.add_data_validation(dv)
            dv.add(f"{L(i)}{FIRST_DATA_ROW}:{L(i)}{FIRST_DATA_ROW + 2000}")
        if c["kind"] == "date":
            for r in range(FIRST_DATA_ROW, FIRST_DATA_ROW + 300):
                ws.cell(r, i).number_format = "yyyy-mm-dd"
    ws.cell(3, 1).value = ws.cell(3, 1).value if ws.cell(3, 1).value not in (None, "") else ("مثال" if ar else "e.g.")
    for r, row in enumerate(rows or [], start=FIRST_DATA_ROW):       # demo rows, ready to import
        for i, c in enumerate(cols, start=1):
            v = row.get(c["key"])
            if v not in (None, ""):
                if c.get("choices") and v in c["choices"]:
                    v = word(c, v)
                if c["kind"] == "date":
                    try:
                        v = _dt.date.fromisoformat(str(v)[:10])
                    except ValueError:
                        pass
                ws.cell(r, i, v)
    ws.row_dimensions[HEADER_ROW].height = 32
    lst.sheet_state = "hidden"
    how = wb.create_sheet("How to fill" if not ar else "طريقة التعبئة")
    if ar:
        how.sheet_view.rightToLeft = True
    head = ["العمود", "مطلوب", "الصيغة", "القيم المسموحة", "ملاحظات"] if ar else ["Column", "Required", "Format", "Allowed values", "Notes"]
    for i, t in enumerate(head, start=1):
        c = how.cell(1, i, t)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = navy
    for r, x in enumerate(d["columns"], start=2):
        note = ((tr.get(x["note"]) or "") if ar else x["note"]) + (T(" Blank = the default account.", " فارغ = الحساب الافتراضي.") if x["default_role"] else "")
        allowed = [choices.label(s["name"], x["key"], v, ar) for v in x["choices"] if v not in ("", None)]
        if x["key"] == "subtype" and s["name"] == "Chart of Accounts":
            allowed = [T("see the 'Statement lines' sheet", "انظر ورقة «دليل البنود»")]
        for i, v in enumerate([x["ar"] if ar else x["en"], T("Yes", "نعم") if x["required"] else "", _fmt(x["kind"], ar),
                               ("، " if ar else ", ").join(allowed), note.strip()], start=1):
            how.cell(r, i, v).alignment = Alignment(wrap_text=True, vertical="top")
    for i, w in enumerate((28, 9, 34, 34, 50), start=1):
        how.column_dimensions[L(i)].width = w
    how.cell(len(d["columns"]) + 3, 1, T("Your own file works too: import it from the same screen and match its columns to these. "
             "Headings in English or Arabic and common synonyms are matched automatically.",
             "يمكنك أيضاً استيراد ملفك الخاص من نفس الشاشة ومطابقة أعمدته بهذه. العناوين بالعربية أو الإنجليزية تُطابق تلقائياً.")).font = Font(italic=True, size=9)
    if s["name"] == "Chart of Accounts":
        _subtype_guide(wb, ar)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _subtype_lists(wb, lst, lcol, cols, ar, L):
    """Lists for a drop-down of statement lines that follows the row's Type: one named list per Type (ST_1…), all of
    them (ST_ALL) while no Type is chosen yet."""
    from openpyxl.workbook.defined_name import DefinedName
    types = list(choices.SUBTYPES)
    tcol = lcol + 1
    lst.cell(1, tcol, "Type")
    for r, t in enumerate(types, start=2):
        lst.cell(r, tcol, choices.label("Chart of Accounts", "type", t, ar))

    def name(n, col, count):
        ref = f"Lists!${L(col)}$2:${L(col)}${count + 1}"
        try:
            wb.defined_names[n] = DefinedName(n, attr_text=ref)
        except TypeError:                     # older openpyxl
            wb.defined_names.append(DefinedName(n, attr_text=ref))

    name("ST_ALL", lcol, sum(len(v) for v in choices.SUBTYPES.values()))
    for k, t in enumerate(types, start=1):
        col = tcol + k
        lines = choices.SUBTYPES[t]
        lst.cell(1, col, t)
        for r, st in enumerate(lines, start=2):
            lst.cell(r, col, choices.label("Chart of Accounts", "subtype", st, ar, long=True))
        name(f"ST_{k}", col, len(lines))
    ti = next(i for i, c in enumerate(cols, start=1) if c["key"] == "type")
    tref = f"${L(ti)}{FIRST_DATA_ROW}"
    rng = f"Lists!${L(tcol)}$2:${L(tcol)}${len(types) + 1}"
    return f'INDIRECT(IFERROR("ST_"&MATCH({tref},{rng},0),"ST_ALL"))', col


def _subtype_guide(wb, ar):
    """A visible sheet: every statement line by Type, what goes there, and where some everyday accounts belong."""
    from openpyxl.styles import Alignment, Font, PatternFill
    g = wb.create_sheet("دليل البنود" if ar else "Statement lines")
    if ar:
        g.sheet_view.rightToLeft = True
    T = lambda en, a: a if ar else en  # noqa: E731
    g["A1"] = T("Statement lines — where each account goes", "بنود القوائم المالية — أين يوضع كل حساب")
    g["A1"].font = Font(bold=True, size=13)
    g["A2"] = T("Pick the Type, then the line that describes what the account holds. Unsure? Find a similar example below.",
                "اختر النوع ثم البند الذي يصف ما يحتويه الحساب. لست متأكداً؟ ابحث عن مثال مشابه بالأسفل.")
    g["A2"].font = Font(size=9, color="555555")
    head = [T("Type", "النوع"), T("Statement line", "البند"), T("What goes here (examples)", "ما يُسجَّل فيه (أمثلة)")]
    for j, h in enumerate(head, start=1):
        c = g.cell(4, j, h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F2A44")
    r = 5
    for t, lines in choices.SUBTYPES.items():
        for st in lines:
            en, a, hen, har = choices.SUBTYPE_INFO[st]
            for j, v in enumerate((choices.label("Chart of Accounts", "type", t, ar), a if ar else en, har if ar else hen), start=1):
                g.cell(r, j, v).alignment = Alignment(wrap_text=True, vertical="top")
            r += 1
    r += 1
    g.cell(r, 1, T("Common accounts", "حسابات شائعة")).font = Font(bold=True)
    r += 1
    for acct, t, st in COMMON_ACCOUNTS:
        name = acct[1] if ar else acct[0]
        for j, v in enumerate((name, choices.label("Chart of Accounts", "type", t, ar), choices.label("Chart of Accounts", "subtype", st, ar)), start=1):
            g.cell(r, j, v)
        r += 1
    for col, w in zip("ABC", (30, 34, 70)):
        g.column_dimensions[col].width = w


# everyday accounts people ask about: (English, Arabic), Type, statement line
COMMON_ACCOUNTS = [
    (("Cash custody with employees", "عهد نقدية لدى العاملين"), "Asset", "other_current_asset"),
    (("Employee advances / loans", "سلف وقروض العاملين"), "Asset", "other_current_asset"),
    (("Petty cash", "صندوق المصروفات النثرية"), "Asset", "cash"),
    (("Cheques under collection", "شيكات تحت التحصيل"), "Asset", "receivable"),
    (("Advances to suppliers", "دفعات مقدمة للموردين"), "Asset", "prepaid"),
    (("Input VAT", "ضريبة القيمة المضافة — مدخلات"), "Asset", "prepaid"),
    (("Security deposits paid (long-term)", "تأمينات لدى الغير (طويلة الأجل)"), "Asset", "other_noncurrent_asset"),
    (("Salaries payable", "رواتب مستحقة"), "Liability", "accrued"),
    (("Social insurance payable", "تأمينات اجتماعية مستحقة"), "Liability", "accrued"),
    (("Output VAT", "ضريبة القيمة المضافة — مخرجات"), "Liability", "tax_payable"),
    (("Customer advances", "دفعات مقدمة من العملاء"), "Liability", "deferred_revenue"),
    (("Customer security deposits", "تأمينات العملاء"), "Liability", "other_current_liability"),
    (("Partner loans", "قروض الشركاء"), "Liability", "long_term_debt"),
    (("Bank charges", "مصروفات بنكية"), "Expense", "general_admin"),
    (("Fuel and vehicle costs", "وقود ومصروفات سيارات"), "Expense", "other_operating"),
]


def template_csv(name, rows=None):
    s = sheet(name)
    cols = input_cols(s)
    from .workbook import EXAMPLES
    ex = EXAMPLES.get(s["name"], {})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"{c['en']} / {c['ar']}" for c in cols])
    if rows:
        for row in rows:
            w.writerow([row.get(c["key"], "") for c in cols])
    elif ex:
        w.writerow([ex.get(c["key"], "") for c in cols])
    return ("﻿" + buf.getvalue()).encode("utf-8")


# ------------------------------------------------------------------- reading
def _tables(raw, filename=""):
    """[(sheet title, rows)] from an Excel or CSV file."""
    if raw[:2] == b"PK":                     # by what the file is, not its name: a CSV renamed .xlsx is still read
        import zipfile
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
        except (zipfile.BadZipFile, KeyError, ValueError, OSError):
            raise acc.ValidationError(xlsafe.NOT_XLSX) from None
        out = []
        for ws in wb.worksheets:
            if ws.sheet_state != "visible" and len(wb.worksheets) > 1:
                continue
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            out.append((ws.title, rows))
        return out
    if raw[:4] == b"\xd0\xcf\x11\xe0":           # the old Excel format (.xls)
        raise acc.ValidationError("This is an old Excel file (.xls). Open it in Excel, save it as .xlsx, and choose it again.")
    text = xlsafe.decode_text(raw)
    sample = text[:4000]
    delim = "\t" if "\t" in sample else (";" if sample.count(";") > sample.count(",") else ",")
    return [("CSV", [[xlsafe.csv_in(v) for v in r] for r in csv.reader(io.StringIO(text), delimiter=delim)])]


def _guess(cols, headers):
    """{file column index: key} — exact names first, then synonyms, then closest spelling."""
    exact = header_map({"cols": cols}, headers)
    out, used = {}, set()
    for i, k in exact.items():
        if k and k not in used and any(c["key"] == k for c in cols):
            out[i], _ = k, used.add(k)
    names = {}
    for c in cols:
        cands = {_norm(c["en"]), _norm(c["ar"]), _norm(c["key"].replace("_", " "))}
        cands |= {_norm(x) for x in SYNONYMS.get(c["key"], [])}
        base = c["key"].split("_")[0]
        if base != c["key"]:
            cands |= {_norm(x) for x in SYNONYMS.get(base, [])}
        names[c["key"]] = {x for x in cands if x}
    norm = {i: _norm(h) for i, h in enumerate(headers) if str(h or "").strip()}
    for i, h in norm.items():      # synonyms: the required columns win ties
        if i in out:
            continue
        for c in sorted(cols, key=lambda c: not c["required"]):
            if c["key"] not in used and h in names[c["key"]]:
                out[i] = c["key"]
                used.add(c["key"])
                break
    for i, h in norm.items():      # closest spelling
        if i in out or len(h) < 3:
            continue
        best, score = None, 0.0
        for c in cols:
            if c["key"] in used:
                continue
            for n in names[c["key"]]:
                r = difflib.SequenceMatcher(None, h, n).ratio()
                if r > score:
                    best, score = c["key"], r
        if best and score >= 0.84:
            out[i] = best
            used.add(best)
    return out


def analyze(db, name, raw, filename="", table=None, header_row=None):
    """Find the heading row and match the file's columns to the sheet's. Nothing is imported."""
    s = sheet(name)
    cols = input_cols(s)
    if not raw:
        raise acc.ValidationError("Choose a file")
    tables = _tables(raw, filename)
    if not tables:
        raise acc.ValidationError("The file has no sheets")
    best = None
    for title, rows in tables:
        if table and title != table:
            continue
        for hi, r in enumerate(rows[:30]):
            if header_row is not None and hi != int(header_row):
                continue
            filled = sum(1 for x in r if str(x or "").strip())
            if filled < 1:
                continue
            g = _guess(cols, r)
            score = len(g) * 10 + sum(1 for k in g.values() if any(c["key"] == k and c["required"] for c in cols)) * 5 \
                + (50 if title == s["name"] else 0)
            if best is None or score > best[0]:
                best = (score, title, hi, r, g, rows)
    if best is None:
        raise acc.ValidationError("No heading row was found")
    _, title, hi, headers, guess, rows = best
    body = [r for r in rows[hi + 1:] if any(str(x or "").strip() for x in r)]
    head = [str(h if h is not None else "").strip() or f"Column {i + 1}" for i, h in enumerate(headers)]
    mapping = {str(i): k for i, k in guess.items()}
    missing = [c["key"] for c in cols if c["required"] and c["key"] not in guess.values()]
    return {"sheet": s["name"], "table": title, "tables": [t for t, _ in tables], "header_row": hi, "headers": head,
            "mapping": mapping, "missing": missing, "rows": len(body),
            "sample": [[_show(x) for x in r[:len(head)]] for r in body[:5]],
            "columns": describe(name)["columns"], "exact": len(guess) == len(cols) and not missing}


def _show(v):
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()[:10]
    return "" if v is None else str(v)[:60]


def read(db, name, raw, filename="", mapping=None, table=None, header_row=None):
    """The file's rows as import rows, with the confirmed column matching ({file column index: key})."""
    s = sheet(name)
    a = analyze(db, name, raw, filename, table, header_row)
    m = {int(k): v for k, v in (mapping if mapping is not None else a["mapping"]).items() if v}
    missing = [c["en"] for c in input_cols(s) if c["required"] and c["key"] not in m.values()]
    if missing:
        raise acc.ValidationError("Match a column of your file to: " + ", ".join(missing))
    keys = {c["key"]: c for c in s["cols"]}
    rows = next(r for t, r in _tables(raw, filename) if t == a["table"])
    from .workbook import EXAMPLES
    example = {k: str(_cell(v, keys[k])) for k, v in EXAMPLES.get(s["name"], {}).items() if k in keys and v not in ("", None)}
    out, nums = [], []
    for n, raw_row in enumerate(rows[a["header_row"] + 1:], start=a["header_row"] + 2):
        row = {}
        for i, k in m.items():
            if k in keys and i < len(raw_row):
                v = raw_row[i]
                if isinstance(v, str):
                    v = v.translate(_AR_DIGITS) if re.fullmatch(r"[\s٠-٩0-9.,٫٬()+-]+", v) else v
                    if keys[k]["kind"] in ("num", "money", "pct") and re.fullmatch(r"\s*\(?[\d,]+(\.\d+)?\)?\s*", v):
                        v = ("-" if "(" in v else "") + v.strip(" ()").replace(",", "")
                row[k] = _cell(v, keys[k])
        if example and all(str(row.get(k, "")) == v for k, v in example.items() if k in m.values()) \
                and any(k in m.values() for k in example):
            continue            # the template's own example row (the CSV template has it under the headings): never imported
        if any(v not in ("", None) for v in row.values()):
            out.append(row)
            nums.append(n)
    if not out:
        raise acc.ValidationError("No rows to import under the heading row")
    return out, nums
