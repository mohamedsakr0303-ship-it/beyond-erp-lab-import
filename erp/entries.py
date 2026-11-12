"""Entry types: named two-line entries people use often ("Pay rent", "Move cash to the bank", "Transfer to legal
reserve"...). Each side is a fixed account or a choice made when entering. They are editable in Settings → Menus,
feed the journal voucher's template list and can be put on the "+ New" menu.

A side is written as:
  "1020"                      a fixed account
  "role:bank"                 the account holding a system role (follows renumbering)
  "subtype:cash"              choose among accounts on these statement lines ("subtype:short_term_debt,long_term_debt")
  "type:Expense"              choose among accounts of these types ("type:COGS,Expense")
  ""                          choose any account
"""
import json
import re

from . import accounting as acc
from .accounting import ValidationError, text

# id, name, name_ar, voucher type (kind), debit side, credit side, party, business unit, on "+ New" by default
BUILTIN = [
    ("cash_transfer", "Move money between cash & bank", "تحويل بين الخزينة والبنك", "Journal Voucher", "subtype:cash", "subtype:cash", "none", "none"),
    ("bank_charges", "Bank charges", "مصروفات بنكية", "Journal Voucher", "type:Expense", "subtype:cash", "none", "optional"),
    ("loan_in", "Loan received", "استلام قرض", "Journal Voucher", "subtype:cash", "subtype:short_term_debt,long_term_debt", "optional", "none"),
    ("loan_out", "Loan repayment", "سداد قرض", "Journal Voucher", "subtype:short_term_debt,long_term_debt", "subtype:cash", "optional", "none"),
    ("interest", "Interest paid", "فوائد مدفوعة", "Journal Voucher", "role:interest", "subtype:cash", "optional", "none"),
    ("interest_in", "Interest received", "فوائد دائنة", "Journal Voucher", "subtype:cash", "role:interest_income", "optional", "none"),
    ("depreciation", "Depreciation (manual)", "إهلاك (يدوي)", "Depreciation", "role:depreciation", "role:accum_depreciation", "none", "optional"),
    ("amortization", "Amortization (manual)", "استهلاك (يدوي)", "Depreciation", "role:amortization", "role:accum_amortization", "none", "optional"),
    ("asset_buy", "Asset purchase (without the register)", "شراء أصل (بدون السجل)", "Journal Voucher", "subtype:fixed_asset,intangible", "subtype:cash", "optional", "optional"),
    ("prepaid_use", "Use up a prepaid expense", "استهلاك مصروف مقدم", "Journal Voucher", "type:Expense", "subtype:prepaid", "optional", "optional"),
    ("accrue", "Accrue an expense (owed, not yet billed)", "إثبات مصروف مستحق", "Journal Voucher", "type:Expense", "subtype:accrued", "optional", "optional"),
    ("unbilled", "Work done, not yet invoiced", "إيراد مستحق غير مفوتر", "Journal Voucher", "subtype:other_current_asset", "type:Revenue", "optional", "required"),
    ("advance_apply", "Apply client advance to invoice", "تسوية دفعة مقدمة مع فاتورة", "Journal Voucher", "role:client_advances", "role:ar", "required", "optional"),
    ("bad_debt", "Write off a bad debt", "إعدام دين معدوم", "Journal Voucher", "role:bad_debts", "role:ar", "required", "optional"),
    ("doubtful", "Provision for doubtful debts", "مخصص ديون مشكوك فيها", "Journal Voucher", "role:bad_debts", "role:doubtful_allowance", "optional", "none"),
    ("lg_margin", "Letter of guarantee — cash margin", "غطاء خطاب ضمان", "Journal Voucher", "subtype:other_current_asset", "subtype:cash", "optional", "optional"),
    ("tax_accrual", "Income tax accrual", "إثبات ضريبة الدخل", "Journal Voucher", "role:income_tax", "role:income_tax_payable", "none", "none"),
    ("vat_offset", "VAT settlement (offset input VAT)", "تسوية ضريبة القيمة المضافة", "Journal Voucher", "role:vat_output", "role:vat_input", "none", "none"),
    ("vat_pay", "Pay VAT to the Tax Authority", "سداد ضريبة القيمة المضافة", "Journal Voucher", "role:vat_output", "subtype:cash", "none", "none"),
    ("wht_pay", "Pay withholding tax to the Tax Authority", "سداد ضريبة الخصم والتحصيل", "Journal Voucher", "role:wht_payable", "subtype:cash", "none", "none"),
    ("wht_offset", "Offset client-withheld tax against income tax", "خصم ضريبة الخصم والتحصيل من ضريبة الدخل", "Journal Voucher", "role:income_tax_payable", "role:wht_receivable", "none", "none"),
    ("legal_reserve", "Transfer to legal reserve", "تجنيب الاحتياطي القانوني", "Reserve Transfer", "role:retained", "subtype:reserves", "none", "none"),
    ("investment", "Buy an investment", "شراء استثمار", "Journal Voucher", "subtype:investment", "subtype:cash", "optional", "none"),
    # work and purchases owed, nothing paid yet: the cost lands on the project now, the supplier is owed until paid
    ("work_on_credit", "Work at a project on credit (owed to the supplier)", "أعمال بمشروع على الحساب (مستحقة للمورد)",
     "Direct Cost", "type:COGS", "role:ap", "required", "required"),
    ("sub_certified", "Subcontractor work certified (owed)", "أعمال مقاول باطن معتمدة (مستحقة)", "Direct Cost",
     "role:subcontract_cost", "role:sub_payable", "required", "required"),
    ("expense_on_credit", "Expense on credit (owed to the supplier)", "مصروف على الحساب (مستحق للمورد)", "Operating Expense",
     "type:Expense", "role:ap", "required", "optional"),
    ("supplier_advance", "Advance paid to a supplier", "دفعة مقدمة لمورد", "Payment Voucher", "role:supplier_advances",
     "subtype:cash", "required", "optional"),
    ("supplier_advance_apply", "Apply a supplier advance to their bill", "تسوية دفعة مقدمة مع فاتورة المورد", "Journal Voucher",
     "role:ap", "role:supplier_advances", "required", "optional"),
    ("client_advance", "Advance received from a client", "دفعة مقدمة من عميل", "Receipt Voucher", "subtype:cash",
     "role:client_advances", "required", "optional"),
    ("retention_in", "Client retention collected", "تحصيل محتجزات العميل", "Receipt Voucher", "subtype:cash",
     "role:retention_receivable", "required", "optional"),
    ("cheque_deposit", "Deposit a customer's cheque", "إيداع شيك عميل", "Journal Voucher", "subtype:cash", "role:notes_receivable",
     "optional", "none"),
    ("partner_paid", "A partner paid a company cost personally", "شريك دفع تكلفة للشركة من ماله", "Journal Voucher",
     "type:Expense,COGS", "role:partner_current", "required", "optional"),
    ("employee_advance", "Salary advance to an employee", "سلفة لموظف", "Journal Voucher", "role:employee_advances", "subtype:cash",
     "required", "none"),
    ("salary_accrue", "Salaries owed for the month", "إثبات الرواتب المستحقة", "Payroll Accrual", "type:Expense,COGS",
     "role:accrued_payroll", "none", "optional"),
]
MAX_LINES = 20
MAX_MODELS = 300
ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")
SIDE_RE = re.compile(r"^(|[\w.-]{1,20}|role:[a-z_]+|subtype:[a-z_,]+|type:[A-Za-z,]+)$")


def defaults():
    return [{"id": i, "name": n, "name_ar": ar, "kind": k, "debit": dr, "credit": cr, "party": pa, "bu": bu,
             "active": True, "builtin": True} for i, n, ar, k, dr, cr, pa, bu in BUILTIN]


def load(db):
    raw = (db.setting("entry_types") or "").strip()
    try:
        removed = set(json.loads(db.setting("entry_types_removed") or "[]"))
    except ValueError:
        removed = set()
    try:
        types = json.loads(raw) if raw else []
    except ValueError:
        types = []
    have = {t.get("id") for t in types}
    # built-ins added by later versions appear unless they were removed on purpose
    return types + [t for t in defaults() if t["id"] not in have and t["id"] not in removed]


def resolve_side(db, spec, accounts=None):
    """{"fixed": code} or {"choose": [codes], "default": code} for one side of an entry type."""
    accounts = accounts if accounts is not None else db.q("SELECT code, type, subtype, role, active FROM accounts ORDER BY code")
    live = [a for a in accounts if a["active"]]
    spec = (spec or "").strip()
    if spec.startswith("role:"):
        a = next((a for a in accounts if a["role"] == spec[5:]), None)
        return {"fixed": a["code"]} if a else {"choose": [a["code"] for a in live], "default": "", "missing": spec[5:]}
    if spec.startswith("subtype:"):
        subs = spec[8:].split(",")
        codes = [a["code"] for a in live if a["subtype"] in subs]
        return {"choose": codes, "default": codes[0] if codes else ""}
    if spec.startswith("type:"):
        types = spec[5:].split(",")
        codes = [a["code"] for a in live if a["type"] in types]
        return {"choose": codes, "default": codes[0] if codes else ""}
    if spec:
        return {"fixed": spec} if any(a["code"] == spec for a in accounts) else {"choose": [a["code"] for a in live], "default": ""}
    return {"choose": [a["code"] for a in live], "default": ""}


def used(db):
    """When each model was last used ({id: ISO time}), company-wide: the lists show the most recently used first."""
    try:
        u = json.loads(db.setting("entry_types_used") or "{}")
        return u if isinstance(u, dict) else {}
    except ValueError:
        return {}


def touch(db, tid):
    """A model was just used to post an entry."""
    if not tid or not any(t["id"] == tid for t in load(db)):
        return
    from .db import now
    u = used(db)
    # the app's clock (the same on every device a change is replayed on), then a running count, so two uses in the same
    # second still keep their order
    n = max((int(v.rsplit("#", 1)[1]) for v in u.values() if isinstance(v, str) and "#" in v), default=0) + 1
    u[tid] = f"{now().isoformat()}#{n:08d}"
    db.set_setting("entry_types_used", json.dumps(u))


def for_client(db):
    """Entry types with each side resolved to account codes (what the forms need), most recently used first."""
    accounts = db.q("SELECT code, type, subtype, role, active FROM accounts ORDER BY code")
    u = used(db)
    out = []
    for t in load(db):
        row = {**t, "dr": resolve_side(db, t["debit"], accounts), "cr": resolve_side(db, t["credit"], accounts),
               "used_at": u.get(t["id"], "")}
        if t.get("lines"):
            row["lines"] = [{**ln, "res": resolve_side(db, ln["account"], accounts)} for ln in t["lines"]]
        out.append(row)
    return sorted(out, key=lambda t: t["used_at"], reverse=True)       # stable: never-used keep their order


def _clean_model_lines(name, lines):
    """A model's own lines: each an account (or a choice, as for a side), debit or credit, and optionally an amount and
    a party. Amounts left empty are filled in when the model is used."""
    if lines in (None, "", []):
        return None
    if not isinstance(lines, list):
        raise ValidationError(f"{name}: lines must be a list")
    out = []
    for i, ln in enumerate(lines, start=1):
        if not isinstance(ln, dict):
            continue
        spec = text(ln.get("account"))
        if not SIDE_RE.match(spec):
            raise ValidationError(f"{name}: line {i} has an account setting that is not valid")
        side = text(ln.get("side")).lower()
        if side not in ("debit", "credit"):
            raise ValidationError(f"{name}: line {i} must be a debit or a credit")
        amount = ln.get("amount")
        amount = None if amount in (None, "") else acc.num(amount, f"{name}: line {i} amount")
        if amount is not None and amount < 0:
            raise ValidationError(f"{name}: line {i} amount cannot be negative")
        out.append({"account": spec, "side": side, "amount": amount, "party": text(ln.get("party"), maxlen=120)})
    if len(out) < 2:
        raise ValidationError(f"{name}: a model needs at least two lines")
    if len(out) > MAX_LINES:
        raise ValidationError(f"{name}: at most {MAX_LINES} lines")
    if not any(ln["side"] == "debit" for ln in out) or not any(ln["side"] == "credit" for ln in out):
        raise ValidationError(f"{name}: a model needs a debit line and a credit line")
    return out


def save(db, types):
    if not isinstance(types, list):
        raise ValidationError("Entry types must be a list")
    out, seen = [], set()
    for t in types:
        tid = text(t.get("id")).lower() or re.sub(r"[^a-z0-9]+", "_", text(t.get("name")).lower()).strip("_")[:40]
        if not ID_RE.match(tid or ""):
            raise ValidationError(f"Bad entry type id {tid!r}")
        if tid in seen:
            tid = f"{tid}_{len(seen)}"
        seen.add(tid)
        name = text(t.get("name"), "entry type name", required=True, maxlen=80)
        lines = _clean_model_lines(name, t.get("lines"))
        if lines:                                   # a model with its own lines: the two sides are its first debit and credit
            t = {**t, "debit": next(ln["account"] for ln in lines if ln["side"] == "debit"),
                 "credit": next(ln["account"] for ln in lines if ln["side"] == "credit")}
        for side in ("debit", "credit"):
            if not SIDE_RE.match(text(t.get(side))):
                raise ValidationError(f"{name}: bad {side} account setting")
        row = {"id": tid, "name": name, "name_ar": text(t.get("name_ar"), maxlen=80),
               "kind": text(t.get("kind"), maxlen=60) or name, "debit": text(t.get("debit")), "credit": text(t.get("credit")),
               "party": t.get("party") if t.get("party") in ("none", "optional", "required") else "optional",
               "bu": t.get("bu") if t.get("bu") in ("none", "optional", "required") else "optional",
               "active": t.get("active") not in (False, 0, "0", "false"),
               "builtin": tid in {b[0] for b in BUILTIN}}
        if lines:
            row["lines"] = lines
        for k, n in (("memo", 300), ("party_name", 120), ("bu_code", 40), ("by", 80), ("created_at", 40)):
            if text(t.get(k)):
                row[k] = text(t.get(k), maxlen=n)
        out.append(row)
    if len(out) > MAX_MODELS:
        raise ValidationError(f"At most {MAX_MODELS} entry models")
    removed = sorted({b[0] for b in BUILTIN} - {t["id"] for t in out})
    db.set_setting("entry_types", json.dumps(out, ensure_ascii=False))
    db.set_setting("entry_types_removed", json.dumps(removed))
    return out


def add_model(db, d, user=""):
    """Save an entry as a model, from the journal form: its lines (accounts, debit or credit, amounts only when asked),
    and optionally its narration, party and business unit. Company-wide; a model with the same name is replaced."""
    name = text(d.get("name"), "model name", required=True, maxlen=80)
    keep = d.get("keep_amounts") in (True, 1, "1", "true", "on")
    lines = []
    for ln in d.get("lines") or []:
        if not isinstance(ln, dict) or not text(ln.get("account")):
            continue
        if not db.one("SELECT 1 FROM accounts WHERE code=?", (text(ln.get("account")),)):
            raise ValidationError(f"Unknown account {text(ln.get('account'))}")
        lines.append({"account": text(ln.get("account")), "side": text(ln.get("side")).lower(),
                      "amount": ln.get("amount") if keep and ln.get("amount") not in (None, "", 0, "0") else None,
                      "party": text(ln.get("party"))})
    types = load(db)
    same = next((t for t in types if t["name"].strip().lower() == name.lower() and not t.get("builtin")), None)
    tid = same["id"] if same else "m_" + (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30] or "model")
    base, n = tid, 1
    while not same and any(t["id"] == tid for t in types):
        n += 1
        tid = f"{base[:34]}_{n}"
    row = {"id": tid, "name": name, "name_ar": text(d.get("name_ar"), maxlen=80), "kind": text(d.get("kind"), maxlen=60) or "Journal Voucher",
           "lines": lines, "party": "optional", "bu": "optional", "active": True, "by": user, "created_at": acc.now_iso()[:10]}
    if d.get("keep_details") in (True, 1, "1", "true", "on"):
        row.update({"memo": text(d.get("memo")), "party_name": text(d.get("party")), "bu_code": text(d.get("bu_code"))})
    out = save(db, [row if t["id"] == tid else t for t in types] + ([] if same else [row]))
    touch(db, tid)
    return next(t for t in out if t["id"] == tid)


def delete_model(db, tid):
    """Remove a model for the whole company. A built-in one can be brought back from Settings → Menus → Entry types."""
    types = load(db)
    if not any(t["id"] == tid for t in types):
        raise ValidationError("That model was already removed")
    save(db, [t for t in types if t["id"] != tid])
    u = used(db)
    if u.pop(tid, None):
        db.set_setting("entry_types_used", json.dumps(u))


# ---------------------------------------------------------------- the workbook: one row per model line
def sheet_rows(db):
    """Models made or changed by the company, for the "Entry Models" sheet (built-ins as shipped are not listed)."""
    base = {t["id"]: t for t in defaults()}
    rows = []
    for t in load(db):
        b = base.get(t["id"])
        if b and all(t.get(k) == b[k] for k in ("name", "name_ar", "kind", "debit", "credit", "party", "bu", "active")) and not t.get("lines"):
            continue
        lines = t.get("lines") or [{"account": t["debit"], "side": "debit", "amount": None, "party": ""},
                                   {"account": t["credit"], "side": "credit", "amount": None, "party": ""}]
        for i, ln in enumerate(lines, start=1):
            rows.append({"model": t["id"], "name": t["name"], "name_ar": t.get("name_ar", ""), "kind": t["kind"], "line": i,
                         "side": ln["side"].title(), "account": ln["account"], "amount": ln.get("amount"), "line_party": ln.get("party", ""),
                         "party": t.get("party", "optional"), "bu": t.get("bu", "optional"), "active": "Yes" if t.get("active", True) else "No",
                         "memo": t.get("memo", ""), "party_name": t.get("party_name", ""), "bu_code": t.get("bu_code", "")})
    return rows


def import_sheet(db, rows):
    """The "Entry Models" sheet back in: each model replaces the one with the same id (others stay). Two-line models
    without amounts keep the simple two-sided form; the rest keep their lines."""
    by = {}
    for r in rows or []:
        mid = text(r.get("model")) or re.sub(r"[^a-z0-9]+", "_", text(r.get("name")).lower()).strip("_")[:40]
        if not mid:
            continue
        by.setdefault(mid, []).append(r)
    if not by:
        return 0
    types = {t["id"]: t for t in load(db)}
    for mid, rs in by.items():
        rs = sorted(rs, key=lambda r: acc.num(r.get("line") or 0, "line"))
        h = rs[0]
        lines = [{"account": text(r.get("account")), "side": text(r.get("side")).lower(), "amount": r.get("amount"),
                  "party": text(r.get("line_party"))} for r in rs]
        simple = len(lines) == 2 and lines[0]["side"] == "debit" and lines[1]["side"] == "credit" and not any(
            ln["amount"] not in (None, "") or ln["party"] for ln in lines)
        row = {"id": mid, "name": text(h.get("name")) or mid, "name_ar": text(h.get("name_ar")), "kind": text(h.get("kind")),
               "party": text(h.get("party")) or "optional", "bu": text(h.get("bu")) or "optional",
               "active": text(h.get("active")).lower() not in ("no", "false", "0"),
               "memo": text(h.get("memo")), "party_name": text(h.get("party_name")), "bu_code": text(h.get("bu_code"))}
        if simple:
            row.update(debit=lines[0]["account"], credit=lines[1]["account"])
        else:
            row["lines"] = lines
        types[mid] = row
    save(db, list(types.values()))
    return len(by)


def kinds(db):
    return [t["kind"] for t in load(db)]


def post(db, tid, d, user=""):
    t = next((t for t in load(db) if t["id"] == tid), None)
    if not t:
        raise ValidationError("Unknown entry type")
    if not t.get("active", True):
        raise ValidationError(f"“{t['name']}” is switched off in Settings")
    amount = acc.num(d.get("amount"), "amount", allow_zero=False)
    codes = {}
    for side in ("debit", "credit"):
        r = resolve_side(db, t[side])
        if "fixed" in r:
            codes[side] = r["fixed"]
        else:
            chosen = text(d.get(side + "_account"))
            if r.get("missing"):
                raise ValidationError(f"No account holds the '{r['missing']}' role. Assign it in the chart of accounts.")
            if chosen not in r["choose"]:
                raise ValidationError(f"Choose the {side} account")
            codes[side] = chosen
    party = text(d.get("party"))
    if t["party"] == "required" and not party:
        raise ValidationError("Enter the party")
    bu = acc.bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=t["bu"] == "required")
    return acc.post_entry(db, date=d.get("date"), kind=t["kind"], memo=text(d.get("memo")) or t["name"], bu_id=bu,
                          party=party if t["party"] != "none" else "", ref=text(d.get("ref")),
                          lines=[{"account": codes["debit"], "debit": amount}, {"account": codes["credit"], "credit": amount}],
                          meta={"entry_type": t["id"]}, user=user)


# ---------------------------------------------------------------- "+ New" menu
# key, English, Arabic, permission needed (module, level)
MENU_ITEMS = [
    ("doc:direct_cost", "Direct Costs (Cost of Revenue)", "التكاليف المباشرة", "purchasing.costs"),
    ("doc:expense", "Operating Expenses", "المصروفات التشغيلية", "purchasing.expenses"),
    ("doc:payment", "Payment Vouchers", "سندات الصرف", "purchasing.payments"),
    ("doc:receipt", "Receipt Vouchers", "سندات القبض", "sales.receipts"),
    ("doc:invoice", "Sales Invoices / Progress Billings", "فواتير المبيعات / المستخلصات", "sales.invoices"),
    ("doc:payroll", "Payroll", "الأجور", "payroll"),
    ("doc:stock_move", "Stock Movements", "حركات المخزون", "stock.moves"),
    ("doc:journal", "Journal Vouchers", "قيود اليومية", "accounting.journal"),
    ("doc:bu", "Business Units (Cost Centres)", "وحدات الأعمال", "business_units.units"),
    ("doc:offer", "Offers", "العروض", "sales.offers"),
    ("eq:contribute", "Capital contribution", "زيادة رأس المال (مساهمة شريك)", "partners.transactions"),
    ("eq:withdraw", "Capital withdrawal", "تخفيض رأس المال (سحب شريك)", "partners.transactions"),
    ("eq:loan", "Loan from a partner", "قرض من شريك", "partners.transactions"),
    ("eq:repay", "Repay a partner's loan / interest", "سداد قرض شريك / فوائده", "partners.transactions"),
    ("eq:interest", "Interest on a partner's loan", "فوائد قرض شريك", "partners.transactions"),
    ("eq:drawing", "Partner drawing", "مسحوبات شريك", "partners.transactions"),
    ("eq:distribute", "Profit distribution", "توزيع أرباح", "partners.transactions"),
    ("eq:transfer", "Transfer / sell a share", "تنازل / بيع حصة", "partners.ownership"),
    ("eq:admit", "Admit a new partner", "دخول شريك جديد", "partners.ownership"),
    ("eq:exit", "Partner retirement / buy-out", "تخارج شريك", "partners.ownership"),
    ("asset:new", "New fixed asset", "أصل ثابت جديد", "assets.register"),
    ("asset:depr", "Run depreciation", "احتساب الإهلاك", "assets.depreciation"),
    ("po:new", "New purchase order", "أمر شراء جديد", "purchasing.orders"),
    ("sub:new", "New subscription / maintenance contract", "اشتراك / عقد صيانة جديد", "shop.subscriptions"),
    ("loan:new", "New loan (taken or given)", "قرض جديد (مأخوذ أو ممنوح)", "accounting.loans"),
    ("loan:pay", "Loan payment / collection", "سداد / تحصيل قسط قرض", "accounting.loans"),
    ("custody:new", "Give cash custody", "صرف عهدة نقدية", "purchasing.custody"),
    ("sale:new", "New sale (cash / installments)", "بيع جديد (كاش / تقسيط)", "shop.orders"),
    ("sale:collect", "Collect an installment", "تحصيل قسط", "shop.installments"),
    ("product:new", "New product / unit", "منتج / وحدة جديدة", "shop.products"),
    ("recurring:new", "New recurring entry (rent, salaries, subscriptions…)", "قيد متكرر جديد (إيجار، رواتب، اشتراكات…)", "accounting.recurring"),
    ("bank:import", "Import a bank statement", "استيراد كشف حساب بنك", "accounting.bankrec"),
    ("contact:new", "New contact (supplier, subcontractor, driver…)", "جهة اتصال جديدة (مورد، مقاول، سائق…)", "purchasing.contacts"),
    ("custody:settle", "Custody expense report (settlement)", "تسوية عهدة (تقرير مصروفات)", "purchasing.custody"),
    ("site:task", "New project task", "مهمة مشروع جديدة", "projects.tasks"),
    ("site:contract", "New contract (client)", "عقد جديد (عميل)", "contracts.contracts"),
    ("site:subcontract", "New subcontract", "عقد مقاول باطن جديد", "contracts.contracts"),
    ("site:prq", "New purchase requisition", "طلب شراء جديد", "purchasing.requests"),
    ("site:usage", "Equipment hours / fuel", "ساعات تشغيل / وقود معدة", "equipment.usage"),
    ("site:maint", "Equipment maintenance done", "صيانة معدة تمت", "equipment.maintenance"),
    ("site:attendance", "Today's attendance", "حضور اليوم", "payroll.attendance"),
    ("site:ereceipt", "New e-receipt", "إيصال إلكتروني جديد", "tax.ereceipt"),
]
DEFAULT_MENU = ["po:new", "doc:direct_cost", "doc:expense", "doc:payment", "doc:receipt", "doc:invoice", "doc:payroll",
                "doc:stock_move", "doc:journal", "eq:contribute", "eq:distribute", "asset:new", "asset:depr", "loan:new", "loan:pay",
                "custody:new", "custody:settle", "contact:new", "sale:new", "sale:collect", "sub:new", "doc:bu", "doc:offer", "site:prq", "site:task", "site:subcontract", "site:usage", "site:attendance"]


def menu(db):
    """The "+ New" menu: [{key, show}] in order. Items not placed yet are listed after, hidden."""
    try:
        saved = json.loads(db.setting("new_menu") or "[]")
    except ValueError:
        saved = []
    if not saved:
        saved = [{"key": k, "show": True} for k in DEFAULT_MENU]
    keys = [m["key"] for m in saved]
    extra = [k for k, *_ in MENU_ITEMS if k not in keys] + [f"entry:{t['id']}" for t in load(db) if f"entry:{t['id']}" not in keys]
    return saved + [{"key": k, "show": False} for k in extra]


def save_menu(db, items):
    known = {k for k, *_ in MENU_ITEMS} | {f"entry:{t['id']}" for t in load(db)}
    out = [{"key": m["key"], "show": bool(m.get("show"))} for m in items or [] if isinstance(m, dict) and m.get("key") in known]
    db.set_setting("new_menu", json.dumps(out))
    return out
