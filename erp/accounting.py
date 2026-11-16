"""Business logic: double-entry posting, operational documents and statements.

Every money movement ends up as one balanced journal entry. Documents (costs,
payroll, collections, invoices, stock moves...) are thin helpers that build
the right debit/credit lines, so every report comes from one source of truth.

System accounts are looked up by *role* (db.acct('bank')), never by a
hard-coded number, so the chart of accounts can be renumbered freely.
"""
import copy
import datetime as _dt
import functools
import json
import collections
import re
import weakref

from .db import SUBTYPES, normal_for, tax_of, to_major, to_minor

BILLING_BASES = ["Cost-Plus %", "Fixed Price", "Flexible / Settled", "Live Fee (Paid vs Costs)"]
# "Live Fee": nothing is billed ahead — the fee is whatever the client has actually paid so far over what the project has
# actually cost so far (see bu_summaries: client_paid, actual_costs, live_fee)
LIVE_FEE = "Live Fee (Paid vs Costs)"
PAYMENT_METHODS = ["Bank Transfer", "Cash", "Cheque", "Credit Card", "Instapay"]
PAYROLL_TYPES = {  # cost type -> (account role, business unit required?)
    "Direct Labour (Cost of Revenue)": ("direct_labour", True),
    "Indirect Labour (Cost-Centre Overhead)": ("project_overhead", True),
    "Administrative Salaries (Shared Overhead)": ("admin_salaries", False),
}
STOCK_MOVE_TYPES = ["Receipt", "Issue", "Transfer", "Adjustment"]
ENTRY_KINDS = ["Direct Cost", "Operating Expense", "Payroll Accrual", "Payment Voucher", "Supplier Discount",
               "Receipt Voucher", "Sales Invoice", "Credit Note", "Debit Note", "Capital Contribution", "Capital Withdrawal", "Owner Drawing", "Partner Loan", "Partner Loan Repayment",
               "Partner Loan Interest", "Share Transfer", "Partner Admission", "Partner Exit", "Profit Distribution",
               "Reserve Transfer", "Cash Transfer", "Asset Purchase", "Depreciation", "Asset Disposal", "Journal Voucher",
               "Goods Receipt", "Material Issue", "Stock Transfer", "Stock Adjustment", "Stock Cost Adjustment", "Reversing Entry",
               "Loan Received", "Loan Repayment", "Loan Given", "Loan Collection", "Loan Interest", "Loan Charges",
               "Loan Forgiven", "Loan Written Off", "Custody Issued", "Custody Settlement", "Custody Return",
               "Custody Deduction", "Rental Equipment Purchase", "Rental Equipment Loss", "Rental Equipment Depreciation",
               "Rental Deposit", "Installment Interest", "Advance Applied", "Cost of Sales", "Cheque Received",
               "Cheque Cleared", "Cheque Bounced", "Sale Refund", "Bank Entry", "FX Revaluation",
               "Subcontractor Certificate", "Equipment Usage", "Equipment Maintenance", "E-Receipt"]


class ValidationError(Exception):
    """Bad user input; the API turns it into a 400 with this message."""


# ------------------------------------------------------------------------ utils
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def today():
    from .db import now
    return now().date().isoformat()


def now_iso():
    from .db import now
    return now().isoformat(timespec="seconds")


def clean_date(value, field="date"):
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    value = ("" if value is None else str(value)).strip()
    if not value:
        return today()
    value = value[:10]
    if not DATE_RE.match(value):
        raise ValidationError(f"{field} must be YYYY-MM-DD")
    try:
        _dt.date.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"{field} is not a real date") from None
    if not "1900" <= value[:4] <= "2199":      # 0206 or 2206 for 2026: a typing slip, not a date in the books
        raise ValidationError(f"{field}: the year {value[:4]} looks mistyped")
    return value


def num(value, field="amount", allow_zero=True, allow_negative=False):
    if value in (None, ""):
        value = 0
    try:
        v = float(str(value).replace(",", "")) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a number") from None
    if v != v or v in (float("inf"), float("-inf")):
        raise ValidationError(f"{field} must be a number")
    if not allow_negative and v < 0:
        raise ValidationError(f"{field} cannot be negative")
    if not allow_zero and v == 0:
        raise ValidationError(f"{field} must be greater than zero")
    return v


def text(value, field=None, required=False, maxlen=500):
    v = ("" if value is None else str(value)).strip()
    if required and not v:
        raise ValidationError(f"{field or 'value'} is required")
    return v[:maxlen]


def is_cash_method(method):
    return (method or "").strip().lower() == "cash"


_CU_PAY = {"c": None}     # set by money_account("CU:<code>"); the next entry posted is tagged to that custody


def money_account(db, method=None, explicit=None):
    """Where money goes in/out: an explicit cash/bank account (or the funding partner's account), else by payment
    method — and when the company has a funding partner, his account is where its money comes from and goes to.
    "CU:<code>" (as the account or the method) is money an employee holds as custody: the payment comes out of it."""
    for v in (explicit, method):
        if isinstance(v, str) and v.startswith("CU:"):
            from . import custody
            return custody.pay_source(db, v[3:])
    if explicit:
        row = db.one("SELECT role FROM accounts WHERE code=?", (text(explicit),))
        from .funders import is_role
        if row and is_role(row["role"]):               # any funding partner's account
            return account_code(db, explicit)
        return account_code(db, explicit, subtypes=("cash", "short_term_debt"))
    if (db.setting("funding_partner") or "").strip():
        f = db.one("SELECT code FROM accounts WHERE role='funding_partner' AND active=1")
        if f:
            return f["code"]
    return db.acct("cash" if is_cash_method(method) else "bank")


def account_code(db, value, types=None, subtypes=None, field="account"):
    code = text(value)
    row = db.one("SELECT * FROM accounts WHERE code=?", (code,))
    if not row:
        raise ValidationError(f"Unknown {field} {code!r}")
    if not row["active"]:
        raise ValidationError(f"Account {code} is inactive")
    if types and row["type"] not in types:
        raise ValidationError(f"{field} {code} must be a {' / '.join(types)} account")
    if subtypes and row["subtype"] not in subtypes:
        raise ValidationError(f"{field} {code} is not a valid choice here")
    return code


def bu_id_or_none(db, value, required=False):
    if value in (None, "", 0, "0"):
        if required:
            raise ValidationError("Choose a business unit")
        return None
    row = db.one("SELECT id FROM business_units WHERE code=? OR (id=? AND ? GLOB '[0-9]*')",
                 (str(value), value, str(value)))
    if not row:
        raise ValidationError(f"Unknown business unit: {value}")
    return row["id"]


# ------------------------------------------------------------------------ posting
def _validate_lines(db, lines):
    if not lines or len(lines) < 2:
        raise ValidationError("An entry needs at least two lines")
    accounts = {r["code"]: r for r in db.q("SELECT code, active, role FROM accounts")}
    from .funders import is_role, name_for_account
    funder = {}
    out, dr, cr = [], 0, 0
    touched = set()
    extra = []
    known_items = None
    for ln in lines:
        acc = text(ln.get("account"))
        if not acc:
            raise ValidationError("A line has no account. Enter the account code.")
        if acc not in accounts:
            raise ValidationError(f"Unknown account {acc!r}")
        if not accounts[acc]["active"]:
            raise ValidationError(f"Account {acc} is deactivated. Reactivate it in the chart of accounts to post to it.")
        d = to_minor(num(ln.get("debit"), "debit"))
        c = to_minor(num(ln.get("credit"), "credit"))
        if d and c:
            raise ValidationError("A line cannot have both a debit and a credit")
        if not d and not c:
            continue
        party = text(ln.get("party"))
        if not party and is_role(accounts[acc]["role"]):
            # money through a funding partner's account is always his
            if acc not in funder:
                funder[acc] = name_for_account(db, acc)
            party = funder[acc]
        item = text(ln.get("cost_item"), maxlen=40)
        if item:
            if known_items is None:
                known_items = {r["code"] for r in db.q("SELECT code FROM cost_items")}
            if item not in known_items:
                raise ValidationError(f"Unknown cost item {item}. Add it under Cost items first.")
        out.append((acc, d, c, party))
        cur = text(ln.get("currency"), maxlen=3).upper()
        fx_amt = to_minor(num(ln.get("fx_amount"), "foreign amount")) if cur else 0
        try:
            fx_rate = float(ln.get("fx_rate") or 0) if cur else 0.0
        except (TypeError, ValueError):
            fx_rate = 0.0
        settles = int(num(ln.get("settles") or 0)) if cur and str(ln.get("settles") or "").strip().isdigit() else 0
        extra.append((text(ln.get("memo") or ln.get("line_memo"), maxlen=500), item, cur, abs(fx_amt), fx_rate, settles))
        touched.add((acc, party))
        dr += d
        cr += c
    if dr == 0:
        raise ValidationError("Entry total must be greater than zero")
    if len(touched) == 1:
        raise ValidationError("Debit and credit are on the same account. Choose the source and destination accounts.")
    if dr != cr:
        raise ValidationError(f"Debits ({to_major(dr):,.2f}) must equal credits ({to_major(cr):,.2f})")
    return _Lines(out, extra)


class _Lines(list):
    """Validated lines (account, debit, credit, party), with each line's description and cost item alongside."""
    def __init__(self, rows, extra):
        super().__init__(rows)
        self.extra = extra


def _insert_lines(db, eid, vlines):
    extra = getattr(vlines, "extra", None) or [("", "", "", 0, 0.0, 0)] * len(vlines)
    db.conn.executemany("INSERT INTO journal_lines(entry_id,account,debit,credit,party,memo,cost_item,currency,fx_amount,fx_rate,settles) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        [(eid, a, d, c, p, *x[:2], *(tuple(x[2:5]) if len(x) >= 5 else ("", 0, 0.0)), x[5] if len(x) >= 6 else 0)
                         for (a, d, c, p), x in zip(vlines, extra)])


def check_open(db, date):
    """Refuse changes dated on or before the lock date ("books closed up to")."""
    lock = (db.setting("lock_date") or "").strip()
    if lock and str(date or "") <= lock:
        raise ValidationError(f"The books are closed up to {lock}. Change the lock date in Settings to post or edit on {date}.")


# set by replace_doc(): the next posted entry overwrites this one instead of adding a new entry
_REPLACE = {"eid": None}


# Set by the server for a request of someone whose work waits for approval: nothing of theirs reaches the books
# directly, whatever screen it comes from (the system's own bookkeeping, such as work in progress, still runs).
import contextvars as _cv
POSTING_HELD = _cv.ContextVar("posting_held", default=False)
# The currency a form was filled in (erp/fx.py): every entry its request posts is converted from it, except the
# system's own entries (stock costs, depreciation, work in progress, reversals…), which are always in the company's.
DOC_FX = _cv.ContextVar("doc_fx", default=None)
HOME_KINDS = {"Material Issue", "Stock Transfer", "Stock Cost Adjustment", "Stock Adjustment", "Work in Progress", "Depreciation",
              "Rental Equipment Depreciation", "Rental Equipment Cost Adjustment", "Reversing Entry", "FX Revaluation", "E-Receipt",
              "Equipment Usage", "Payroll Accrual", "Salary Advance", "Year-End Close"}


class in_currency:
    """`with in_currency("USD", 52):` — entries posted inside are in that currency (blank / None: the company's)."""
    def __init__(self, currency, rate=None):
        cur = text(currency, maxlen=3).upper() if currency else ""
        self.val = {"currency": cur, "rate": rate if rate not in ("", 0, "0") else None} if cur else None

    def __enter__(self):
        self.tok = DOC_FX.set(self.val)
        return self

    def __exit__(self, *a):
        DOC_FX.reset(self.tok)
        return False
# Going over a limit (a contract, a bill of quantities, a purchase request): asked once, done when confirmed
OVER_OK = _cv.ContextVar("over_ok", default=False)


class NeedsConfirm(ValidationError):
    """Allowed, but only once the person confirms (the screen asks and sends it again with confirm_over)."""


def over_limit(msg):
    from .db import CLOCK
    if OVER_OK.get() or CLOCK["at"] is not None:      # confirmed, or a change replayed from the shared log
        return
    raise NeedsConfirm(msg)


def _fx_lines(db, date, lines, party, currency=None, rate=None, exclude_eid=None):
    """Foreign amounts → the company's currency, with the exchange difference (erp/fx.py); other lines unchanged."""
    if currency and currency.upper() == (db.setting("currency") or "EGP").upper():
        currency = None
    fx_accounts = {r["code"] for r in db.q("SELECT code FROM accounts WHERE currency<>''")}
    if not currency:
        lines = [{k: v for k, v in l.items() if k != "_home"} if "_home" in l else l for l in lines]
    if currency or any(l.get("fx_debit") or l.get("fx_credit") or l.get("currency") or text(l.get("account")) in fx_accounts for l in lines):
        from . import fx
        return fx.convert(db, clean_date(date), lines, text(party), currency, rate, exclude_eid=exclude_eid)
    return lines


def _insert_entry(db, *, date, kind, memo="", bu_id=None, party="", ref="", lines, meta=None,
                  reversal_of=None, user="", currency=None, rate=None):
    """Insert a balanced entry. Caller must hold a transaction (db.tx())."""
    if POSTING_HELD.get() and user != "system":
        from .textl10n import Note
        raise PermissionError(Note("Your entries need approval before they reach the books. Save it as a draft or voucher for approval, "
                                   "or ask someone with posting rights to post it.",
                                   "قيودك تحتاج إلى اعتماد قبل ترحيلها. احفظها كمسودة أو كقيد للاعتماد، أو اطلب من شخص لديه صلاحية الترحيل ترحيلها."))
    cu, _CU_PAY["c"] = _CU_PAY["c"], None
    if cu and not str(ref or "").startswith("CU:") and any(text(l.get("account")) == cu["account"] for l in lines):
        # paid from (or into) a custody: the holder's line, and the entry belongs on that custody's statement
        lines = [dict(l, party=l.get("party") or cu["holder"]) if text(l.get("account")) == cu["account"] else l for l in lines]
        meta = {**(meta or {}), "paid_by_custody": cu["code"]}
    check_open(db, clean_date(date))
    if currency is None and reversal_of is None and kind not in HOME_KINDS and DOC_FX.get():
        currency, rate = DOC_FX.get()["currency"], DOC_FX.get()["rate"]
    lines = _fx_lines(db, date, lines, party, currency, rate, _REPLACE["eid"])
    vlines = _validate_lines(db, lines)
    if _REPLACE["eid"]:
        eid, _REPLACE["eid"] = _REPLACE["eid"], None
        e = get_entry(db, eid)
        snap = {k: e[k] for k in ("date", "memo", "bu_code", "party", "lines")}
        snap.update(ref=e["ref"], edited_at=now_iso(), edited_by=user)
        db.conn.execute("UPDATE journal_entries SET date=?, kind=?, memo=?, bu_id=?, party=?, ref=?, meta=?, history=? "
                        "WHERE id=?", (clean_date(date), kind, text(memo, maxlen=1000), bu_id, text(party), text(ref),
                                       json.dumps(meta or {}, ensure_ascii=False),
                                       json.dumps(e["history"] + [snap], ensure_ascii=False), eid))
        db.conn.execute("DELETE FROM journal_lines WHERE entry_id=?", (eid,))
        _insert_lines(db, eid, vlines)
        _wip_after(db, kind, bu_id, e.get("bu_id"))
        return eid
    cur = db.conn.execute(
        "INSERT INTO journal_entries(date,kind,memo,bu_id,party,ref,meta,reversal_of,created_by,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (clean_date(date), kind, text(memo, maxlen=1000), bu_id, text(party), text(ref),
         json.dumps(meta or {}, ensure_ascii=False), reversal_of, user or "", now_iso().replace("T", " ")))
    eid = cur.lastrowid
    _insert_lines(db, eid, vlines)
    _wip_after(db, kind, bu_id)
    return eid


def _wip_after(db, kind, *bu_ids):
    """A project's costs or invoices changed: bring its work in progress up to date (not for that entry itself)."""
    if kind == "Work in Progress":
        return
    from . import wip
    for b in {x for x in bu_ids if x}:
        wip.sync(db, b)


def owed_in(db, account, party, d):
    """What is owed to a party on a liability account, in the form's currency: (amount, " USD" or "")."""
    cur = (fx_of(d).get("currency") or (DOC_FX.get() or {}).get("currency") or "").upper()
    if cur and cur != (db.setting("currency") or "EGP").upper():
        from . import fx
        return -to_major(fx.carried(db, account, party, cur, clean_date(d.get("date")))[0]), f" {cur}"
    return party_balance(db, account, party), ""


def fx_of(d):
    """A form's currency and rate (erp/fx.py): its amounts are in that currency; blank = the company's."""
    cur = text(d.get("currency"), maxlen=3).upper()
    return {"currency": cur, "rate": d.get("rate_fx") if d.get("rate_fx") not in (None, "") else d.get("fx_rate")} if cur else {}


def post_entry(db, **kw):
    with db.tx():
        return _insert_entry(db, **kw)


def possible_duplicates(db, eid, days=3):
    """Posted entries that look like this one was already recorded: the same kind and party (or the same description
    when there is no party), the same total, dated within `days` of it — or the same kind, party and reference (e.g.
    the supplier's invoice number) on any date."""
    e = db.one("SELECT e.*, (SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id) amt FROM journal_entries e WHERE e.id=?", (int(eid),))
    if not e or not e["amt"]:
        return []
    d = _dt.date.fromisoformat(e["date"])
    lo, hi = (d - _dt.timedelta(days=days)).isoformat(), (d + _dt.timedelta(days=days)).isoformat()
    who = ("e.party=?", e["party"]) if e["party"] else ("LOWER(TRIM(e.memo))=LOWER(TRIM(?)) AND e.party=''", e["memo"] or "")
    if not e["party"] and not (e["memo"] or "").strip():
        return []
    ref = "OR (e.kind=? AND e.party<>'' AND e.party=? AND e.ref<>'' AND e.ref=?)" if e["party"] and (e["ref"] or "").strip() else ""
    rows = db.q(f"""SELECT e.id, e.date, e.kind, e.memo, e.party, e.ref, e.created_by, e.created_at, b.code bu_code,
                          (SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id) amt
                   FROM journal_entries e LEFT JOIN business_units b ON b.id=e.bu_id
                   WHERE {LIVE} AND e.id<>? AND ((e.kind=? AND e.date BETWEEN ? AND ? AND {who[0]}
                         AND (SELECT SUM(debit) FROM journal_lines WHERE entry_id=e.id)=?) {ref})
                   ORDER BY e.date DESC, e.id DESC LIMIT 5""",
                [e["id"], e["kind"], lo, hi, who[1], e["amt"]] + ([e["kind"], e["party"], e["ref"]] if ref else []))
    return [{**r, "amount": to_major(r.pop("amt") or 0), "same_ref": bool(ref) and r["ref"] == e["ref"]} for r in rows]


def get_entry(db, eid):
    e = db.one("SELECT e.*, b.code AS bu_code, (SELECT COUNT(*) FROM attachments WHERE owner='entry' AND owner_id=e.id) AS files "
               "FROM journal_entries e LEFT JOIN business_units b ON b.id=e.bu_id WHERE e.id=?", (eid,))
    if not e:
        return None
    e["meta"] = json.loads(e["meta"] or "{}")
    e["history"] = json.loads(e["history"] or "[]")
    e["lines"] = [{"account": r["account"], "name": r["name"], "name_ar": r["name_ar"], "debit": to_major(r["debit"]),
                   "credit": to_major(r["credit"]), **({"party": r["party"]} if r["party"] else {}),
                   **({"memo": r["memo"]} if r["memo"] else {}), **({"cost_item": r["cost_item"]} if r["cost_item"] else {}),
                   **({"currency": r["currency"], "fx_amount": to_major(r["fx_amount"]), "fx_rate": r["fx_rate"], **({"settles": r["settles"]} if r.get("settles") else {})} if r["currency"] else {})}
                  for r in db.q("SELECT l.*, a.name, a.name_ar FROM journal_lines l JOIN accounts a ON a.code=l.account "
                                "WHERE entry_id=? ORDER BY l.id", (eid,))]
    e["amount"] = round(sum(ln["debit"] for ln in e["lines"]), 2)
    e["tags"] = [r["tag"] for r in db.q("SELECT tag FROM entry_tags WHERE entry_id=? ORDER BY tag", (eid,))]
    return e


def get_entries(db, ids):
    """get_entry for many entries at once, in the order given: three queries in all instead of three per entry (a page of
    the journal in the browser edition was a hundred and fifty round trips)."""
    ids = [int(i) for i in ids]
    if not ids:
        return []
    if len(ids) > 500:                        # a whole export: in parts, within any SQLite's limit on parameters
        return [e for k in range(0, len(ids), 500) for e in get_entries(db, ids[k:k + 500])]
    marks = ",".join("?" * len(ids))
    heads = {e["id"]: e for e in db.q(
        "SELECT e.*, b.code AS bu_code, (SELECT COUNT(*) FROM attachments WHERE owner='entry' AND owner_id=e.id) AS files "
        f"FROM journal_entries e LEFT JOIN business_units b ON b.id=e.bu_id WHERE e.id IN ({marks})", ids)}
    lines, tags = {}, {}
    for r in db.q("SELECT l.*, a.name, a.name_ar FROM journal_lines l JOIN accounts a ON a.code=l.account "
                  f"WHERE entry_id IN ({marks}) ORDER BY l.id", ids):
        lines.setdefault(r["entry_id"], []).append(
            {"account": r["account"], "name": r["name"], "name_ar": r["name_ar"], "debit": to_major(r["debit"]),
             "credit": to_major(r["credit"]), **({"party": r["party"]} if r["party"] else {}),
             **({"memo": r["memo"]} if r["memo"] else {}), **({"cost_item": r["cost_item"]} if r["cost_item"] else {}),
                   **({"currency": r["currency"], "fx_amount": to_major(r["fx_amount"]), "fx_rate": r["fx_rate"], **({"settles": r["settles"]} if r.get("settles") else {})} if r["currency"] else {})})
    for r in db.q(f"SELECT entry_id, tag FROM entry_tags WHERE entry_id IN ({marks}) ORDER BY tag", ids):
        tags.setdefault(r["entry_id"], []).append(r["tag"])
    out = []
    for i in ids:
        if i not in heads:
            continue
        e = dict(heads[i])                    # its own copy (an id asked for twice gets two separate entries)
        e["meta"] = json.loads(e["meta"] or "{}")
        e["history"] = json.loads(e["history"] or "[]")
        e["lines"] = [dict(ln) for ln in lines.get(i, [])]
        e["amount"] = round(sum(ln["debit"] for ln in e["lines"]), 2)
        e["tags"] = list(tags.get(i, []))
        out.append(e)
    return out


def set_tags(db, eid, tags):
    """An entry's tags: short labels, at most 12, each up to 40 characters."""
    clean = []
    for t in tags or []:
        t = re.sub(r"\s+", " ", str(t or "")).strip()[:40]
        if t and t.lower() not in [c.lower() for c in clean]:
            clean.append(t)
    with db.tx():
        db.conn.execute("DELETE FROM entry_tags WHERE entry_id=?", (int(eid),))
        db.conn.executemany("INSERT INTO entry_tags(entry_id, tag) VALUES (?,?)", [(int(eid), t) for t in clean[:12]])
    return clean[:12]


def _split(db, total_minor, paid_minor, payable_code, method=None, paid_from=None):
    """Credit lines for 'total, of which paid now; the rest is owed'."""
    if paid_minor < 0 or paid_minor > total_minor:
        raise ValidationError("Paid now must be between zero and the total")
    lines = []
    if paid_minor:
        lines.append({"account": money_account(db, method, paid_from), "credit": to_major(paid_minor)})
    if total_minor - paid_minor:
        lines.append({"account": payable_code, "credit": to_major(total_minor - paid_minor)})
    return lines


# ---------------------------------------------------------------------- taxes
def tax_code(db, code, kind):
    """(code, rate) for a VAT or WHT code; blank code = no tax."""
    code = text(code)
    if not code:
        return "", 0.0
    t = db.one("SELECT * FROM tax_codes WHERE code=?", (code,))
    if not t:
        raise ValidationError(f"Unknown tax code {code!r}")
    if t["kind"] != kind:
        raise ValidationError(f"{code} is not a {'VAT' if kind == 'vat' else 'withholding'} code")
    if not t["active"]:
        raise ValidationError(f"Tax code {code} is inactive")
    return code, float(t["rate"])


def _purchase_taxes(db, d, net_minor):
    """Input VAT we can recover and withholding tax we deduct from the supplier."""
    vat_code, vat_rate = tax_code(db, d.get("vat_code"), "vat")
    wht_code, wht_rate = tax_code(db, d.get("wht_code"), "wht")
    vat = tax_of(net_minor, vat_rate)
    wht = tax_of(net_minor, wht_rate)
    return vat_code, vat, wht_code, wht


def _purchase_lines(db, d, debit_account, net, vat, wht, payable, paid):
    """Dr cost/asset (net) + Dr input VAT; Cr cash (paid) + Cr supplier (rest) + Cr WHT payable."""
    supplier_total = net + vat - wht
    lines = [{"account": debit_account, "debit": to_major(net)}]
    if vat:
        lines.append({"account": db.acct("vat_input"), "debit": to_major(vat)})
    lines += _split(db, supplier_total, paid, payable, d.get("method"), d.get("paid_from"))
    if wht:
        lines.append({"account": db.acct("wht_payable"), "credit": to_major(wht)})
    return lines


# ---------------------------------------------------------------- purchasing
def record_project_cost(db, d, user="", strict=False):
    """Direct costs for a business unit (materials, subcontractors, site work)."""
    bu = bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=True)
    account = account_code(db, d.get("account") or db.acct("materials"), types=("COGS",), field="cost account")
    qty = num(d.get("qty", 1) or 1, "quantity", allow_zero=False)
    rate = num(d.get("rate"), "unit cost", allow_zero=False)
    quoted = to_minor(qty * rate)
    savings = to_minor(num(d.get("savings"), "negotiation savings"))
    if savings > quoted:
        raise ValidationError("Savings cannot exceed the quoted total")
    cost = quoted - savings
    vat_code, vat, wht_code, wht = _purchase_taxes(db, d, cost)
    paid = to_minor(num(d.get("paid"), "paid now"))
    vendor = text(d.get("vendor"))
    if (paid < cost + vat - wht or wht) and not vendor:
        raise ValidationError("Name the supplier so the unpaid balance / withholding can be tracked")
    payable = account_code(db, d.get("payable_account") or db.acct("ap"), types=("Liability",))
    # new entries only (strict): replays of the shared log and imports keep whatever was recorded before this rule
    if strict and text(d.get("deal_status")).lower() == "settled" and paid < cost + vat - wht:
        raise ValidationError(
            f"The deal is marked Settled but {to_major(cost + vat - wht - paid):,.2f} is still unpaid. If the supplier "
            "accepted less, add the difference to Negotiation discount; otherwise leave the status Open until it is paid.")
    lines = _purchase_lines(db, d, account, cost, vat, wht, payable, paid)
    mode = negotiation_mode(db, bu, d.get("negotiation"))
    if mode == "keep" and savings:   # cost stays at the full price (what the client is billed on); the discount is ours
        lines[0]["debit"] = to_major(quoted)
        lines.append({"account": db.acct("negotiation_income"), "credit": to_major(savings)})
    meta = {"qty": qty, "unit": text(d.get("unit")), "rate": rate, "quoted": to_major(quoted),
            "savings": to_major(savings), "deal_ref": text(d.get("deal_ref")),
            "deal_status": text(d.get("deal_status")), "method": text(d.get("method")),
            "vat_code": vat_code, "wht_code": wht_code, "negotiation": mode if savings else "",
            **({"stage": text(d["stage"], maxlen=80)} if text(d.get("stage")) else {})}
    return post_entry(db, date=d.get("date"), kind="Direct Cost",
                      memo=text(d.get("description"), "description", required=True), bu_id=bu, party=vendor,
                      ref=meta["deal_ref"], lines=lines, meta=meta, user=user, **fx_of(d))


def record_expense(db, d, user=""):
    """Operating / overhead expense. With no business unit it is *shared* overhead,
    later allocated to business units by their manual overhead %."""
    account = account_code(db, d.get("account") or db.acct("other_overhead"), types=("Expense",),
                           field="expense account")
    amount = to_minor(num(d.get("amount"), "amount", allow_zero=False))
    vat_code, vat, wht_code, wht = _purchase_taxes(db, d, amount)
    due = amount + vat - wht
    paid = due if d.get("paid") in (None, "") else to_minor(num(d.get("paid"), "paid now"))
    vendor = text(d.get("vendor"))
    if (paid < due or wht) and not vendor:
        raise ValidationError("Name the supplier so the unpaid balance / withholding can be tracked")
    payable = account_code(db, d.get("payable_account") or db.acct("ap"), types=("Liability",))
    lines = _purchase_lines(db, d, account, amount, vat, wht, payable, paid)
    return post_entry(db, date=d.get("date"), kind="Operating Expense",
                      memo=text(d.get("description"), "description", required=True),
                      bu_id=bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")), party=vendor, lines=lines,
                      meta={"method": text(d.get("method")), "vat_code": vat_code, "wht_code": wht_code, **alloc_meta(db, d)}, user=user,
                      **fx_of(d))


def alloc_meta(db, d):
    """How a shared cost is allocated: a named rule, or kept as company cost (not allocated). Blank: by the rules."""
    v = text(d.get("alloc_rule"))
    if not v or d.get("bu_id") or d.get("bu_code"):
        return {}
    if v in ("company", "__company"):
        return {"no_alloc": 1}
    if not db.one("SELECT 1 FROM alloc_rules WHERE code=?", (v,)):
        raise ValidationError(f"Unknown allocation rule: {v}")
    return {"alloc_rule": v}


def record_payroll(db, d, user=""):
    ctype = d.get("cost_type") or "Direct Labour (Cost of Revenue)"
    if ctype not in PAYROLL_TYPES:
        raise ValidationError("Unknown payroll cost type")
    role, needs_bu = PAYROLL_TYPES[ctype]
    bu = bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=needs_bu)
    account = account_code(db, d.get("account") or db.acct(role), types=("COGS", "Expense"))
    worker = text(d.get("worker"), "worker name", required=True)
    qty = num(d.get("qty", 1) or 1, "days / months", allow_zero=False)
    rate = num(d.get("rate"), "rate", allow_zero=False)
    total = to_minor(qty * rate)
    paid = to_minor(num(d.get("paid"), "paid now"))
    payable = account_code(db, d.get("payable_account") or db.acct("accrued_payroll"), types=("Liability",))
    lines = [{"account": account, "debit": to_major(total)}] + _split(db, total, paid, payable, d.get("method"),
                                                                      d.get("paid_from"))
    role_txt = text(d.get("role"))
    meta = {"role": role_txt, "cost_type": ctype, "qty": qty, "rate": rate, "period": text(d.get("period")),
            "basis": text(d.get("basis")), "method": text(d.get("method"))}
    return post_entry(db, date=d.get("date"), kind="Payroll Accrual",
                      memo=f"{worker} — {role_txt}" if role_txt else worker,
                      bu_id=bu, party=worker, lines=lines, meta=meta, user=user, **fx_of(d))


def record_payment(db, d, user=""):
    """Settle what we owe a supplier or worker (any liability account)."""
    account = account_code(db, d.get("against") or db.acct("ap"), types=("Liability",), field="pay-against account")
    party = text(d.get("party"), "payee", required=True)
    amount = num(d.get("amount"), "amount", allow_zero=False)
    fxd = fx_of(d)
    owed, unit = owed_in(db, account, party, d)
    if to_minor(amount) > to_minor(owed) and not d.get("allow_overpay"):
        raise ValidationError(f"{party} is owed {owed:,.2f}{unit} on {account}. Tick 'allow overpayment' to pay more.")
    return post_entry(db, date=d.get("date"), kind="Payment Voucher", memo=text(d.get("memo")) or f"Payment to {party}",
                      bu_id=bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")), party=party, ref=text(d.get("ref")),
                      lines=[{"account": account, "debit": amount, **({"settles": d["settles"]} if str(d.get("settles") or "").isdigit() else {})},
                             {"account": money_account(db, d.get("method"), d.get("paid_from")), "credit": amount}],
                      meta={"method": text(d.get("method")), **({"deal_ref": text(d["deal_ref"])} if text(d.get("deal_ref")) else {})},
                      user=user, **fxd)


NEGOTIATION_MODES = ("keep", "pass")


def negotiation_mode(db, bu_id, asked=None):
    """What happens to a negotiated discount: 'keep' = company earnings (the cost and the client's bill stay at the
    full price), 'pass' = the cost goes down (the client benefits). The entry's own choice wins, then the business
    unit's; entries from before this option existed were always 'pass'."""
    asked = text(asked).lower()
    if asked in NEGOTIATION_MODES:
        return asked
    if asked:
        raise ValidationError("Negotiation discount handling must be 'keep' (company earnings) or 'pass' (lower cost)")
    b = db.one("SELECT negotiation FROM business_units WHERE id=?", (bu_id,)) if bu_id else None
    return b["negotiation"] if b and b["negotiation"] in NEGOTIATION_MODES else "pass"


def supplier_discount_defaults(db, party, account=None):
    """Where a late discount from a supplier goes: company earnings or back to the cost of their latest purchase
    (same account, business unit and deal), following that project's negotiation setting."""
    account = account or db.acct("ap")
    last = db.one(f"""SELECT e.bu_id, e.meta, (SELECT l.account FROM journal_lines l JOIN accounts a ON a.code=l.account
                         WHERE l.entry_id=e.id AND l.debit>0 AND a.type IN ('COGS','Expense') ORDER BY l.debit DESC LIMIT 1) AS cost
                      FROM journal_entries e WHERE {LIVE} AND e.party=?
                        AND EXISTS (SELECT 1 FROM journal_lines x WHERE x.entry_id=e.id AND x.account=? AND x.credit>0)
                      ORDER BY e.date DESC, e.id DESC LIMIT 1""", (party, account))
    meta = json.loads(last["meta"] or "{}") if last else {}
    bu = (last or {}).get("bu_id")
    b = db.one("SELECT negotiation FROM business_units WHERE id=?", (bu,)) if bu else None
    mode = meta.get("negotiation") or (b["negotiation"] if b and b["negotiation"] in NEGOTIATION_MODES else "") \
        or ("pass" if (last or {}).get("cost") else "keep")
    keep = db.acct("negotiation_income")
    cost = (last or {}).get("cost") or keep
    return {"credit_account": keep if mode == "keep" else cost, "negotiation": mode, "keep_account": keep,
            "cost_account": cost, "bu_id": bu, "deal_ref": meta.get("deal_ref", ""),
            "owed": party_balance(db, account, party)}


def record_supplier_discount(db, d, user=""):
    """The supplier accepted less than what is still owed: clear the rest as a discount (reduces that cost)."""
    party = text(d.get("party"), "supplier", required=True)
    account = account_code(db, d.get("against") or db.acct("ap"), types=("Liability",), field="payable account")
    dflt = supplier_discount_defaults(db, party, account)
    owed, unit = owed_in(db, account, party, d)
    amount = num(d.get("amount") or owed, "amount", allow_zero=False)
    if to_minor(amount) > to_minor(owed):
        raise ValidationError(f"{party} is only owed {owed:,.2f}{unit} on {account}")
    mode = text(d.get("negotiation")).lower()
    if not d.get("credit_account") and mode in NEGOTIATION_MODES:
        d = {**d, "credit_account": dflt["keep_account"] if mode == "keep" else dflt["cost_account"]}
    credit = account_code(db, d.get("credit_account") or dflt["credit_account"], types=("COGS", "Expense", "Revenue"),
                          field="discount account")
    mode = "keep" if db.one("SELECT type FROM accounts WHERE code=?", (credit,))["type"] == "Revenue" else "pass"
    bu = bu_id_or_none(db, d.get("bu_id") if d.get("bu_id") not in (None, "") else dflt["bu_id"])
    deal = text(d.get("deal_ref")) or dflt["deal_ref"]
    return post_entry(db, date=d.get("date"), kind="Supplier Discount",
                      memo=text(d.get("memo")) or f"Discount agreed with {party}" + (f" — {deal}" if deal else ""),
                      bu_id=bu, party=party, ref=deal,
                      lines=[{"account": account, "debit": amount}, {"account": credit, "credit": amount}],
                      meta={"deal_ref": deal, "savings": amount, "deal_status": "Settled", "negotiation": mode},
                      user=user, **fx_of(d))


# ----------------------------------------------------------------------- sales
def record_collection(db, d, user=""):
    """Money received from a client. The credit account decides what it is:
    revenue (cash-basis), receivables (settles an invoice) or client advances."""
    bu = bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=True)
    amount = num(d.get("amount"), "amount", allow_zero=False)
    mode_roles = {"revenue": "revenue", "invoice": "ar", "advance": "client_advances"}
    credit = d.get("credit_account") or db.acct(mode_roles.get(d.get("mode") or "revenue", "revenue"))
    credit = account_code(db, credit, types=("Revenue", "Asset", "Liability"), field="credit account")
    bu_row = db.one("SELECT name, customer FROM business_units WHERE id=?", (bu,))
    received = to_minor(amount)
    withheld = to_minor(num(d.get("withheld"), "tax withheld by client"))
    gross = received + withheld
    vat_code, vat_rate = tax_code(db, d.get("vat_code"), "vat")
    vat = 0
    if vat_code:
        if db.one("SELECT type FROM accounts WHERE code=?", (credit,))["type"] != "Revenue":
            raise ValidationError("VAT on a receipt applies only when it is booked straight to revenue "
                                  "(invoiced VAT is charged on the sales invoice)")
        vat = gross - int(round(gross / (1 + vat_rate)))
    lines = [{"account": money_account(db, d.get("method"), d.get("received_into")), "debit": to_major(received)}]
    if withheld:
        lines.append({"account": db.acct("wht_receivable"), "debit": to_major(withheld)})
    lines.append({"account": credit, "credit": to_major(gross - vat), **({"settles": d["settles"]} if str(d.get("settles") or "").isdigit() else {})})
    if vat:
        lines.append({"account": db.acct("vat_output"), "credit": to_major(vat)})
    return post_entry(db, date=d.get("date"), kind="Receipt Voucher", memo=text(d.get("memo")) or "Client payment",
                      bu_id=bu, party=text(d.get("party")) or bu_row["customer"] or bu_row["name"],
                      ref=text(d.get("ref")), lines=lines,
                      meta={"method": text(d.get("method")), "vat_code": vat_code, "withheld": to_major(withheld)},
                      user=user, **fx_of(d))


# ------------------------------------------------------------------ partners
def _partner(db, d):
    p = db.one("SELECT * FROM partners WHERE id=? OR name=?", (d.get("partner_id"), text(d.get("partner"))))
    if not p:
        raise ValidationError("Choose a partner")
    return p


def record_partner_tx(db, d, user=""):
    """Capital in, or distribution (dividend/drawing) out."""
    p = _partner(db, d)
    typ = d.get("type") or "Distribution"
    amount = num(d.get("amount"), "amount", allow_zero=False)
    money = money_account(db, d.get("method"), d.get("cash_account"))
    if typ == "Capital":
        lines = [{"account": money, "debit": amount},
                 {"account": account_code(db, d.get("equity_account") or db.acct("capital"), types=("Equity",)),
                  "credit": amount}]
    elif typ == "Distribution":
        lines = [{"account": account_code(db, d.get("equity_account") or db.acct("drawings"), types=("Equity",)),
                  "debit": amount}, {"account": money, "credit": amount}]
    else:
        raise ValidationError("Type must be Capital or Distribution")
    kind = "Capital Contribution" if typ == "Capital" else "Owner Drawing"
    return post_entry(db, date=d.get("date"), kind=kind, memo=text(d.get("memo")) or kind,
                      bu_id=bu_id_or_none(db, d.get("bu_id") or d.get("bu_code")), party=p["name"], lines=lines,
                      user=user, **fx_of(d))


# ----------------------------------------------------------------- journal
def _journal_items(db, raw, bu_default):
    """Item lines of a journal voucher → (debit lines, rows to keep). Each: item (an existing one or a new name), unit,
    qty × unit price, and where it goes — a warehouse (its inventory account; the stock goes up on this same entry, so
    the asset is counted once) or straight onto a project (a cost account, materials unless another is chosen)."""
    out, debits = [], []
    for i, ln in enumerate(raw or []):
        name = text(ln.get("item") or ln.get("name"), maxlen=200)
        if not name and not ln.get("item_id"):
            continue
        qty = num(ln.get("qty"), f"quantity (item {i + 1})", allow_zero=False)
        if qty < 0:
            raise ValidationError(f"Item {i + 1}: the quantity must be positive")
        rate = to_minor(num(ln.get("rate") if ln.get("rate") not in (None, "") else ln.get("price"), f"unit price (item {i + 1})",
                            allow_zero=False))
        amount = int(round(qty * rate))
        wh = ln.get("warehouse_id") or ln.get("warehouse")
        if wh not in (None, "", "project"):
            w = _warehouse(db, wh)
            it = _item(db, {"item": name, "item_id": ln.get("item_id"), "unit": ln.get("unit")}, create=True)
            account, bu = _wh_account(db, w), None
        else:
            w = None
            it = db.one("SELECT * FROM stock_items WHERE id=? OR name=?", (ln.get("item_id"), name))
            account = account_code(db, ln.get("account") or db.acct("materials"), types=("COGS", "Expense"), field="cost account")
            bu = bu_id_or_none(db, ln.get("bu_id") or ln.get("bu_code")) or bu_default
            if not bu:
                raise ValidationError(f"Item {i + 1}: choose the project it was used on")
        if it and ln.get("unit") and not it.get("unit"):
            db.conn.execute("UPDATE stock_items SET unit=? WHERE id=?", (text(ln.get("unit")), it["id"]))
        out.append({"item": it, "description": (it or {}).get("name") or name, "unit": text(ln.get("unit")) or (it or {}).get("unit") or "",
                    "qty": qty, "rate": rate, "amount": amount, "warehouse": w, "account": account, "bu_id": bu})
        debits.append({"account": account, "debit": to_major(amount)})
    return debits, out


def record_journal(db, d, user=""):
    """Manual journal: opening balances, depreciation, tax, loans, accruals... With item lines (qty × unit price) the
    voucher also receives stock into a warehouse or charges materials to a project; give only the other side."""
    with db.tx():
        bu_default = bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"))
        debits, items = _journal_items(db, d.get("items"), bu_default)
        meta = {**{k: text(d[k]) for k in ("deal_ref", "deal_status") if d.get(k)},
                # a negotiated deal: what was quoted, the saving and how it is treated (keep / pass)
                **{k: num(d[k], k) for k in ("quoted", "savings") if d.get(k) not in (None, "")},
                **({"negotiation": d["negotiation"]} if d.get("negotiation") in ("keep", "pass") else {}),
                **alloc_meta(db, d), **({"stage": text(d["stage"], maxlen=80)} if text(d.get("stage")) else {})}
        if items:
            meta["items"] = [{"item": x["description"], "unit": x["unit"], "qty": x["qty"], "rate": to_major(x["rate"]),
                              "warehouse": x["warehouse"]["name"] if x["warehouse"] else "", "account": x["account"]} for x in items]
        date = clean_date(d.get("date"))
        eid = _insert_entry(db, date=date, kind=text(d.get("kind")) or "Journal Voucher", memo=text(d.get("memo")),
                            bu_id=bu_default, party=text(d.get("party")), ref=text(d.get("ref")),
                            lines=debits + list(d.get("lines") or []), user=user, meta=meta or None, **fx_of(d))
        for x in items:
            if x["warehouse"]:
                from . import stockcost
                mid = _new_move(db, x["item"]["id"], x["warehouse"]["id"], date, x["qty"], "Receipt", "journal voucher",
                                eid=eid, value=x["amount"], fixed=1, rate=x["rate"])
                stockcost.apply(db, x["item"]["id"], stockcost.order_key(db.one("SELECT * FROM stock_moves WHERE id=?", (mid,))),
                                why=f"goods received on {date}", user=user)
            db.conn.execute("INSERT INTO material_lines(entry_id,date,bu_id,item_id,description,unit,qty,rate,amount,warehouse_id,account) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (eid, date, x["bu_id"], (x["item"] or {}).get("id"), x["description"], x["unit"], x["qty"], x["rate"],
                             x["amount"], (x["warehouse"] or {}).get("id"), x["account"]))
        return eid


# ------------------------------------------------------------- corrections
def _stock_undo_guard(db, eid):
    """A stock cost adjustment goes with the movement it re-costed: it is undone with that movement's entry."""
    r = db.one("SELECT r.origin_entry, r.move_id FROM stock_recosts r WHERE r.entry_id=?", (eid,))
    if r:
        raise ValidationError("This cost adjustment belongs to a stock movement" + (f" (entry #{r['origin_entry']})" if r["origin_entry"] else "")
                              + ". Undo that movement's entry and it goes with it.")


def _undo_recosts(db, eid, user="", reverse_on=None):
    """The cost adjustments of an entry's stock movements: voided with it, or reversed on the same date."""
    for r in db.q("SELECT s.entry_id FROM stock_recosts s JOIN journal_entries e ON e.id=s.entry_id "
                  "WHERE s.origin_entry=? AND e.status='posted' AND e.reversed_by IS NULL", (eid,)):
        if reverse_on:
            a = get_entry(db, r["entry_id"])
            rid = _insert_entry(db, date=reverse_on, kind="Reversing Entry", memo=f"Reversal of #{a['id']}: {a['memo']}",
                                bu_id=a["bu_id"], party=a["party"], ref=a["ref"], reversal_of=a["id"], user=user,
                                lines=[{"account": ln["account"], "debit": ln["credit"], "credit": ln["debit"]} for ln in a["lines"]])
            db.conn.execute("UPDATE journal_entries SET reversed_by=? WHERE id=?", (rid, a["id"]))
        else:
            meta = json.loads(db.one("SELECT meta FROM journal_entries WHERE id=?", (r["entry_id"],))["meta"] or "{}")
            meta.update(void_reason=f"with entry #{eid}", voided_by=user)
            db.conn.execute("UPDATE journal_entries SET status='void', meta=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), r["entry_id"]))


def _owned_guard(db, eid):
    """An entry another document made (a purchase receipt, a subcontractor certificate, a crew sheet) is changed from
    that document, so the two never disagree; voiding it releases the document instead."""
    from .textl10n import Note
    for table, en, ar in (("po_receipts", "a purchase order's receipt", "استلام أمر شراء"),
                          ("sub_certificates", "a subcontractor certificate", "مستخلص مقاول باطن"), ("crew_sheets", "a crew sheet", "يومية عمال")):
        if db.one(f"SELECT 1 FROM {table} WHERE entry_id=? LIMIT 1", (eid,)):
            raise ValidationError(Note(f"This entry was made by {en}. Change it there, or void this entry and record it again.",
                                       f"هذا القيد ناتج عن {ar}. عدّله من هناك، أو ألغِ هذا القيد وسجّله من جديد."))


def _unlink_documents(db, eid, user=""):
    from . import equity, stockcost
    equity.undo_change(db, entry_id=eid)  # an ownership change booked by this entry goes with it
    db.conn.execute("UPDATE extracts SET status='draft', entry_id=NULL WHERE entry_id=?", (eid,))
    # the other documents an entry books: back to where they were before posting, so they can be posted again
    db.conn.execute("UPDATE sub_certificates SET status='approved', entry_id=NULL WHERE entry_id=?", (eid,))
    db.conn.execute("UPDATE crew_sheets SET paid=0, entry_id=NULL WHERE entry_id=?", (eid,))
    db.conn.execute("UPDATE equipment_usage SET entry_id=NULL WHERE entry_id=?", (eid,))
    db.conn.execute("UPDATE maintenance_logs SET entry_id=NULL WHERE entry_id=?", (eid,))
    db.conn.execute("DELETE FROM sub_recognitions WHERE entry_id=?", (eid,))
    m = json.loads((db.one("SELECT meta FROM journal_entries WHERE id=?", (eid,)) or {}).get("meta") or "{}")
    if m.get("bill") and m.get("bill_period"):          # a bill's payment undone: that period is due again
        others = db.one("SELECT 1 FROM journal_entries WHERE id<>? AND status='posted' AND reversed_by IS NULL "
                        "AND json_extract(meta,'$.bill')=? AND json_extract(meta,'$.bill_period')=?",
                        (eid, m["bill"], m["bill_period"]))
        if not others:
            db.conn.execute("UPDATE cost_subs SET next_due=? WHERE id=? AND next_due>?", (m["bill_period"], m["bill"], m["bill_period"]))
    pos = [r["po_id"] for r in db.q("SELECT DISTINCT po_id FROM po_receipts WHERE entry_id=?", (eid,))]
    if pos:
        from . import purchasing
        db.conn.execute("DELETE FROM po_receipts WHERE entry_id=?", (eid,))
        for po in pos:
            purchasing._refresh_status(db, po)
    gone = db.q("SELECT * FROM stock_moves WHERE entry_id=?", (eid,))
    db.conn.execute("DELETE FROM stock_moves WHERE entry_id=?", (eid,))
    db.conn.execute("DELETE FROM material_lines WHERE entry_id=?", (eid,))
    first = {}
    for m in gone:
        k = stockcost.order_key(m)
        first[m["item_id"]] = min(first.get(m["item_id"], k), k)
    for item_id, k in first.items():     # what came after it is re-costed; stock that would run short is refused
        stockcost.apply(db, item_id, k, why=f"entry #{eid} undone", user=user)


def void_entry(db, eid, reason="", user="", payroll_run=False):
    with db.tx():
        e = db.one("SELECT * FROM journal_entries WHERE id=?", (eid,))
        if not e:
            raise ValidationError("Entry not found")
        if e["status"] == "void":
            raise ValidationError("Already void")
        if not payroll_run and db.one("SELECT 1 FROM payroll_lines l JOIN payroll_runs r ON r.id=l.run_id "
                                      "WHERE l.entry_id=? AND r.status='posted'", (eid,)):
            # voiding one employee's entry would leave the run 'posted' with totals the books no longer have
            from .textl10n import Note
            raise ValidationError(Note("This entry belongs to a posted payroll run. Open the run and Unpost it instead.",
                                       "هذا القيد جزء من مسير رواتب مُرحَّل. افتح المسير واستخدم «إلغاء الترحيل» بدلاً من ذلك."))
        check_open(db, e["date"])
        # a month-end revaluation and its next-day reversal (erp/fx.py) are undone together, so it can be done again
        pair = e["reversal_of"] or e["reversed_by"]
        if pair and "FX Revaluation" in (e["kind"], (db.one("SELECT kind FROM journal_entries WHERE id=?", (pair,)) or {}).get("kind")):
            check_open(db, db.one("SELECT date FROM journal_entries WHERE id=?", (pair,))["date"])
            for x in (eid, pair):
                m = json.loads((db.one("SELECT meta FROM journal_entries WHERE id=?", (x,)) or {}).get("meta") or "{}")
                m.update(void_reason=text(reason), voided_by=user)
                db.conn.execute("UPDATE journal_entries SET status='void', meta=? WHERE id=?", (json.dumps(m, ensure_ascii=False), x))
            return
        if e["reversed_by"] or e["reversal_of"]:
            raise ValidationError("This entry is part of a reversal pair and cannot be voided")
        _stock_undo_guard(db, eid)
        meta = json.loads(e["meta"] or "{}")
        meta["void_reason"] = text(reason)
        meta["voided_by"] = user
        db.conn.execute("UPDATE journal_entries SET status='void', meta=? WHERE id=?",
                        (json.dumps(meta, ensure_ascii=False), eid))
        _undo_recosts(db, eid, user)
        _unlink_documents(db, eid, user)
        _wip_after(db, e["kind"], e["bu_id"])


def reverse_entry(db, eid, date=None, user=""):
    with db.tx():
        e = get_entry(db, eid)
        if not e:
            raise ValidationError("Entry not found")
        if e["status"] != "posted" or e["reversed_by"] or e["reversal_of"]:
            raise ValidationError("Only a live, un-reversed entry can be reversed")
        if date and clean_date(date) < e["date"]:
            raise ValidationError(f"The reversal cannot be dated before the entry it reverses ({e['date']})")
        lock = (db.setting("lock_date") or "").strip()
        if lock and e["date"] <= lock and db.one("SELECT 1 FROM extracts WHERE entry_id=?", (eid,)):
            # the invoice belongs to a closed period: it stays issued there; correct it with a credit note now
            raise ValidationError(f"This invoice is in the closed books (up to {lock}). Issue a credit note in the open period instead.")
        _stock_undo_guard(db, eid)
        lines = [{"account": ln["account"], "debit": ln["credit"], "credit": ln["debit"], "party": ln.get("party", ""),
                  **({k: ln[k] for k in ("currency", "fx_amount", "fx_rate", "settles") if k in ln} if ln.get("currency") else {})}
                 for ln in e["lines"]]
        rid = _insert_entry(db, date=date or today(), kind="Reversing Entry", memo=f"Reversal of #{eid}: {e['memo']}",
                            bu_id=e["bu_id"], party=e["party"], ref=e["ref"], lines=lines, reversal_of=eid, user=user)
        db.conn.execute("UPDATE journal_entries SET reversed_by=? WHERE id=?", (rid, eid))
        _undo_recosts(db, eid, user, reverse_on=date or today())
        _unlink_documents(db, eid, user)
        return rid


def edit_entry(db, eid, d, user=""):
    """Correct an entry in place; the previous version is kept in its history."""
    with db.tx():
        e = get_entry(db, eid)
        if not e:
            raise ValidationError("Entry not found")
        if e["status"] != "posted" or e["reversed_by"] or e["reversal_of"]:
            raise ValidationError("Only live entries can be edited")
        check_open(db, e["date"])
        check_open(db, clean_date(d.get("date") or e["date"]))
        if db.one("SELECT 1 FROM stock_moves WHERE entry_id=?", (eid,)) or db.one("SELECT 1 FROM material_lines WHERE entry_id=?", (eid,)) or \
                db.one("SELECT 1 FROM extracts WHERE entry_id=?", (eid,)):
            raise ValidationError("This entry belongs to a stock movement or invoice. Reverse it and re-enter.")
        _owned_guard(db, eid)
        fxd = fx_of(d)
        vlines = _validate_lines(db, _fx_lines(db, d.get("date") or e["date"], d.get("lines") or e["lines"], d.get("party", e["party"]),
                                               fxd.get("currency"), fxd.get("rate"), exclude_eid=eid))
        snap = {k: e[k] for k in ("date", "memo", "bu_code", "party", "lines")}
        snap.update(ref=e["ref"], edited_at=now_iso(), edited_by=user)
        meta = dict(e["meta"])
        for k in ("deal_ref", "deal_status"):   # the deal a cost belongs to can be corrected here too
            if k in d:
                meta[k] = text(d.get(k))
        db.conn.execute("UPDATE journal_entries SET date=?, memo=?, bu_id=?, party=?, ref=?, meta=?, history=? "
                        "WHERE id=?",
                        (clean_date(d.get("date") or e["date"]), text(d.get("memo", e["memo"]), maxlen=1000),
                         bu_id_or_none(db, d.get("bu_id", e["bu_id"])), text(d.get("party", e["party"])),
                         text(d.get("ref", e["ref"])), json.dumps(meta, ensure_ascii=False),
                         json.dumps(e["history"] + [snap], ensure_ascii=False), eid))
        db.conn.execute("DELETE FROM journal_lines WHERE entry_id=?", (eid,))
        _insert_lines(db, eid, vlines)
        # work in progress follows the corrected entry (both the old and the new business unit)
        new_bu = bu_id_or_none(db, d.get("bu_id", e["bu_id"]))
        for bu in {e["bu_id"], new_bu}:
            _wip_after(db, e["kind"], bu)


def delete_entry(db, eid):
    with db.tx():
        e = db.one("SELECT status FROM journal_entries WHERE id=?", (eid,))
        if not e:
            raise ValidationError("Entry not found")
        if e["status"] != "void":
            raise ValidationError("Void an entry before deleting it")
        check_open(db, db.one("SELECT date FROM journal_entries WHERE id=?", (eid,))["date"])
        db.conn.execute("DELETE FROM journal_entries WHERE id=?", (eid,))


# ------------------------------------------------------------- chart of accounts
def save_account(db, d, old_code=None):
    code = text(d.get("code"), "code", required=True, maxlen=20)
    if not re.fullmatch(r"[\w.\-]+", code):          # letters, digits, dot, dash: a code is never markup
        raise ValidationError("An account code uses letters, digits, dots and dashes only")
    name = text(d.get("name"), "name", required=True)
    name_ar = text(d.get("name_ar"))
    typ = d.get("type")
    if typ not in SUBTYPES:
        raise ValidationError("Choose an account type")
    sub = d.get("subtype") or SUBTYPES[typ][0]
    if sub not in SUBTYPES[typ]:
        raise ValidationError(f"Subtype {sub!r} does not belong to {typ}")
    role = text(d.get("role")) or None
    active = 0 if d.get("active") in (0, "0", False, "false") else 1
    with db.tx():
        if db.one("SELECT 1 FROM account_groups WHERE code=?", (code,)):
            raise ValidationError(f"Code {code} is used by a group of the chart")
        cur0 = db.one("SELECT parent FROM accounts WHERE code=?", (old_code,)) if old_code else None
        # its group: given, or kept as it was (callers that do not know about groups never move an account)
        parent = text(d.get("parent")) if "parent" in d else ((cur0 or {}).get("parent") or "")
        _check_parent(db, parent, typ)
        # kept in a foreign currency (a USD bank account): given, or kept as it was
        from . import fx
        currency = fx.code(d.get("currency")) if "currency" in d else ((db.one("SELECT currency FROM accounts WHERE code=?", (old_code,)) or {}).get("currency", "") if old_code else "")
        if currency == fx.base(db):
            currency = ""
        if old_code:
            cur = db.one("SELECT * FROM accounts WHERE code=?", (old_code,))
            if not cur:
                raise ValidationError("Account not found")
            if code != old_code and db.one("SELECT 1 FROM accounts WHERE code=?", (code,)):
                raise ValidationError(f"Code {code} is already used")
            if cur["role"] and not active:
                raise ValidationError("System accounts cannot be deactivated")
            used = db.one("SELECT 1 FROM journal_lines WHERE account=? LIMIT 1", (old_code,))
            if used and cur["type"] != typ:
                raise ValidationError("This account has postings; its type cannot change (subtype can)")
            if used and (cur["subtype"] == "cash") != (sub == "cash"):
                # cash ↔ not cash re-states every past cash flow and the money lists; open a new account instead
                raise ValidationError("This account has postings; it cannot move into or out of Cash & cash equivalents. "
                                      "Open a new account and move the balance.")
            if currency != (cur["currency"] or ""):
                if cur["role"]:
                    raise ValidationError("A system account stays in the company's currency; open a new account for the foreign one")
                if used:
                    raise ValidationError("This account has postings; its currency cannot change. Open a new account and move the balance.")
            db.conn.execute("UPDATE accounts SET code=?, name=?, name_ar=?, type=?, subtype=?, normal=?, active=?, parent=?, currency=? "
                            "WHERE code=?", (code, name, name_ar, typ, sub, normal_for(typ, sub), active, parent, currency, old_code))
        else:
            if db.one("SELECT 1 FROM accounts WHERE code=?", (code,)):
                raise ValidationError(f"Code {code} is already used")
            db.conn.execute("INSERT INTO accounts(code,name,name_ar,type,subtype,normal,active,parent,currency) VALUES (?,?,?,?,?,?,?,?,?)",
                            (code, name, name_ar, typ, sub, normal_for(typ, sub), active, parent, currency))
    return code


def delete_account(db, code):
    a = db.one("SELECT * FROM accounts WHERE code=?", (code,))
    if not a:
        raise ValidationError("Account not found")
    if a["role"]:
        raise ValidationError("System accounts cannot be deleted; they can be renamed or renumbered.")
    if db.one("SELECT 1 FROM journal_lines WHERE account=? LIMIT 1", (code,)):
        raise ValidationError("The account has postings. Deactivate it instead.")
    db.x("DELETE FROM accounts WHERE code=?", (code,))


# ------------------------------------------------------- groups of the chart (the tree)
# A group is a heading ("Fixed assets 111", "Suppliers 221") that totals the accounts and groups under it. Groups live in
# their own table, so nothing that reads accounts (forms, money lists, reports, imports) ever sees one, and nothing can
# be posted to one. A book without groups works exactly as before: every account at the top level.
def _check_parent(db, parent, typ):
    """An account's group: none, or an existing group whose type (if it has one) is the account's."""
    if not parent:
        return
    g = db.one("SELECT code, type FROM account_groups WHERE code=?", (parent,))
    if not g:
        raise ValidationError(f"Unknown group {parent}. Add it under the chart's groups first.")
    for t in _group_types_up(db, parent):
        if t and t != typ:
            raise ValidationError(f"Group {parent} holds {t} accounts; this account is {typ}")


def _group_types_up(db, code):
    """The types of a group and every group above it (the nearest first)."""
    out, seen = [], set()
    while code and code not in seen:
        seen.add(code)
        g = db.one("SELECT type, parent FROM account_groups WHERE code=?", (code,))
        if not g:
            break
        out.append(g["type"])
        code = g["parent"]
    return out


def group_descendants(db, code):
    """Every group under a group (not the group itself)."""
    kids = collections.defaultdict(list)
    for g in db.q("SELECT code, parent FROM account_groups"):
        kids[g["parent"]].append(g["code"])
    out, todo = [], list(kids.get(code, []))
    while todo:
        c = todo.pop()
        if c in out:
            continue
        out.append(c)
        todo += kids.get(c, [])
    return out


def group_accounts(db, code):
    """Every account under a group, however deep."""
    groups = [code] + group_descendants(db, code)
    marks = ",".join("?" * len(groups))
    return [r["code"] for r in db.q(f"SELECT code FROM accounts WHERE parent IN ({marks}) ORDER BY code", groups)]


def save_group(db, d, old_code=None):
    code = text(d.get("code"), "code", required=True, maxlen=20)
    if not re.fullmatch(r"[\w.\-]+", code):
        raise ValidationError("A group code uses letters, digits, dots and dashes only")
    name = text(d.get("name"), "name", required=True)
    name_ar = text(d.get("name_ar"))
    typ = text(d.get("type"))
    if typ and typ not in SUBTYPES:
        raise ValidationError("Choose an account type for the group, or leave it for any type")
    parent = text(d.get("parent"))
    active = 0 if d.get("active") in (0, "0", False, "false") else 1
    with db.tx():
        if db.one("SELECT 1 FROM accounts WHERE code=?", (code,)):
            raise ValidationError(f"Code {code} is used by an account")
        if code != old_code and db.one("SELECT 1 FROM account_groups WHERE code=?", (code,)):
            raise ValidationError(f"Group {code} already exists")
        if old_code and not db.one("SELECT 1 FROM account_groups WHERE code=?", (old_code,)):
            raise ValidationError("Group not found")
        if parent:
            if parent in (code, old_code) or (old_code and parent in group_descendants(db, old_code)):
                raise ValidationError("A group cannot sit under itself or under one of its own groups")
            up = db.one("SELECT 1 FROM account_groups WHERE code=?", (parent,))
            if not up:
                raise ValidationError(f"Unknown group {parent}")
            for t in _group_types_up(db, parent):
                if t and typ and t != typ:
                    raise ValidationError(f"Group {parent} holds {t} accounts; this group is {typ}")
        if typ and old_code:
            # what is already under it must be of its type
            under = group_accounts(db, old_code)
            if under:
                marks = ",".join("?" * len(under))
                bad = db.one(f"SELECT code, type FROM accounts WHERE code IN ({marks}) AND type<>? LIMIT 1", (*under, typ))
                if bad:
                    raise ValidationError(f"Account {bad['code']} under this group is {bad['type']}; the group cannot be {typ}")
            for g in group_descendants(db, old_code):
                t = db.one("SELECT type FROM account_groups WHERE code=?", (g,))["type"]
                if t and t != typ:
                    raise ValidationError(f"Group {g} under this group is {t}; the group cannot be {typ}")
        if old_code:
            db.conn.execute("UPDATE account_groups SET code=?, name=?, name_ar=?, type=?, parent=?, active=? WHERE code=?",
                            (code, name, name_ar, typ, parent, active, old_code))
            if code != old_code:      # what was under it follows the new code
                db.conn.execute("UPDATE account_groups SET parent=? WHERE parent=?", (code, old_code))
                db.conn.execute("UPDATE accounts SET parent=? WHERE parent=?", (code, old_code))
        else:
            db.conn.execute("INSERT INTO account_groups(code,name,name_ar,type,parent,active) VALUES (?,?,?,?,?,?)",
                            (code, name, name_ar, typ, parent, active))
    return code


def delete_group(db, code):
    if not db.one("SELECT 1 FROM account_groups WHERE code=?", (code,)):
        raise ValidationError("Group not found")
    if db.one("SELECT 1 FROM accounts WHERE parent=? LIMIT 1", (code,)) or db.one("SELECT 1 FROM account_groups WHERE parent=? LIMIT 1", (code,)):
        raise ValidationError("Move or delete the accounts and groups under this group first")
    db.x("DELETE FROM account_groups WHERE code=?", (code,))


def group_chain(db):
    """{account code: [its groups, top first]} for every account that has a group."""
    groups = {g["code"]: g["parent"] for g in db.q("SELECT code, parent FROM account_groups")}
    out = {}
    for a in db.q("SELECT code, parent FROM accounts WHERE parent<>''"):
        chain, c, seen = [], a["parent"], set()
        while c in groups and c not in seen:
            seen.add(c)
            chain.append(c)
            c = groups[c]
        out[a["code"]] = list(reversed(chain))
    return out


def chart_tree(db, balances=None):
    """The groups with their depth and the totals of everything under them ({code: minor} balances, optional)."""
    groups = {g["code"]: dict(g) for g in db.q("SELECT * FROM account_groups ORDER BY code")}
    def depth(c, seen=()):
        g = groups.get(c)
        return 0 if not g or not g["parent"] or c in seen else 1 + depth(g["parent"], seen + (c,))
    for c, g in groups.items():
        g["depth"] = depth(c)
        g["accounts"] = 0
        g["total"] = 0
    for a in db.q("SELECT code, parent FROM accounts WHERE parent<>''"):
        c, seen = a["parent"], set()
        while c in groups and c not in seen:
            seen.add(c)
            groups[c]["accounts"] += 1
            if balances is not None:
                groups[c]["total"] += balances.get(a["code"], 0)
            c = groups[c]["parent"]
    return list(groups.values())


# --------------------------------------------------------------------- stock
def _warehouse(db, value):
    w = db.one("SELECT * FROM warehouses WHERE id=? OR name=?", (value, text(value)))
    if not w:
        from .textl10n import seed_alias
        alias = seed_alias("warehouses", "name", value)
        w = db.one("SELECT * FROM warehouses WHERE name=?", (alias,)) if alias else None
    if not w:
        raise ValidationError(f"Unknown warehouse: {value}")
    return w


def _wh_account(db, w):
    return w["account"] or db.acct("inventory")


def _recompute_level(db, item_id, wh_id=None):
    """What each store holds of the item and its value, from the movements (stockcost)."""
    from . import stockcost
    stockcost.refresh(db, item_id)


def _item(db, d, create=False):
    name = text(d.get("item") or d.get("name"), "item", required=True)
    it = db.one("SELECT * FROM stock_items WHERE id=? OR name=?", (d.get("item_id"), name))
    if it:
        return it
    from . import dedupe
    same = dedupe.existing(db, name)      # the same item under another spelling, or a merged name
    if same:
        return same
    if not create:
        raise ValidationError(f"Unknown item: {name}")
    iid = db.conn.execute("INSERT INTO stock_items(name,unit) VALUES (?,?)", (name, text(d.get("unit")) or "pcs")).lastrowid
    return db.one("SELECT * FROM stock_items WHERE id=?", (iid,))


def _level(db, item_id, wh_id):
    return db.one("SELECT * FROM stock_levels WHERE item_id=? AND warehouse_id=?", (item_id, wh_id)) or \
        {"qty": 0.0, "avg_cost": 0.0}


def _new_move(db, item_id, wh_id, date, qty, kind, note="", bu=None, eid=None, value=None, fixed=0, rate=0.0, link=None):
    return db.conn.execute("INSERT INTO stock_moves(item_id,warehouse_id,date,qty,rate,bu_id,entry_id,kind,note,value,booked,fixed,link_id) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (item_id, wh_id, date, qty, rate, bu, eid, kind, note, value, value if fixed else None, fixed, link)).lastrowid


def _qty_on(db, item_id, wh_id, date):
    """What a store held of an item at the end of a day."""
    r = db.one("SELECT COALESCE(SUM(qty),0) q FROM stock_moves WHERE item_id=? AND warehouse_id=? AND date<=?", (item_id, wh_id, date))
    return round(r["q"], 6)


def _booked_now(db, mid, value, eid):
    db.conn.execute("UPDATE stock_moves SET value=?, booked=?, entry_id=? WHERE id=?", (value, value, eid, mid))


def stock_move(db, d, user=""):
    """One entry point for Receipt / Issue / Transfer / Adjustment. Costed by stockcost (FIFO or moving average, per
    warehouse, in date order); a movement dated before later ones re-costs those (with adjustment entries)."""
    from . import stockcost
    typ = d.get("type") or "Receipt"
    if typ not in STOCK_MOVE_TYPES:
        raise ValidationError("Unknown stock move type")
    date = clean_date(d.get("date"))
    note = text(d.get("note"))
    with db.tx():
        check_open(db, date)
        wh = _warehouse(db, d.get("warehouse_id") or d.get("warehouse"))
        item = _item(db, d, create=(typ == "Receipt"))
        inv = _wh_account(db, wh)

        last = {}

        def cost_of(mid, new_ids):
            r = last["r"] = stockcost.compute(db, item["id"])
            if r["short"]:              # (this movement's place, found once — not again for each older shortage)
                here = stockcost.order_key(next(m for m in r["rows"] if m["id"] == mid))
                for sh in r["short"]:
                    if stockcost.order_key(sh[0]) >= here:
                        raise ValidationError(stockcost.short_message(db, sh, new_ids))
            return r["values"][mid], r["units"][mid]

        def settle(mid, why, reuse=False):
            m = db.one("SELECT * FROM stock_moves WHERE id=?", (mid,))
            stockcost.apply(db, item["id"], stockcost.order_key(m), why=why, user=user, computed=last.get("r") if reuse else None)

        if typ == "Receipt":
            qty = num(d.get("qty"), "quantity", allow_zero=False)
            rate = num(d.get("rate"), "unit cost", allow_zero=False)
            total = to_minor(qty * rate)
            vat_code, vat, wht_code, wht = _purchase_taxes(db, d, total)
            paid = to_minor(num(d.get("paid"), "paid now"))
            vendor = text(d.get("vendor"))
            if (paid < total + vat - wht or wht) and not vendor:
                raise ValidationError("Name the supplier so the unpaid balance / withholding can be tracked")
            payable = account_code(db, d.get("payable_account") or db.acct("ap"), types=("Liability",))
            lines = _purchase_lines(db, d, inv, total, vat, wht, payable, paid)
            eid = _insert_entry(db, date=date, kind="Goods Receipt", memo=f"Received {qty:g} {item['unit']} "
                                f"{item['name']} → {wh['name']}", party=vendor, lines=lines, user=user,
                                meta={"qty": qty, "rate": rate, "item": item["name"], "warehouse": wh["name"],
                                      "vat_code": vat_code, "wht_code": wht_code}, **fx_of(d))
            if db.one("SELECT 1 FROM journal_lines WHERE entry_id=? AND currency<>'' LIMIT 1", (eid,)):
                # bought in a foreign currency: the stock is valued at what it cost in the company's currency
                total = db.one("SELECT COALESCE(SUM(debit),0) n FROM journal_lines WHERE entry_id=? AND account=?", (eid, inv))["n"]
                rate = to_major(total) / qty
            mid = _new_move(db, item["id"], wh["id"], date, qty, "Receipt", note, eid=eid, value=total, fixed=1, rate=rate * 100)
            settle(mid, f"goods received on {date}")
        elif typ == "Issue":
            qty = num(d.get("qty"), "quantity", allow_zero=False)
            bu = bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=True)
            cost_acc = account_code(db, d.get("account") or db.acct("materials"), types=("COGS", "Expense"))
            mid = _new_move(db, item["id"], wh["id"], date, -qty, "Issue", note, bu=bu)
            value, _ = cost_of(mid, (mid,))
            if value <= 0:
                raise ValidationError("Item has no cost value to issue")
            eid = _insert_entry(db, date=date, kind="Material Issue", bu_id=bu, user=user,
                                memo=f"Issued {qty:g} {item['unit']} {item['name']} from {wh['name']}",
                                lines=[{"account": cost_acc, "debit": to_major(value)},
                                       {"account": inv, "credit": to_major(value)}],
                                meta={"qty": qty, "item": item["name"], "warehouse": wh["name"]})
            _booked_now(db, mid, value, eid)
            settle(mid, f"material issued on {date}", reuse=True)     # only this issue's booking changed since
        elif typ == "Transfer":
            qty = num(d.get("qty"), "quantity", allow_zero=False)
            to = _warehouse(db, d.get("to_warehouse_id") or d.get("to_warehouse"))
            if to["id"] == wh["id"]:
                raise ValidationError("Pick a different destination warehouse")
            out = _new_move(db, item["id"], wh["id"], date, -qty, "Transfer", note)
            arr = _new_move(db, item["id"], to["id"], date, qty, "Transfer", note, link=out)
            value, _ = cost_of(out, (out,))
            to_acc = _wh_account(db, to)
            eid = None
            if to_acc != inv and value:
                eid = _insert_entry(db, date=date, kind="Stock Transfer", user=user,
                                    memo=f"Transfer {qty:g} {item['name']}: {wh['name']} → {to['name']}",
                                    lines=[{"account": to_acc, "debit": to_major(value)},
                                           {"account": inv, "credit": to_major(value)}])
            _booked_now(db, out, value, eid)
            _booked_now(db, arr, value, eid)
            settle(out, f"stock transferred on {date}")
        else:  # Adjustment: to a physical count of that day (or by a difference, as the workbook gives it)
            on_day = _qty_on(db, item["id"], wh["id"], date)
            if d.get("diff") not in (None, ""):
                diff = num(d.get("diff"), "adjustment quantity", allow_negative=True)
            else:
                diff = num(d.get("counted"), "counted quantity") - on_day
            if abs(diff) < 1e-9:
                raise ValidationError("The count matches the books. No adjustment is required.")
            given = d.get("rate") not in (None, "") and num(d.get("rate"), "unit cost") > 0
            if diff > 0 and given:          # a gain at the unit cost written on the count
                value = to_minor(diff * num(d.get("rate"), "unit cost"))
                mid = _new_move(db, item["id"], wh["id"], date, diff, "Adjustment", note, value=value, fixed=1,
                                rate=num(d.get("rate"), "unit cost") * 100)
            else:                           # a loss at its FIFO / average cost; a gain at the cost of what is on hand
                mid = _new_move(db, item["id"], wh["id"], date, diff, "Adjustment", note)
                value, unit = cost_of(mid, (mid,))
                if diff > 0 and not value and not unit:
                    raise ValidationError(f"{item['name']} has no cost in {wh['name']} yet: enter the unit cost of what was found")
            adj = db.acct("stock_adjust")
            eid = None
            if value:
                lines = ([{"account": inv, "debit": to_major(value)}, {"account": adj, "credit": to_major(value)}]
                         if diff > 0 else
                         [{"account": adj, "debit": to_major(value)}, {"account": inv, "credit": to_major(value)}])
                eid = _insert_entry(db, date=date, kind="Stock Adjustment", user=user, lines=lines,
                                    memo=f"Count adjustment — {item['name']} @ {wh['name']} ({diff:+g})")
            _booked_now(db, mid, value, eid)
            settle(mid, f"stock count of {date}")
        return eid


def materials_used(db, bu_id=None, q=None, date_from=None, date_to=None, limit=5000, item=None, source=None):
    """Every material that went into a project, wherever it was recorded: issued from a store (Material Issue), bought
    straight for the project on a journal voucher's item lines, or a direct cost on a materials account. Newest first."""
    live = f"{LIVE} AND e.reversed_by IS NULL AND e.reversal_of IS NULL"
    rows = []
    for r in db.q(f"""SELECT m.entry_id, m.date, m.bu_id, m.description, m.unit, m.qty, m.rate, m.amount, e.party, e.kind
                      FROM material_lines m JOIN journal_entries e ON e.id=m.entry_id
                      WHERE {live} AND m.warehouse_id IS NULL"""):
        rows.append({**r, "source": "bought", "from": r["party"] or ""})
    for r in db.q(f"""SELECT s.entry_id, s.date, s.bu_id, i.name AS description, i.unit, -s.qty AS qty, s.rate, s.value, w.name AS wh,
                             e.party, e.kind FROM stock_moves s JOIN stock_items i ON i.id=s.item_id JOIN warehouses w ON w.id=s.warehouse_id
                      JOIN journal_entries e ON e.id=s.entry_id WHERE {live} AND s.kind='Issue' AND s.qty<0 AND s.bu_id IS NOT NULL"""):
        value = r.pop("value")       # the issue's own FIFO / average cost, as booked
        rows.append({**r, "amount": abs(value) if value is not None else int(round(r["qty"] * r["rate"])), "source": "issued",
                     "from": r.pop("wh")})
    mats = [a["code"] for a in db.q("SELECT code FROM accounts WHERE role='materials' OR subtype='materials'")]
    if mats:
        marks = ",".join("?" * len(mats))
        for r in db.q(f"""SELECT e.id AS entry_id, e.date, e.bu_id, e.memo AS description, e.meta, e.party, e.kind,
                                 SUM(l.debit) AS amount FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id
                          WHERE {live} AND e.kind='Direct Cost' AND l.account IN ({marks}) GROUP BY e.id""", mats):
            m = json.loads(r.pop("meta") or "{}")
            qty = float(m.get("qty") or 1)
            rows.append({**r, "unit": m.get("unit") or "", "qty": qty, "rate": int(round(r["amount"] / qty)) if qty else r["amount"],
                         "source": "direct", "from": r["party"] or ""})
    bus = {b["id"]: b for b in db.q("SELECT id, code, name FROM business_units")}
    want_bu = int(bu_id) if str(bu_id or "").isdigit() else None
    needle = (q or "").strip().lower()
    want_item = (item or "").strip().lower()
    names = sorted({r["description"] for r in rows if r.get("description")}, key=str.lower)
    out = []
    for r in rows:
        if want_item and (r.get("description") or "").strip().lower() != want_item:
            continue
        if source and r["source"] != source:
            continue
        if want_bu and r["bu_id"] != want_bu:
            continue
        if date_from and r["date"] < clean_date(date_from):
            continue
        if date_to and r["date"] > clean_date(date_to):
            continue
        b = bus.get(r["bu_id"]) or {}
        r["bu_code"], r["bu_name"] = b.get("code", ""), b.get("name", "")
        if needle and needle not in " ".join(str(r.get(k) or "") for k in ("description", "from", "bu_code", "bu_name", "unit")).lower():
            continue
        r["rate"], r["amount"] = to_major(r["rate"]), to_major(r["amount"])
        out.append(r)
    out.sort(key=lambda r: (r["date"], r["entry_id"]), reverse=True)
    units = {(r.get("unit") or "").strip().lower() for r in out}
    # a quantity total only makes sense for one material in one unit (a whole project mixes m², drums, bags…)
    one = {"qty": round(sum(r["qty"] or 0 for r in out), 4), "unit": (out[0].get("unit") or "") if out else ""} \
        if want_item and out and len(units) == 1 else None
    return {"rows": out[:limit], "count": len(out), "total": round(sum(r["amount"] for r in out), 2), "one_material": one,
            "materials": names}


def stock_report(db, warehouse_id=None, q=None):
    w, p = [], []
    if warehouse_id:
        w.append("l.warehouse_id=?")
        p.append(int(warehouse_id))
    for word in str(q or "").split():          # every word must match the name, the code or the category
        w.append("(i.name LIKE ? OR i.sku LIKE ? OR i.category LIKE ?)")
        p += [f"%{word}%"] * 3
    last = ("(SELECT {} FROM stock_moves m WHERE m.item_id=i.id AND m.qty>0 AND m.kind='Receipt' "
            "ORDER BY m.date DESC, m.id DESC LIMIT 1)")
    rows = db.q("SELECT i.id AS item_id, i.name, i.sku, i.category, i.unit, i.reorder_level, w.id AS warehouse_id, w.name AS warehouse, "
                f"l.qty, l.avg_cost, l.value AS value_minor, {last.format('m.rate')} AS last_rate, {last.format('m.date')} AS last_date "
                "FROM stock_levels l JOIN stock_items i ON i.id=l.item_id "
                "JOIN warehouses w ON w.id=l.warehouse_id" + (" WHERE " + " AND ".join(w) if w else "") +
                " ORDER BY i.name, w.name", p)
    from . import stockplan
    mins = {(m["item_id"], m["warehouse_id"]): m for m in stockplan.levels(db, warehouse_id)} if rows else {}
    for r in rows:
        r["value"] = to_major(r.pop("value_minor"))
        r["avg_cost"] = to_major(r["avg_cost"])
        r["last_rate"] = to_major(r["last_rate"]) if r["last_rate"] is not None else None
        m = mins.get((r["item_id"], r["warehouse_id"]))     # the warehouse's own minimum, else the item's (stockplan)
        r["min"] = m["min"] if m else 0
        r["alert_mode"] = m["alert_mode"] if m else "both"
        r["low"] = bool(m and m["below"])
    from . import customfilter            # (here: it imports this module)
    return customfilter.keep(rows, "stock")


# ------------------------------------------------------------- BOQ & extracts
def _lines_in(raw):
    out = []
    if raw not in (None, "") and not isinstance(raw, list):
        raise ValidationError("The lines could not be read")
    for i, ln in enumerate(raw or []):
        if not isinstance(ln, dict):
            raise ValidationError(f"Line {i + 1} could not be read")
        desc = text(ln.get("description"), maxlen=2000)
        if not desc:
            continue
        out.append({"seq": i, "category": text(ln.get("category")), "sub": text(ln.get("sub")),
                    "item_no": text(ln.get("item_no")), "description": desc, "unit": text(ln.get("unit")),
                    "qty": num(ln.get("qty"), "quantity"), "price": to_minor(num(ln.get("price"), "price")),
                    "cost": to_minor(num(ln.get("cost"), "cost")), "account": text(ln.get("account"), maxlen=20),
                    "pct": _pct_in(ln.get("pct")), "style": line_style(ln.get("style"))})
    return out


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def line_style(v):
    """A BOQ / invoice line's look, as JSON text ('' = plain): b, i, u; color and bg (#rrggbb); align left|center|right."""
    if isinstance(v, str):
        try:
            v = json.loads(v) if v.strip() else {}
        except ValueError:
            v = {}
    if not isinstance(v, dict):
        return ""
    out = {k: 1 for k in ("b", "i", "u") if v.get(k)}
    for k in ("color", "bg"):
        if isinstance(v.get(k), str) and _HEX.match(v[k]):
            out[k] = v[k].lower()
    if v.get("align") in ("left", "center", "right"):
        out["align"] = v["align"]
    return json.dumps(out, sort_keys=True) if out else ""


def _pct_in(v):
    """The share of an item executed on an extract line (النسبة): 1 = 100 %; '50%', 50 or 0.5 all mean half."""
    if v in (None, ""):
        return 1.0
    s = str(v).strip().replace("%", "")
    p = num(s, "percentage")
    if p > 1 or "%" in str(v):
        p = p / 100
    if p < 0 or p > 1:
        raise ValidationError("The percentage must be between 0 and 100 %")
    return p


def _line_out(ln):
    ln = dict(ln)
    if "style" in ln:
        ln["style"] = json.loads(ln["style"]) if ln["style"] else {}
    ln["price"] = to_major(ln["price"])
    if "cost" in ln:
        ln["cost"] = to_major(ln["cost"])
    pct = ln.get("pct")
    pct = 1.0 if pct is None else pct
    ln["amount"] = round(ln["qty"] * ln["price"] * pct, 2)
    return ln


def save_boq(db, d, boq_id=None):
    lines = _lines_in(d.get("lines"))
    bu = bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"))
    with db.tx():
        vals = (bu, text(d.get("title")) or "BOQ", text(d.get("rev")) or "Rev 01", clean_date(d.get("date")),
                text(d.get("client")), text(d.get("location")), text(d.get("notes"), maxlen=4000))
        if boq_id:
            if not db.one("SELECT 1 FROM boqs WHERE id=?", (boq_id,)):
                raise ValidationError("BOQ not found")
            db.conn.execute("UPDATE boqs SET bu_id=?,title=?,rev=?,date=?,client=?,location=?,notes=? WHERE id=?",
                            vals + (boq_id,))
            db.conn.execute("DELETE FROM boq_lines WHERE boq_id=?", (boq_id,))
        else:
            boq_id = db.conn.execute("INSERT INTO boqs(bu_id,title,rev,date,client,location,notes) "
                                     "VALUES (?,?,?,?,?,?,?)", vals).lastrowid
        db.conn.executemany("INSERT INTO boq_lines(boq_id,seq,category,sub,item_no,description,unit,qty,price,cost,style) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            [(boq_id, ln["seq"], ln["category"], ln["sub"], ln["item_no"], ln["description"],
                              ln["unit"], ln["qty"], ln["price"], ln["cost"], ln["style"]) for ln in lines])
    return boq_id


def get_boq(db, boq_id):
    b = db.one("SELECT b.*, u.code AS bu_code, u.name AS bu_name FROM boqs b "
               "LEFT JOIN business_units u ON u.id=b.bu_id WHERE b.id=?", (boq_id,))
    if not b:
        return None
    b["lines"] = [_line_out(ln) for ln in db.q("SELECT * FROM boq_lines WHERE boq_id=? ORDER BY seq, id", (boq_id,))]
    b["total"] = round(sum(ln["amount"] for ln in b["lines"]), 2)
    b["cost_total"] = round(sum(ln["qty"] * ln["cost"] for ln in b["lines"]), 2)
    b["billed"] = round(sum(x["total"] for x in list_extracts(db, boq_id=boq_id) if x["status"] == "posted"), 2)
    return b


def fees_in(v):
    """Fee % per section, from a dict ({"Electrical": 10, "*": 0.12}) or text ("Electrical=10%; HVAC=8%; *=12%").
    Numbers above 1 are percents. Returns the stored JSON text ('' = no fees)."""
    if v in (None, "", {}):
        return ""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            pairs = {}
            for part in re.split(r"[;\n،]", v):
                if "=" in part or ":" in part:
                    k, _, p = part.replace(":", "=", 1).partition("=")
                    pairs[k.strip()] = p.strip()
            v = pairs
    if not isinstance(v, dict):
        raise ValidationError("Fees: section = percent, e.g. Electrical=10%; *=12%")
    out = {}
    for k, p in v.items():
        if p in (None, ""):
            continue
        x = num(str(p).replace("%", "").strip(), "fee %")
        x = x / 100 if (x > 1 or "%" in str(p)) else x
        if x < 0 or x > 5:
            raise ValidationError("A fee % must be between 0 and 500%")
        out[text(k) or "*"] = round(x, 6)
    return json.dumps(out, ensure_ascii=False, sort_keys=True) if out else ""


def fee_rows(fees_json, lines):
    """[{category, pct, base, amount}] — each section's work (sum of its lines) times its fee % ("*" for the rest)."""
    try:
        fees = json.loads(fees_json or "{}") or {}
    except ValueError:
        fees = {}
    if not fees:
        return []
    base = {}
    for ln in lines:
        base[ln.get("category") or ""] = base.get(ln.get("category") or "", 0) + ln["amount"]
    out = []
    for cat, b in base.items():
        p = fees.get(cat, fees.get("*"))
        if p:
            out.append({"category": cat, "pct": p, "base": round(b, 2), "amount": round(b * p, 2)})
    return out


def next_in_series(db, prefix):
    """The next number after a prefix left as it was suggested (INV-2026-10- → INV-2026-10-001, -002 …)."""
    n = 0
    for r in db.q("SELECT number FROM extracts WHERE number LIKE ? ESCAPE '\\'",
                  (prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",)):
        tail = r["number"][len(prefix):]
        if tail.isdigit():
            n = max(n, int(tail))
    return f"{prefix}{n + 1:03d}"


DOC_TYPES = {"I": "Invoice", "C": "Credit Note", "D": "Debit Note"}


def _note_fields(db, d, extract_id=None):
    """(document type, the invoice a credit / debit note corrects, its reason). A note names a posted invoice of the same
    customer and says why; a credit note can take back no more than that invoice still holds."""
    cur = db.one("SELECT doc_type, ref_id, eta_reason FROM extracts WHERE id=?", (extract_id,)) if extract_id else None
    dt = str(d.get("doc_type") or (cur or {}).get("doc_type") or "I").upper()[:1]
    if dt not in DOC_TYPES:
        raise ValidationError("Choose the document: an invoice, a credit note or a debit note")
    if dt == "I":
        return "I", None, ""
    ref = d.get("ref_id") if "ref_id" in d or "ref_number" not in d else None
    if not ref and d.get("ref_number"):
        r = db.one("SELECT id FROM extracts WHERE number=?", (text(d["ref_number"]),))
        if not r:
            raise ValidationError(f"Invoice {d['ref_number']} not found")
        ref = r["id"]
    ref = ref or (cur or {}).get("ref_id")
    if not ref:
        raise ValidationError(f"A {DOC_TYPES[dt].lower()} must name the invoice it corrects")
    orig = db.one("SELECT id, number, status, doc_type, customer FROM extracts WHERE id=?", (int(ref),))
    if not orig or orig["doc_type"] not in ("I", None, ""):
        raise ValidationError("Choose the original invoice (not another note)")
    if orig["status"] != "posted":
        raise ValidationError(f"Invoice {orig['number']} is still a draft. Post it before correcting it")
    if text(d.get("customer")) and orig["customer"] and text(d.get("customer")) != orig["customer"]:
        raise ValidationError(f"A note goes to the invoice's customer ({orig['customer']})")
    reason = text(d.get("eta_reason") if "eta_reason" in d else (cur or {}).get("eta_reason"), maxlen=500)
    if not reason:
        raise ValidationError("Give the reason for the note")
    return dt, orig["id"], reason


def credit_left(db, invoice_id, except_id=None):
    """What an invoice still holds for credit notes (its total with VAT, less the credit notes already issued)."""
    inv = get_extract(db, invoice_id)
    if not inv:
        return 0.0
    taken = 0.0
    for c in db.q("SELECT id FROM extracts WHERE ref_id=? AND doc_type='C' AND status='posted' AND id<>?", (invoice_id, except_id or 0)):
        taken += get_extract(db, c["id"])["gross"]
    return round(inv["gross"] - taken, 2)


def save_extract(db, d, extract_id=None):
    bu = bu_id_or_none(db, d.get("bu_id") or d.get("bu_code"), required=True)
    number = text(d.get("number"), "invoice / extract number", required=True)
    lines = _lines_in(d.get("lines"))
    boq_id = d.get("boq_id") or None
    vat_code, _ = tax_code(db, d.get("vat_code"), "vat")
    wht_code, _ = tax_code(db, d.get("wht_code"), "wht")
    old_bu = (db.one("SELECT bu_id FROM extracts WHERE id=?", (extract_id,)) or {}).get("bu_id") if extract_id else None
    with db.tx():
        prev = d.get("prev_paid")
        prev = None if prev in (None, "") else to_minor(num(prev, "previously paid"))
        seq_no = int(num(d.get("seq_no") or 0, "extract no."))
        if not seq_no and not extract_id:   # extract no. on this site: the next one
            r = db.one("SELECT COALESCE(MAX(seq_no),0) AS n, COUNT(*) AS c FROM extracts WHERE bu_id=?", (bu,))
            seq_no = max(r["n"] or 0, r["c"] or 0) + 1
        doc_type, ref_id, reason = _note_fields(db, d, extract_id)
        from . import fx
        if "currency" in d or not extract_id:      # the invoice's currency (blank = the company's) and, if fixed, its rate
            currency = fx.code(d.get("currency"))
            currency = "" if currency == fx.base(db) else currency
            fx_rate = num(d.get("fx_rate") or 0, "exchange rate") if currency else 0
        else:
            old = db.one("SELECT currency, fx_rate FROM extracts WHERE id=?", (extract_id,)) or {}
            currency, fx_rate = old.get("currency") or "", old.get("fx_rate") or 0
        if ref_id:      # a credit / debit note belongs to its invoice's business unit (its revenue and receivable)
            bu = db.one("SELECT bu_id FROM extracts WHERE id=?", (int(ref_id),))["bu_id"] or bu
            currency = db.one("SELECT currency FROM extracts WHERE id=?", (int(ref_id),))["currency"] or ""   # and its currency
            fx_rate = fx_rate if currency else 0
        vals = (bu, boq_id, number, clean_date(d.get("date")), text(d.get("customer")), vat_code, wht_code,
                text(d.get("project")), text(d.get("work")), seq_no, prev, to_minor(num(d.get("deductions") or 0, "deductions")),
                text(d.get("notes"), maxlen=2000), 1 if d.get("final") in (1, True, "1", "true", "yes", "Yes") else 0,
                fees_in(d["fees"]) if "fees" in d else
                ((db.one("SELECT fees FROM extracts WHERE id=?", (extract_id,)) or {}).get("fees") or "" if extract_id else ""))
        if extract_id:
            x = db.one("SELECT status FROM extracts WHERE id=?", (extract_id,))
            if not x:
                raise ValidationError("Progress bill not found")
            if x["status"] == "posted":
                raise ValidationError("Issued progress bills are locked. Reverse the invoice entry first.")
            db.conn.execute("UPDATE extracts SET bu_id=?,boq_id=?,number=?,date=?,customer=?,vat_code=?,wht_code=?,"
                            "project=?,work=?,seq_no=?,prev_paid=?,deductions=?,notes=?,final=?,fees=? WHERE id=?", vals + (extract_id,))
            db.conn.execute("DELETE FROM extract_lines WHERE extract_id=?", (extract_id,))
        else:
            if db.one("SELECT 1 FROM extracts WHERE number=?", (number,)):
                raise ValidationError(f"Invoice number {number} already exists")
            extract_id = db.conn.execute("INSERT INTO extracts(bu_id,boq_id,number,date,customer,vat_code,wht_code,project,work,"
                                         "seq_no,prev_paid,deductions,notes,final,fees) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals).lastrowid
        db.conn.execute("UPDATE extracts SET currency=?, fx_rate=? WHERE id=?", (currency, fx_rate, extract_id))
        db.conn.execute("UPDATE extracts SET doc_type=?, ref_id=?, eta_reason=?, einv_template=? WHERE id=?",
                        (doc_type, ref_id, reason, text(d.get("einv_template"), maxlen=40) if "einv_template" in d else
                         ((db.one("SELECT einv_template FROM extracts WHERE id=?", (extract_id,)) or {}).get("einv_template") or ""), extract_id))
        for ln in lines:   # a line may earn into its own revenue account (rent, services, compensation...)
            if ln["account"] and ln["account"] != db.acct("service_deferred"):   # billed in advance, earned month by month
                account_code(db, ln["account"], types=("Revenue",), field="revenue account")
        db.conn.executemany("INSERT INTO extract_lines(extract_id,seq,category,sub,item_no,description,unit,qty,price,account,pct,style) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            [(extract_id, ln["seq"], ln["category"], ln["sub"], ln["item_no"], ln["description"],
                              ln["unit"], ln["qty"], ln["price"], ln["account"], ln["pct"], ln["style"]) for ln in lines])
        _wip_after(db, "Sales Invoice", bu, old_bu)
    return extract_id


def _post_credit_note(db, x, date, user, ar, rev):
    """A credit note takes back part or all of an invoice: the mirror of its entry (Dr revenue and output VAT, Cr the
    customer and the withholding they would have deducted), on the same project and customer."""
    left = credit_left(db, x["ref_id"], x["id"])
    if x["gross"] > left + 0.005:
        raise ValidationError(f"The credit note ({x['gross']:,.2f}) is more than the invoice still holds ({left:,.2f})")
    orig = db.one("SELECT number FROM extracts WHERE id=?", (x["ref_id"],))
    by_acc = {}
    for ln in x["lines"]:
        a = ln.get("account") or rev
        by_acc[a] = by_acc.get(a, 0) + to_minor(ln["amount"])
    by_acc[rev] = by_acc.get(rev, 0) + to_minor(x["total"]) - sum(by_acc.values())
    lines = [{"account": a, "debit": to_major(v)} if v > 0 else {"account": a, "credit": to_major(-v)} for a, v in by_acc.items() if v]
    if x["vat"]:
        lines.append({"account": db.acct("vat_output"), "debit": x["vat"]})
    lines.append({"account": ar, "credit": x["receivable"]})
    if x["wht"]:
        lines.append({"account": db.acct("wht_receivable"), "credit": x["wht"]})
    with db.tx():
        eid = _insert_entry(db, date=date or x["date"], kind="Credit Note", **_extract_fx(db, x, date),
                            memo=f"Credit note — {x['number']} (on {orig['number']}): {x.get('eta_reason') or ''}",
                            bu_id=x["bu_id"], party=x["customer"] or x["bu_customer"] or x["bu_name"], ref=x["number"], user=user,
                            lines=lines, meta={"extract_id": x["id"], "credit_of": x["ref_id"], "vat_code": x["vat_code"], "wht_code": x["wht_code"]})
        db.conn.execute("UPDATE extracts SET status='posted', entry_id=? WHERE id=?", (eid, x["id"]))
        _wip_after(db, "Sales Invoice", x["bu_id"])
    return eid


def get_extract(db, xid):
    x = db.one("SELECT x.*, u.code AS bu_code, u.name AS bu_name, u.location AS bu_location, u.customer AS bu_customer, "
               "b.title AS boq_title "
               "FROM extracts x JOIN business_units u ON u.id=x.bu_id LEFT JOIN boqs b ON b.id=x.boq_id "
               "WHERE x.id=?", (xid,))
    if not x:
        return None
    x["lines"] = [_line_out(ln) for ln in
                  db.q("SELECT * FROM extract_lines WHERE extract_id=? ORDER BY seq, id", (xid,))]
    x["works_total"] = round(sum(ln["amount"] for ln in x["lines"]), 2)
    x["fee_rows"] = fee_rows(x.get("fees"), x["lines"])          # cost-plus: a fee % on top of each section
    x["fee_total"] = round(sum(f["amount"] for f in x["fee_rows"]), 2)
    x["fees_map"] = json.loads(x["fees"]) if x.get("fees") else {}
    x["total"] = round(x["works_total"] + x["fee_total"], 2)
    x.update(invoice_taxes(db, x))
    if x["boq_id"]:
        contract = {ln["item_no"] or ln["description"]: ln["qty"]
                    for ln in db.q("SELECT item_no, description, qty FROM boq_lines WHERE boq_id=?", (x["boq_id"],))}
        before = {}
        for r in db.q("SELECT l.item_no, l.description, l.qty FROM extract_lines l JOIN extracts e "
                      "ON e.id=l.extract_id WHERE e.boq_id=? AND e.status='posted' AND e.id<>?", (x["boq_id"], xid)):
            k = r["item_no"] or r["description"]
            before[k] = before.get(k, 0) + r["qty"]
        for ln in x["lines"]:
            k = ln["item_no"] or ln["description"]
            ln["contract_qty"] = contract.get(k)
            ln["billed_before"] = before.get(k, 0)
    return x


def invoice_taxes(db, x):
    """VAT and WHT for a sales invoice, computed on its net total."""
    _, vr = tax_code(db, x.get("vat_code"), "vat")
    _, wr = tax_code(db, x.get("wht_code"), "wht")
    net = to_minor(x["total"])
    vat, wht = tax_of(net, vr), tax_of(net, wr)
    party = x.get("customer") or ""
    return {"vat": to_major(vat), "wht": to_major(wht), "gross": to_major(net + vat),
            "receivable": to_major(net + vat - wht), "customer_name": party}


def list_extracts(db, boq_id=None, bu_id=None, status=None, date_from=None, date_to=None):
    w, p = [], []
    for cond, val in (("x.boq_id=?", boq_id), ("x.bu_id=?", bu_id), ("x.status=?", status),
                      ("x.date>=?", date_from), ("x.date<=?", date_to)):
        if val:
            w.append(cond)
            p.append(val)
    rows = db.q("SELECT x.*, u.code AS bu_code, b.title AS boq_title, "
                "(SELECT COALESCE(SUM(ROUND(qty*price*COALESCE(pct,1))),0) FROM extract_lines WHERE extract_id=x.id) AS total_minor "
                "FROM extracts x JOIN business_units u ON u.id=x.bu_id LEFT JOIN boqs b ON b.id=x.boq_id" +
                (" WHERE " + " AND ".join(w) if w else "") + " ORDER BY x.date DESC, x.id DESC", p)
    for r in rows:
        r["total"] = to_major(r.pop("total_minor"))
        if r.get("fees"):            # cost-plus: the fees on top of the work
            lines = [{"category": ln["category"], "amount": to_major(round(ln["qty"] * ln["price"] * (ln["pct"] if ln["pct"] is not None else 1)))}
                     for ln in db.q("SELECT category, qty, price, pct FROM extract_lines WHERE extract_id=?", (r["id"],))]
            r["total"] = round(r["total"] + sum(f["amount"] for f in fee_rows(r["fees"], lines)), 2)
    from . import customfilter            # (here: it imports this module)
    return customfilter.keep(rows, "extracts")


def _extract_fx(db, x, date=None):
    """A foreign-currency invoice: its currency and the rate it posts at (fixed on the invoice, else the day's), kept on it."""
    if not x.get("currency"):
        return {}
    from . import fx
    rate = x.get("fx_rate") or fx.rate_on(db, x["currency"], clean_date(date or x["date"]))
    db.conn.execute("UPDATE extracts SET fx_rate=? WHERE id=?", (rate, x["id"]))
    return {"currency": x["currency"], "rate": rate}


def post_extract(db, xid, date=None, user="", ar_account=None, revenue_account=None, from_unbilled=None):
    """Invoice executed work: Dr Receivables / Cr Revenue."""
    x = get_extract(db, xid)
    if not x:
        raise ValidationError("Progress bill not found")
    if x["status"] == "posted":
        raise ValidationError("Already invoiced")
    if x["total"] <= 0:
        raise ValidationError("The progress bill total must be greater than zero")
    over = [ln for ln in x["lines"] if ln.get("contract_qty") and ln["qty"] > 0
            and ln["billed_before"] + ln["qty"] > ln["contract_qty"] + 1e-9]
    if over:          # billing past the bill of quantities: allowed for variations, but only when confirmed
        ln = over[0]
        over_limit(f"{ln['item_no'] or ln['description']}: {ln['billed_before'] + ln['qty']:,.2f} billed in all, more than the "
                   f"{ln['contract_qty']:,.2f} in the bill of quantities" + (f" (and {len(over) - 1} more line(s))" if len(over) > 1 else "")
                   + ". Save only if a variation covers it.")
    ar = account_code(db, ar_account or db.acct("ar"), types=("Asset",))
    rev = account_code(db, revenue_account or db.acct("revenue"), types=("Revenue",))
    if (x.get("doc_type") or "I") == "C":
        return _post_credit_note(db, x, date, user, ar, rev)
    xfx = _extract_fx(db, x, date)
    lines = [{"account": ar, "debit": x["receivable"]}]
    if x["wht"]:
        lines.append({"account": db.acct("wht_receivable"), "debit": x["wht"]})
    by_acc = {}
    for ln in x["lines"]:
        a = ln.get("account") or rev
        by_acc[a] = by_acc.get(a, 0) + to_minor(ln["amount"])
    diff = to_minor(x["total"]) - sum(by_acc.values())   # rounding lands on the main revenue account
    by_acc[rev] = by_acc.get(rev, 0) + diff
    # Work already recognised as "done, not yet invoiced" (unbilled revenue / contract asset) is billed out of that
    # balance, not earned a second time: Dr Receivables / Cr Unbilled revenue, and only the rest is new revenue.
    from . import wip
    managed = wip.managed(db, x["bu_id"])          # kept up to date automatically: it adjusts once this is posted
    unbilled_acc, billed_from = db.acct("unbilled_revenue") if x["bu_id"] and not managed else None, 0
    if unbilled_acc:
        total = sum(by_acc.values())
        if from_unbilled is not None:              # restored from a file: exactly the share it was billed from before
            billed_from = max(0, min(to_minor(num(from_unbilled)), total))
        else:
            bal = db.one(f"SELECT COALESCE(SUM(l.debit-l.credit),0) AS n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                         f"WHERE {LIVE} AND l.account=? AND e.bu_id=?", (unbilled_acc, x["bu_id"]))["n"]
            if xfx:                                # the invoice is in a foreign currency: that balance in it
                bal = int(bal / xfx["rate"])
            billed_from = max(0, min(bal, total))
        if billed_from:
            keep = total - billed_from
            scaled = {a: (v * keep) // total for a, v in by_acc.items()}
            scaled[rev] = scaled.get(rev, 0) + keep - sum(scaled.values())
            by_acc = scaled
            by_acc[unbilled_acc] = by_acc.get(unbilled_acc, 0) + billed_from
    for a, v in by_acc.items():
        if v:
            lines.append({"account": a, "credit": to_major(v)} if v > 0 else {"account": a, "debit": to_major(-v)})
    if x["vat"]:
        lines.append({"account": db.acct("vat_output"), "credit": x["vat"]})
    with db.tx():
        debit_note = (x.get("doc_type") or "I") == "D"
        if debit_note:
            orig = db.one("SELECT number FROM extracts WHERE id=?", (x["ref_id"],)) or {}
        eid = _insert_entry(db, date=date or x["date"], kind="Debit Note" if debit_note else "Sales Invoice", **xfx,
                            memo=f"Debit note — {x['number']} (on {orig.get('number', '')}): {x.get('eta_reason') or ''}" if debit_note else f"Invoice — {x['number']}",
                            bu_id=x["bu_id"], party=x["customer"] or x["bu_customer"] or x["bu_name"], ref=x["number"],
                            user=user, lines=lines,
                            meta={"extract_id": xid, "vat_code": x["vat_code"], "wht_code": x["wht_code"],
                                  **({"billed_from_unbilled": to_major(billed_from)} if billed_from else {})})
        db.conn.execute("UPDATE extracts SET status='posted', entry_id=? WHERE id=?", (eid, xid))
        _wip_after(db, "Sales Invoice", x["bu_id"])
    return eid


# ====================================================================== REPORTS
LIVE = "e.status='posted'"
PARTY = "COALESCE(NULLIF(l.party,''), e.party)"  # a line's own party, else the entry's
def ref_prefix(prefix):
    """A GLOB pattern for references starting with this text (case-sensitive, so the ref index is used)."""
    return re.sub(r"([*?\[])", r"[\1]", prefix) + "*"


PARTY_IS = "l.id IN (SELECT id FROM party_lines WHERE party=?)"  # same as {PARTY_IS} but uses the indexes



_MEMO = weakref.WeakKeyDictionary()


def memo(fn):
    """Figures read from the books, reused until anything is written. Keyed on the connection's change count
    and SQLite's data_version (which moves when another connection commits) plus today's date, so a cached
    answer can never be stale; callers get their own copy."""
    @functools.wraps(fn)
    def wrap(db, *a, **kw):
        conn = getattr(db, "conn", None)
        ver = getattr(conn, "total_changes", None)
        if ver is None:                       # browser build: no cheap change counter
            return fn(db, *a, **kw)
        from . import customfilter
        if customfilter.active():             # a list narrowed by someone's own conditions: never kept, never reused
            return fn(db, *a, **kw)
        try:
            with db.lock:
                dv = conn.execute("PRAGMA data_version").fetchone()[0]
            key = (fn.__name__, a, tuple(sorted(kw.items())))
            hash(key)
        except (TypeError, AttributeError):
            return fn(db, *a, **kw)
        stamp = (ver, dv, today())
        try:
            box = _MEMO.get(db)
        except TypeError:
            return fn(db, *a, **kw)
        if not box or box[0] != stamp or box[2] is not conn:     # a restore opens a new connection: start again
            box = _MEMO[db] = (stamp, {}, conn)
        if key not in box[1]:
            if len(box[1]) > 200:
                box[1].clear()
            box[1][key] = fn(db, *a, **kw)
        return copy.deepcopy(box[1][key])
    return wrap

def _where(date_from=None, date_to=None, bu_id=None):
    w, p = [LIVE], []
    if date_from:
        w.append("e.date>=?")
        p.append(clean_date(date_from))
    if date_to:
        w.append("e.date<=?")
        p.append(clean_date(date_to))
    if bu_id not in (None, ""):
        if bu_id == "none":
            w.append("e.bu_id IS NULL")
        else:
            w.append("e.bu_id=?")
            p.append(int(bu_id))
    return " AND ".join(w), p


@memo
def account_totals(db, **kw):
    w, p = _where(**kw)
    rows = db.q(f"""SELECT a.code, a.name, a.type, a.subtype, a.normal, a.role,
                    COALESCE(t.dr,0) AS dr, COALESCE(t.cr,0) AS cr
                    FROM accounts a LEFT JOIN (SELECT l.account, SUM(l.debit) AS dr, SUM(l.credit) AS cr
                         FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id WHERE {w} GROUP BY l.account) t
                      ON t.account=a.code ORDER BY a.code""", p)
    for r in rows:
        r["net"] = r["dr"] - r["cr"]  # debit-positive
    return rows


def _day_before(d):
    return (_dt.date.fromisoformat(clean_date(d)) - _dt.timedelta(days=1)).isoformat()


def trial_balance(db, date_from=None, date_to=None, bu_id=None):
    """Opening, period movement and closing per account (debit/credit columns)."""
    opening = {r["code"]: r["net"] for r in account_totals(db, date_to=_day_before(date_from), bu_id=bu_id)} \
        if date_from else {}
    rows = []
    # closed financial years (yearend.py): their revenue and costs are retained earnings, so the income-statement
    # accounts open with this year's movements only, as on the balance sheet
    closed_to = db.one("SELECT MAX(year_end) e FROM year_closes WHERE year_end<?", (clean_date(date_from),))["e"] if date_from else None
    if closed_to:
        pl = {r["code"]: r["net"] for r in account_totals(db, date_to=closed_to, bu_id=bu_id) if r["type"] in ("Revenue", "COGS", "Expense")}
        for code, n in pl.items():
            opening[code] = opening.get(code, 0) - n
        prior = sum(pl.values())
        if prior:
            rows.append({"code": "", "name": "Retained earnings — closed years", "type": "Equity", "subtype": "earnings_closed",
                         "opening_debit": to_major(max(prior, 0)), "opening_credit": to_major(max(-prior, 0)),
                         "period_debit": 0.0, "period_credit": 0.0,
                         "debit": to_major(max(prior, 0)), "credit": to_major(max(-prior, 0))})
    for r in account_totals(db, date_from=date_from, date_to=date_to, bu_id=bu_id):
        o = opening.get(r["code"], 0)
        c = o + r["net"]
        if not (o or r["dr"] or r["cr"]):
            continue
        rows.append({"code": r["code"], "name": r["name"], "type": r["type"], "subtype": r["subtype"],
                     "opening_debit": to_major(max(o, 0)), "opening_credit": to_major(max(-o, 0)),
                     "period_debit": to_major(r["dr"]), "period_credit": to_major(r["cr"]),
                     "debit": to_major(max(c, 0)), "credit": to_major(max(-c, 0))})
    tot = {k: round(sum(r[k] for r in rows), 2) for k in
           ("opening_debit", "opening_credit", "period_debit", "period_credit", "debit", "credit")}
    return {"rows": rows, "totals": tot, "balanced": abs(tot["debit"] - tot["credit"]) < 0.005,
            "date_from": date_from, "date_to": date_to}


def _by_subtype(rows):
    m = {}
    for r in rows:
        m.setdefault(r["subtype"], []).append(r)
    return m


@memo
def income_statement(db, date_from=None, date_to=None, bu_id=None, allocate_overhead=False):
    """Revenue → Net sales → Gross profit → Operating expenses → EBITDA → D&A → EBIT
    → interest & other → EBT → income tax → Net profit."""
    rows = [r for r in account_totals(db, date_from=date_from, date_to=date_to, bu_id=bu_id)
            if r["type"] in ("Revenue", "COGS", "Expense") and (r["dr"] or r["cr"])]
    sub = _by_subtype(rows)

    def lines(subtype, sign):  # sign +1: credit-positive (income), -1: debit-positive (cost)
        return [{"code": r["code"], "name": r["name"], "amount": to_major(-r["net"] if sign > 0 else r["net"])}
                for r in sub.get(subtype, [])]

    def total(ls):
        return round(sum(x["amount"] for x in ls), 2)

    revenue = lines("operating_revenue", +1)
    discounts = lines("sales_discount", -1)
    cogs = lines("cogs", -1)
    opex_groups = [("Employee expenses", lines("employee", -1)),
                   ("General & administrative expenses", lines("general_admin", -1)),
                   ("Marketing & advertising expenses", lines("marketing", -1)),
                   ("Utilities (electricity / telephone)", lines("utilities", -1)),
                   ("Other operating expenses", lines("other_operating", -1))]
    dep, amort = lines("depreciation", -1), lines("amortization", -1)
    interest, other_nonop = lines("interest", -1), lines("other_nonoperating", -1)
    other_income = lines("other_income", +1)
    tax = lines("income_tax", -1)

    gross_sales = total(revenue)
    net_sales = round(gross_sales - total(discounts), 2)
    cost_of_sales = total(cogs)
    gross_profit = round(net_sales - cost_of_sales, 2)
    opex = round(sum(total(g) for _, g in opex_groups), 2)
    allocated = 0.0
    if allocate_overhead and bu_id not in (None, "", "none"):
        allocated = allocated_overhead(db, int(bu_id), date_from, date_to)
        opex_groups.append(("Allocated shared overhead", [{"code": "", "name": "Share of company overhead",
                                                           "amount": allocated}] if allocated else []))
        opex = round(opex + allocated, 2)
    ebitda = round(gross_profit - opex, 2)
    da = round(total(dep) + total(amort), 2)
    ebit = round(ebitda - da, 2)
    ebt = round(ebit - total(interest) - total(other_nonop) + total(other_income), 2)
    net = round(ebt - total(tax), 2)
    pct = (lambda v: round(v / net_sales, 4) if net_sales else 0)
    return {
        "date_from": date_from, "date_to": date_to, "bu_id": bu_id,
        "revenue": revenue, "gross_sales": gross_sales, "discounts": discounts, "net_sales": net_sales,
        "cogs": cogs, "cost_of_sales": cost_of_sales, "gross_profit": gross_profit,
        "opex_groups": [{"label": lbl, "lines": ls, "total": total(ls)} for lbl, ls in opex_groups],
        "operating_expenses": opex, "allocated_overhead": allocated, "ebitda": ebitda,
        "depreciation": dep, "amortization": amort, "d_and_a": da, "ebit": ebit,
        "interest": interest, "other_nonoperating": other_nonop, "other_income": other_income, "ebt": ebt,
        "tax": tax, "income_tax": total(tax), "net_profit": net,
        "margins": {"gross": pct(gross_profit), "ebitda": pct(ebitda), "ebit": pct(ebit), "net": pct(net)},
    }


BS_CURRENT_ASSETS = ["cash", "receivable", "allowance_doubtful", "inventory", "prepaid", "other_current_asset"]
BS_NONCURRENT_ASSETS = ["fixed_asset", "accumulated_depreciation", "intangible", "accumulated_amortization", "investment",
                        "other_noncurrent_asset"]
BS_CURRENT_LIABILITIES = ["payable", "accrued", "deferred_revenue", "tax_payable", "dividends_payable",
                          "other_current_liability", "short_term_debt"]
BS_NONCURRENT_LIABILITIES = ["long_term_debt", "other_noncurrent_liability"]
BS_EQUITY = ["capital", "partner_current", "reserves", "retained_earnings", "other_equity", "drawings"]


@memo
def balance_sheet(db, date_to=None, bu_id=None):
    rows = account_totals(db, date_to=date_to, bu_id=bu_id)
    sub = _by_subtype([r for r in rows if r["dr"] or r["cr"]])
    earnings = -sum(r["net"] for r in rows if r["type"] in ("Revenue", "COGS", "Expense"))

    def sect(subtypes, sign):
        out = []
        for s in subtypes:
            for r in sub.get(s, []):
                out.append({"code": r["code"], "name": r["name"], "subtype": s, "amount": to_major(sign * r["net"])})
        return out

    cur_assets = sect(BS_CURRENT_ASSETS, 1)
    nc_assets = sect(BS_NONCURRENT_ASSETS, 1)
    cur_liab = sect(BS_CURRENT_LIABILITIES, -1)
    nc_liab = sect(BS_NONCURRENT_LIABILITIES, -1)
    # funding facility: what he has fronted is owed to him; a debit balance is the company's own money in his account
    from .funders import is_role
    fund = {r["code"] for r in rows if is_role(r.get("role")) and r["type"] == "Liability"}
    for x in [x for x in cur_liab if x["code"] in fund and x["amount"] < 0]:
        cur_liab.remove(x)
        cur_assets.insert(0, {**x, "name": "Company money held in the funding partner's account", "subtype": "cash",
                              "amount": -x["amount"]})
    equity = sect(BS_EQUITY, -1)
    # closed financial years (yearend.py): their profit is retained earnings; only the profit since is current earnings
    closed_to = db.one("SELECT MAX(year_end) e FROM year_closes" + (" WHERE year_end<=?" if date_to else ""),
                       (clean_date(date_to),) if date_to else ())["e"]
    if closed_to:
        prior = -sum(r["net"] for r in account_totals(db, date_to=closed_to, bu_id=bu_id) if r["type"] in ("Revenue", "COGS", "Expense"))
        equity.append({"code": "", "name": "Retained earnings — closed years", "subtype": "earnings_closed", "amount": to_major(prior)})
        earnings -= prior
    equity.append({"code": "", "name": "Current earnings (profit to date)", "subtype": "earnings",
                   "amount": to_major(earnings)})
    t = lambda ls: round(sum(x["amount"] for x in ls), 2)  # noqa: E731
    ta = round(t(cur_assets) + t(nc_assets), 2)
    tl = round(t(cur_liab) + t(nc_liab), 2)
    te = t(equity)
    return {"date_to": date_to, "current_assets": cur_assets, "noncurrent_assets": nc_assets,
            "current_liabilities": cur_liab, "noncurrent_liabilities": nc_liab, "equity": equity,
            "total_current_assets": t(cur_assets), "total_noncurrent_assets": t(nc_assets),
            "total_current_liabilities": t(cur_liab), "total_noncurrent_liabilities": t(nc_liab),
            "total_assets": ta, "total_liabilities": tl, "total_equity": te,
            "total_liabilities_equity": round(tl + te, 2), "balanced": abs(ta - tl - te) < 0.005}


CF_OPERATING = [(("receivable", "allowance_doubtful"), "Accounts receivable"), ("inventory", "Inventory"),
                ("prepaid", "Prepaid expenses & advances"), ("other_current_asset", "Other current assets"),
                ("payable", "Accounts payable"), ("accrued", "Accrued liabilities & payroll"),
                ("deferred_revenue", "Deferred revenue (client advances)"), ("tax_payable", "Taxes payable"),
                ("other_current_liability", "Other current liabilities")]
CF_INVESTING = [("fixed_asset", "Capital expenditures (fixed assets)"), ("intangible", "Additions to intangibles"),
                ("investment", "Investments bought / (sold)"), ("other_noncurrent_asset", "Other long-term assets")]
CF_FINANCING = [("short_term_debt", "Short-term debt issued / (repaid)"),
                ("long_term_debt", "Long-term debt issued / (repaid)"),
                ("other_noncurrent_liability", "Other long-term liabilities"),
                ("capital", "Capital contributed"), ("partner_current", "Partners' current accounts"),
                (("other_equity", "reserves"), "Other equity movements"),
                (("drawings", "dividends_payable"), "Dividends / partner drawings paid")]


@memo
def cash_flow(db, date_from=None, date_to=None, bu_id=None):
    """Indirect-method statement: net income, add back non-cash, working-capital
    changes, investing and financing — reconciled to the change in cash."""
    date_to = date_to or today()
    start = {r["code"]: r for r in account_totals(db, date_to=_day_before(date_from), bu_id=bu_id)} \
        if date_from else {}
    period = account_totals(db, date_from=date_from, date_to=date_to, bu_id=bu_id)
    by_sub = {}
    for r in period:
        by_sub[r["subtype"]] = by_sub.get(r["subtype"], 0) + r["net"]
    pnl = income_statement(db, date_from, date_to, bu_id)
    # change in an asset (debit up) uses cash: -net; change in a liability/equity (credit up) adds cash: -net too.
    effect = lambda s: to_major(-sum(by_sub.get(x, 0) for x in ((s,) if isinstance(s, str) else s)))  # noqa: E731
    ops = [{"label": "(+) Depreciation", "amount": effect("accumulated_depreciation")},
           {"label": "(+) Amortization", "amount": effect("accumulated_amortization")}]
    ops += [{"label": f"(+/-) {lbl}", "amount": effect(s)} for s, lbl in CF_OPERATING]
    inv = [{"label": f"(-) {lbl}", "amount": effect(s)} for s, lbl in CF_INVESTING]
    fin = [{"label": ("(-) " if "drawings" in s else "(+/-) ") + lbl, "amount": effect(s)} for s, lbl in CF_FINANCING]
    # retained earnings moved directly (e.g. opening balances or prior-year adjustments)
    fin.append({"label": "(+/-) Retained earnings adjustments", "amount": effect("retained_earnings")})
    net_ops = round(pnl["net_profit"] + sum(x["amount"] for x in ops), 2)
    net_inv = round(sum(x["amount"] for x in inv), 2)
    net_fin = round(sum(x["amount"] for x in fin), 2)
    begin = to_major(sum(r["net"] for r in start.values() if r["subtype"] == "cash"))
    change = to_major(by_sub.get("cash", 0))
    end = round(begin + change, 2)
    computed = round(net_ops + net_inv + net_fin, 2)
    return {"date_from": date_from, "date_to": date_to, "net_income": pnl["net_profit"], "operating": ops,
            "net_operating": net_ops, "investing": inv, "net_investing": net_inv, "financing": fin,
            "net_financing": net_fin, "beginning_cash": begin, "change_in_cash": computed, "ending_cash": end,
            "reconciles": abs(computed - change) < 0.005}


def general_ledger(db, account=None, date_from=None, date_to=None, bu_id=None, party=None, limit=500, offset=0, group=None):
    """An account's (or several accounts') lines with a running balance. limit=None: every line. The opening, running
    balance and closing always count every line before and in the period (a contact's own when a contact is chosen),
    whatever part of the list is returned."""
    w, p = _where(date_from=date_from, date_to=date_to, bu_id=bu_id)
    acc = None
    if group:                                # every account under a group of the chart, however deep
        if not db.one("SELECT 1 FROM account_groups WHERE code=?", (group,)):
            raise ValidationError(f"Unknown group {group}")
        codes = group_accounts(db, group) or ["\u0000"]
        w += f" AND l.account IN ({','.join('?' * len(codes))})"
        p += codes
    if account and len(str(account)) == 1:  # account-class filter, e.g. "5" = all 5000s
        w += " AND l.account LIKE ?"
        p.append(f"{account}%")
        account = None
    if account:
        acc = db.one("SELECT * FROM accounts WHERE code=?", (account,))
        if not acc:
            raise ValidationError("Unknown account")
        w += " AND l.account=?"
        p.append(account)
    if party:
        w += f" AND {PARTY_IS}"
        p.append(party)
    opening = 0
    if acc and date_from:
        w0, p0 = _where(date_to=_day_before(date_from), bu_id=bu_id)
        if party:                    # a contact's ledger opens at what that contact owed, not the whole account
            w0 += f" AND {PARTY_IS}"
            p0 = p0 + [party]
        r = db.one(f"SELECT COALESCE(SUM(l.debit-l.credit),0) AS n FROM journal_lines l JOIN journal_entries e "
                   f"ON e.id=l.entry_id WHERE l.account=? AND {w0}", [account] + p0)
        opening = r["n"]
    sign = 1 if (not acc or acc["normal"] == "Debit") else -1
    rows = db.q(f"""SELECT e.id, e.date, e.kind, e.memo, {PARTY} AS party, b.code AS bu_code, l.account, a.name AS account_name,
                    l.debit, l.credit, l.currency, l.fx_amount, l.fx_rate FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                    JOIN accounts a ON a.code=l.account LEFT JOIN business_units b ON b.id=e.bu_id
                    WHERE {w} ORDER BY e.date, e.id, l.id LIMIT ? OFFSET ?""", p + [-1 if limit is None else int(limit), int(offset)])
    run = opening
    if offset:                       # a later page: carry what the lines before it added
        run += db.one(f"""SELECT COALESCE(SUM(n),0) n FROM (SELECT l.debit-l.credit n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                          JOIN accounts a ON a.code=l.account LEFT JOIN business_units b ON b.id=e.bu_id
                          WHERE {w} ORDER BY e.date, e.id, l.id LIMIT ?)""", p + [int(offset)])["n"]
    for r in rows:
        run += r["debit"] - r["credit"]
        r["debit"], r["credit"] = to_major(r["debit"]), to_major(r["credit"])
        r["balance"] = to_major(sign * run) if acc else None
    closing = run
    if acc and limit is not None and (offset or len(rows) >= int(limit)):    # part of the list: the closing counts all of it
        closing = opening + db.one(f"""SELECT COALESCE(SUM(l.debit-l.credit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                                       JOIN accounts a ON a.code=l.account LEFT JOIN business_units b ON b.id=e.bu_id WHERE {w}""", p)["n"]
    return {"account": acc, "opening": to_major(sign * opening), "rows": rows,
            "closing": to_major(sign * closing) if acc else None}


def party_balance(db, account, party):
    r = db.one(f"SELECT COALESCE(SUM(l.credit-l.debit),0) AS n FROM journal_lines l JOIN journal_entries e "
               f"ON e.id=l.entry_id WHERE {LIVE} AND l.account=? AND {PARTY_IS}", (account, party))
    return to_major(r["n"])


_OPEN_ITEM_SUBTYPES = ("receivable", "payable", "accrued", "prepaid", "deferred_revenue", "short_term_debt",
                       "long_term_debt", "other_current_liability", "other_noncurrent_liability", "other_current_asset",
                       "other_noncurrent_asset")


def contact_summary(db, name, limit=30):
    """What the books say about one contact: open balances per account, totals and the latest vouchers."""
    rows = db.q(f"""SELECT l.account, a.name, a.name_ar, a.type, a.subtype, a.role, SUM(l.debit) dr, SUM(l.credit) cr
                    FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                    WHERE {LIVE} AND {PARTY_IS} GROUP BY l.account ORDER BY l.account""", (name,))
    balances, spent, earned = [], 0, 0
    for r in rows:
        if r["type"] in ("COGS", "Expense") or r["subtype"] in ("fixed_asset", "inventory"):
            spent += r["dr"] - r["cr"]
        elif r["type"] == "Revenue":
            earned += r["cr"] - r["dr"]
        elif r["subtype"] in _OPEN_ITEM_SUBTYPES and r["role"] not in ("vat_input", "wht_receivable") and r["dr"] != r["cr"]:
            bal = r["dr"] - r["cr"]
            liab = r["type"] == "Liability"
            balances.append({"account": r["account"], "name": r["name"], "name_ar": r["name_ar"], "type": r["type"],
                             "we_owe": to_major(-bal) if liab else 0, "owes_us": 0 if liab else to_major(bal)})
    recent = db.q(f"""SELECT DISTINCT e.id, e.date, e.kind, e.memo, e.ref FROM journal_entries e
                      JOIN journal_lines l ON l.entry_id=e.id WHERE {LIVE} AND {PARTY_IS}
                      ORDER BY e.date DESC, e.id DESC LIMIT ?""", (name, int(limit)))
    for r in recent:
        t = db.one("SELECT SUM(debit) n FROM journal_lines WHERE entry_id=?", (r["id"],))
        r["amount"] = to_major(t["n"] or 0)
    n = db.one(f"""SELECT COUNT(DISTINCT e.id) n, MIN(e.date) first, MAX(e.date) last FROM journal_entries e
                   JOIN journal_lines l ON l.entry_id=e.id WHERE {LIVE} AND {PARTY_IS}""", (name,))
    return {"balances": balances, "spent": to_major(spent), "earned": to_major(earned), "vouchers": n["n"],
            "first": n["first"], "last": n["last"], "recent": recent}


def party_balances(db, subtypes=("payable",), accounts=None, date_to=None):
    """Open balances per party on liability accounts (suppliers, workers)."""
    if accounts:
        codes = list(accounts)
    else:
        codes = [r["code"] for r in db.q("SELECT code FROM accounts WHERE subtype IN (%s)" %
                                          ",".join("?" * len(subtypes)), subtypes)]
    if not codes:
        return []
    w, p = _where(date_to=date_to)
    rows = db.q(f"""SELECT {PARTY} AS party, l.account, SUM(l.credit) AS owed, SUM(l.debit) AS paid,
                    SUM(l.credit-l.debit) AS balance, MAX(e.date) AS last_date
                    FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                    WHERE {w} AND l.account IN ({",".join("?" * len(codes))})
                    GROUP BY 1, l.account HAVING balance<>0 ORDER BY balance DESC""", p + codes)
    fxs = {}       # what is still owed in each foreign currency, per contact and account
    for f in db.q(f"""SELECT {PARTY} AS party, l.account, l.currency, SUM(CASE WHEN l.credit>0 THEN l.fx_amount ELSE -l.fx_amount END) n
                       FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                       WHERE {w} AND l.currency<>'' AND l.account IN ({",".join("?" * len(codes))})
                       GROUP BY 1, l.account, l.currency HAVING n<>0""", p + codes):
        fxs.setdefault((f["party"], f["account"]), {})[f["currency"]] = to_major(f["n"])
    for r in rows:
        for k in ("owed", "paid", "balance"):
            r[k] = to_major(r[k])
        r["fx"] = fxs.get((r["party"], r["account"])) or {}
    from . import customfilter            # (here: it imports this module)
    return customfilter.keep(rows, "parties")


def shared_overhead_total(db, date_from=None, date_to=None):
    """Operating expenses booked without a business unit (the indirect-cost pool)."""
    w, p = _where(date_from=date_from, date_to=date_to, bu_id="none")
    r = db.one(f"""SELECT COALESCE(SUM(l.debit-l.credit),0) AS n FROM journal_lines l
                   JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                   WHERE {w} AND a.subtype IN ('employee','general_admin','marketing','utilities','other_operating')
                     AND COALESCE(json_extract(e.meta,'$.no_alloc'),0)<>1""",   # company costs kept out of the projects
               p)
    return to_major(r["n"])


def allocated_overhead(db, bu_id, date_from=None, date_to=None):
    """This project's share of the shared costs, by the allocation rules (allocation.py)."""
    from . import allocation
    x = allocation.allocate(db, date_from, date_to)["by_bu"].get(int(bu_id)) or {}
    return to_major((x.get("billable") or 0) + (x.get("non_billable") or 0))


DONE_STATUSES = ("completed", "complete", "closed", "done", "cancelled")


def expected_cost_default(db):
    """The company's expected cost as a share of revenue, for projects without their own estimate (blank = none)."""
    v = db.setting("expected_cost_pct")
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def expected_cost(b, rev, actual, default_pct):
    """A project's expected total cost (a fixed amount, or its expected revenue — or revenue to date, if more — times
    its expected cost %), the cost still to come before it is finished, and how complete it is by cost. Money is in
    minor units in, major units out."""
    method = b.get("cost_method") or "pct"
    pct = b.get("expected_cost_pct")
    pct = default_pct if pct is None else pct
    if method == "fixed":
        total = b.get("expected_cost") or 0
    else:
        total = int(round(max(b.get("expected_revenue") or 0, rev) * pct)) if pct else 0
    done = str(b.get("status") or "").strip().lower().startswith(DONE_STATUSES)
    still = 0 if done or not total else max(0, total - actual)
    return {"expected_revenue": to_major(b.get("expected_revenue") or 0), "cost_method": method,  # noqa: E501
            "expected_cost_pct": b.get("expected_cost_pct"), "expected_cost": to_major(b.get("expected_cost") or 0),
            "expected_total_cost": to_major(total), "cost_to_date": to_major(actual), "cost_still_to_come": to_major(still),
            "pct_complete": round(min(1, actual / total), 4) if total else None}


@memo
def bu_summaries(db, date_from=None, date_to=None, status=None, q=None):
    w, p = _where(date_from=date_from, date_to=date_to)
    agg = {r["bid"]: r for r in db.q(f"""
        SELECT e.bu_id AS bid,
          SUM(CASE WHEN a.type='Revenue' THEN l.credit-l.debit ELSE 0 END) AS revenue,
          SUM(CASE WHEN a.type='COGS' THEN l.debit-l.credit ELSE 0 END) AS cogs,
          SUM(CASE WHEN a.type='Expense' THEN l.debit-l.credit ELSE 0 END) AS direct_overhead,
          SUM(CASE WHEN a.subtype='receivable' THEN l.debit-l.credit ELSE 0 END) AS receivable,
          SUM(CASE WHEN a.subtype='deferred_revenue' THEN l.credit-l.debit ELSE 0 END) AS advances,
          SUM(CASE WHEN e.kind='Receipt Voucher' AND a.subtype='cash' THEN l.debit ELSE 0 END) AS collected
        FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
        WHERE {w} AND e.bu_id IS NOT NULL GROUP BY e.bu_id""", p)}
    # money the client actually paid into the project: money in on entries settled with the client (receipts against
    # invoices, advances, sales paid on the spot, cheques when they clear), less refunds to them — whether it landed in
    # cash / a bank, the funding partner's account (when it replaces the bank) or with a partner personally; not
    # partner money, loans, refundable deposits or supplier payments
    from .funders import role_sql
    FUNDER_A = role_sql("a")
    paid = {r["bid"]: r for r in db.q(f"""
        SELECT x.bid, SUM(x.cash) AS n, SUM(x.bank) AS bank, SUM(x.funder) AS funder, SUM(x.partner) AS partner FROM (
          SELECT e.bu_id AS bid, e.id,
            SUM(CASE WHEN a.subtype IN ('cash','partner_current') OR {FUNDER_A} THEN l.debit-l.credit ELSE 0 END) AS cash,
            SUM(CASE WHEN a.subtype='cash' THEN l.debit-l.credit ELSE 0 END) AS bank,
            SUM(CASE WHEN {FUNDER_A} THEN l.debit-l.credit ELSE 0 END) AS funder,
            SUM(CASE WHEN a.subtype='partner_current' AND NOT {FUNDER_A} THEN l.debit-l.credit ELSE 0 END) AS partner,
            MAX(CASE WHEN a.subtype IN ('receivable','deferred_revenue','operating_revenue','sales_discount') AND a.code<>? THEN 1 ELSE 0 END) AS client
          FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
          WHERE {w} AND e.bu_id IS NOT NULL GROUP BY e.bu_id, e.id) x
        WHERE x.client=1 GROUP BY x.bid""", [db.acct("negotiation_income")] + p)}   # a supplier discount is not the client's money
    # the VAT share of what clients pay, per project (output VAT over sales + VAT billed): taken out of "paid", since the
    # tax is collected for the state and is not the company's fee
    vat_share = {r["bid"]: (r["vat"] / (r["rev"] + r["vat"]) if (r["rev"] or 0) + (r["vat"] or 0) > 0 and (r["vat"] or 0) > 0 else 0) for r in db.q(f"""
        SELECT e.bu_id AS bid,
          SUM(CASE WHEN l.account=? THEN l.credit-l.debit ELSE 0 END) AS vat,
          SUM(CASE WHEN a.subtype IN ('operating_revenue','sales_discount') AND a.code<>? THEN l.credit-l.debit ELSE 0 END) AS rev
        FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
        WHERE {w} AND e.bu_id IS NOT NULL GROUP BY e.bu_id""", [db.acct("vat_output"), db.acct("negotiation_income")] + p)}
    from . import allocation
    alloc_by = allocation.allocate(db, date_from, date_to)["by_bu"]
    savings, kept = {}, {}
    for r in db.q(f"SELECT e.bu_id, e.meta FROM journal_entries e WHERE {w} AND kind IN ('Direct Cost','Supplier Discount')", p):
        m = json.loads(r["meta"] or "{}")
        savings[r["bu_id"]] = savings.get(r["bu_id"], 0) + to_minor(m.get("savings", 0))
        if m.get("negotiation") == "keep":
            kept[r["bu_id"]] = kept.get(r["bu_id"], 0) + to_minor(m.get("savings", 0))
    bw, bp = [], []
    if status:
        bw.append("status=?")
        bp.append(status)
    if q:
        bw.append("(code LIKE ? OR name LIKE ? OR location LIKE ?)")
        bp += [f"%{q}%"] * 3
    out = []
    default_pct = expected_cost_default(db)
    # optional: a project's own overhead (expense lines tagged to it) also earns the cost-plus fee
    plus_ovh = (db.setting("costplus_overhead") or "no").lower() in ("yes", "1", "true")
    for b in db.q("SELECT * FROM business_units" + (" WHERE " + " AND ".join(bw) if bw else "") + " ORDER BY code", bp):
        a = agg.get(b["id"], {})
        rev, cogs, ovh = (a.get(k) or 0 for k in ("revenue", "cogs", "direct_overhead"))
        collected = a.get("collected") or 0
        ab = alloc_by.get(b["id"]) or {}
        alloc_bill, alloc = ab.get("billable") or 0, (ab.get("billable") or 0) + (ab.get("non_billable") or 0)
        if b["basis"] == "Cost-Plus %":       # the fee is charged on direct costs and on shared costs billable to clients
            expected = int(round((cogs + alloc_bill + (ovh if plus_ovh else 0)) * (1 + (b["fee_pct"] or 0))))
        elif b["basis"] == "Fixed Price":
            expected = b["contract_value"] or 0
        else:
            expected = collected
        pr = paid.get(b["id"]) or {}
        paid_gross, actual = pr.get("n") or 0, cogs + ovh   # the project's own costs, not the shared overhead
        paid_vat = int(round(paid_gross * min(vat_share.get(b["id"], 0), 0.5))) if paid_gross > 0 else 0
        client_paid = paid_gross - paid_vat
        if b["basis"] == LIVE_FEE:
            expected = client_paid       # billed as paid: nothing waits to be collected
        net = rev - cogs - ovh - alloc
        est = expected_cost(b, rev, actual, default_pct)
        out.append({**b, **est, "contract_value": to_major(b["contract_value"]), "revenue": to_major(rev),
                    "cogs": to_major(cogs), "direct_overhead": to_major(ovh), "allocated_overhead": to_major(alloc),
                    "allocated_billable": to_major(alloc_bill), "allocated_non_billable": to_major(alloc - alloc_bill),
                    "gross_profit": to_major(rev - cogs), "net_profit": to_major(net),
                    "margin": round(net / rev, 4) if rev else 0, "receivable": to_major(a.get("receivable") or 0),
                    "advances": to_major(a.get("advances") or 0), "collected": to_major(collected),
                    "expected_billing": to_major(expected), "to_collect": 0.0 if b["basis"] == LIVE_FEE else to_major(max(expected - collected, 0)),
                    "savings": to_major(savings.get(b["id"], 0)), "savings_kept": to_major(kept.get(b["id"], 0)),
                    "client_paid": to_major(client_paid), "client_paid_gross": to_major(paid_gross), "client_paid_vat": to_major(paid_vat),
                    "actual_costs": to_major(actual),
                    "paid_into": {k: to_major(pr.get(k) or 0) for k in ("bank", "funder", "partner")},   # where it landed
                    "live_gap": to_major(client_paid - actual),
                    "live_fee": round((client_paid - actual) / actual, 4) if actual > 0 else None,
                    "live_margin": round((client_paid - actual) / client_paid, 4) if client_paid > 0 else None})
    from . import customfilter            # (here: it imports this module)
    return customfilter.keep(out, "bus")


def negotiations(db, date_from=None, date_to=None, bu_id=None):
    """Every negotiated discount, per business unit: kept as company earnings or passed on as a lower cost."""
    w, p = _where(date_from=date_from, date_to=date_to, bu_id=bu_id)
    keep_acc = db.acct("negotiation_income")
    rows = []
    for e in db.q(f"""SELECT e.id, e.date, e.kind, e.memo, e.party, e.ref, e.meta, e.bu_id, b.code AS bu_code,
                             b.name AS bu_name FROM journal_entries e LEFT JOIN business_units b ON b.id=e.bu_id
                      WHERE {w} AND (e.kind IN ('Direct Cost','Supplier Discount') OR EXISTS
                            (SELECT 1 FROM journal_lines x WHERE x.entry_id=e.id AND x.account=?))
                      ORDER BY e.date, e.id""", p + [keep_acc]):
        meta = json.loads(e["meta"] or "{}")
        savings = to_minor(meta.get("savings", 0))
        if e["kind"] in ("Direct Cost", "Supplier Discount"):
            mode = meta.get("negotiation") or "pass"
        else:   # any other entry booked straight to Negotiation Earnings
            n = db.one("SELECT COALESCE(SUM(credit-debit),0) AS n FROM journal_lines WHERE entry_id=? AND account=?",
                       (e["id"], keep_acc))["n"]
            savings, mode = n, "keep"
        if not savings:
            continue
        quoted = to_minor(meta.get("quoted", 0))
        rows.append({"id": e["id"], "date": e["date"], "kind": e["kind"], "memo": e["memo"], "party": e["party"],
                     "deal_ref": meta.get("deal_ref") or e["ref"] or "", "bu_id": e["bu_id"], "bu_code": e["bu_code"] or "",
                     "bu_name": e["bu_name"] or "", "quoted": to_major(quoted), "savings": to_major(savings),
                     "mode": mode, "kept": to_major(savings if mode == "keep" else 0),
                     "passed": to_major(savings if mode == "pass" else 0)})
    by_bu = {}
    for r in rows:
        g = by_bu.setdefault(r["bu_id"], {"bu_id": r["bu_id"], "bu_code": r["bu_code"], "bu_name": r["bu_name"],
                                          "quoted": 0, "savings": 0, "kept": 0, "passed": 0, "count": 0})
        g["count"] += 1
        for k in ("quoted", "savings", "kept", "passed"):
            g[k] = round(g[k] + r[k], 2)
    modes = {b["id"]: b["negotiation"] or "" for b in db.q("SELECT id, negotiation FROM business_units")}
    for g in by_bu.values():
        g["setting"] = modes.get(g["bu_id"], "")
    total = {k: round(sum(r[k] for r in rows), 2) for k in ("quoted", "savings", "kept", "passed")}
    return {"rows": rows, "by_bu": sorted(by_bu.values(), key=lambda g: g["bu_code"]), "total": total,
            "account": keep_acc}


def replace_doc(db, eid, fn, d, user="", **kw):
    """Re-enter a document through its own form: the entry keeps its number and history, its lines are rebuilt."""
    with db.tx():
        e = get_entry(db, eid)
        if not e:
            raise ValidationError("Entry not found")
        if e["status"] != "posted" or e["reversed_by"] or e["reversal_of"]:
            raise ValidationError("Only live entries can be edited")
        check_open(db, e["date"])
        if db.one("SELECT 1 FROM stock_moves WHERE entry_id=?", (eid,)) or \
                db.one("SELECT 1 FROM extracts WHERE entry_id=?", (eid,)):
            raise ValidationError("This entry belongs to a stock movement or invoice. Reverse it and re-enter.")
        _owned_guard(db, eid)
        _REPLACE["eid"] = eid
        try:
            out = fn(db, d, user, **kw)
        finally:
            _REPLACE["eid"] = None
        if out != eid:
            raise ValidationError("This document could not be edited in place")
        return eid


def doc_form(db, e):
    """The values of the form an entry was typed into (for editing it the same way)."""
    meta = e["meta"] or {}
    if meta.get("form"):
        form = {**meta["form"], "date": e["date"]}
        if e["kind"] == "Direct Cost":   # how the discount was actually booked, even if the form did not say
            form["negotiation"] = meta.get("negotiation") or form.get("negotiation") or "pass"
        return form
    lines = e["lines"]
    types = {a["code"]: a for a in db.q("SELECT code, type, subtype FROM accounts")}
    cash = [ln for ln in lines if ln["credit"] and types.get(ln["account"], {}).get("subtype") == "cash"]
    paid = round(sum(ln["credit"] for ln in cash), 2)
    base = {"date": e["date"], "bu_id": e["bu_id"] or "", "method": meta.get("method", ""),
            "paid_from": cash[0]["account"] if cash else "", "vat_code": meta.get("vat_code", ""),
            "wht_code": meta.get("wht_code", "")}
    if e["kind"] == "Direct Cost":
        cost = next((ln for ln in lines if ln["debit"] and types.get(ln["account"], {}).get("type") == "COGS"), None)
        return {**base, "account": cost["account"] if cost else "", "description": e["memo"], "vendor": e["party"],
                "deal_ref": meta.get("deal_ref", ""), "deal_status": meta.get("deal_status", ""),
                "qty": meta.get("qty", 1), "unit": meta.get("unit", ""), "rate": meta.get("rate", ""),
                "savings": meta.get("savings", 0) or "", "paid": paid,
                "negotiation": meta.get("negotiation") or ("pass" if meta.get("savings") else "")}
    if e["kind"] == "Operating Expense":
        ex = next((ln for ln in lines if ln["debit"] and types.get(ln["account"], {}).get("type") == "Expense"), None)
        return {**base, "account": ex["account"] if ex else "", "description": e["memo"], "vendor": e["party"],
                "amount": ex["debit"] if ex else "", "paid": paid}
    if e["kind"] == "Supplier Discount":
        dr = next((ln for ln in lines if ln["debit"]), None)
        cr = next((ln for ln in lines if ln["credit"]), None)
        return {"date": e["date"], "party": e["party"], "against": dr["account"] if dr else "",
                "amount": dr["debit"] if dr else "", "credit_account": cr["account"] if cr else "",
                "bu_id": e["bu_id"] or "", "deal_ref": meta.get("deal_ref", ""), "memo": e["memo"]}
    return None


def deals(db, date_to=None):
    """What is owed per deal (Deal Ref): one supplier can have several deals, each booked and paid on its own. Every
    live entry naming the deal counts — costs, partial payments in the same entry, later payment vouchers, discounts.
    Booked = paid + still owed; paid = money out of cash / bank / the funding account; still owed = the deal's balance
    on payable accounts (taxes excluded). Payables not tagged with any deal show as one "no deal ref" line."""
    from .funders import ROLE_SQL
    money = {r["code"] for r in db.q(f"SELECT code FROM accounts WHERE subtype='cash' OR {ROLE_SQL}")}
    owe = {r["code"] for r in db.q("SELECT code FROM accounts WHERE type='Liability' AND COALESCE(role,'') NOT IN "
                                   f"('vat_output','vat_input','wht_payable','wht_receivable') AND NOT {ROLE_SQL} "
                                   "AND subtype IN ('payable','accrued')")}
    cond, p = (f"{LIVE} AND e.date<=?", [date_to]) if date_to else (LIVE, [])
    m = {}
    rows = db.q(f"""SELECT e.id, e.date, e.party, e.meta, e.kind, b.code AS bu_code, l.account, l.debit, l.credit,
                           COALESCE(NULLIF(l.party,''), e.party) AS line_party
                    FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id LEFT JOIN business_units b ON b.id=e.bu_id
                    WHERE {cond} AND json_extract(e.meta,'$.deal_ref')<>'' ORDER BY e.date, e.id""", p)
    seen = set()
    for r in rows:
        meta = json.loads(r["meta"] or "{}")
        ref = text(meta.get("deal_ref"))
        g = m.setdefault(ref, {"ref": ref, "vendor": "", "bu_code": "", "quoted": 0, "savings": 0, "entries": 0, "status": "",
                               "paid": 0, "owed": 0, "first": r["date"], "last": r["date"]})
        if r["id"] not in seen:
            seen.add(r["id"])
            g["entries"] += 1
            g["quoted"] += to_minor(meta.get("quoted", 0)) if r["kind"] in ("Direct Cost", "Supplier Discount") else 0
            g["savings"] += to_minor(meta.get("savings", 0)) if r["kind"] in ("Direct Cost", "Supplier Discount") else 0
            g["status"] = meta.get("deal_status") or g["status"]
            g["last"] = r["date"]
        if r["account"] in money:
            g["paid"] += r["credit"] - r["debit"]
        elif r["account"] in owe:
            g["owed"] += r["credit"] - r["debit"]
            g["vendor"] = g["vendor"] or r["line_party"] or ""
        g["vendor"] = g["vendor"] or r["party"] or ""
        g["bu_code"] = g["bu_code"] or r["bu_code"] or ""
    out = []
    for g in m.values():
        booked = g["paid"] + g["owed"]
        g["state"] = ("settled" if g["owed"] <= 0 and booked > 0 else "not_paid" if g["paid"] <= 0 else "stages")
        g.update(cost=to_major(g["quoted"] - g["savings"]), quoted=to_major(g["quoted"]), savings=to_major(g["savings"]),
                 booked=to_major(booked), paid=to_major(g["paid"]), owed=to_major(g["owed"]))
        out.append(g)
    tagged = sum(to_minor(g["owed"]) for g in out)
    total = db.one(f"""SELECT COALESCE(SUM(l.credit-l.debit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                       WHERE {cond} AND l.account IN ({','.join('?' * len(owe)) or "''"})""", p + sorted(owe))["n"]
    return {"deals": sorted(out, key=lambda g: g["ref"]), "untagged": to_major(total - tagged),
            "total_owed": to_major(total)}


def ownership(db, date_from=None, date_to=None):
    """share(partner_id, bu_id, fallback): a partner's share of a project's profit. Where stages of the project have
    their own ownership, the project's profit is split over its stages by the direct cost booked to each stage (costs
    without a stage count under the project as a whole), and each stage uses its own shares."""
    shares = {(r["partner_id"], r["bu_id"]): r["share"] for r in db.q("SELECT * FROM partner_shares")}
    staged = {}
    for r in db.q("SELECT * FROM partner_stage_shares"):
        staged.setdefault(r["bu_id"], {})[(r["partner_id"], r["stage"])] = r["share"]
    weights = {}
    if staged:
        w, p = _where(date_from=date_from, date_to=date_to)
        rows = db.q(f"""SELECT e.bu_id, COALESCE(json_extract(e.meta,'$.stage'),'') AS stage, SUM(l.debit-l.credit) n
                        FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                        WHERE {w} AND a.type='COGS' AND e.bu_id IN ({','.join('?' * len(staged))}) GROUP BY 1, 2""", p + list(staged))
        for r in rows:
            weights.setdefault(r["bu_id"], {})[r["stage"] or ""] = max(0, r["n"] or 0)
        for b, ws in weights.items():
            tot = sum(ws.values())
            weights[b] = {k: v / tot for k, v in ws.items()} if tot else {"": 1.0}

    def share(pid, bu_id, fallback):
        base = shares.get((pid, bu_id), fallback)
        ws = weights.get(bu_id)
        if not ws:
            return base
        st = staged.get(bu_id, {})
        return sum(wt * st.get((pid, stage), base) for stage, wt in ws.items())
    return share


@memo
def partner_report(db, date_from=None, date_to=None):
    partners = db.q("SELECT * FROM partners ORDER BY id")
    shares = {(r["partner_id"], r["bu_id"]): r["share"] for r in db.q("SELECT * FROM partner_shares")}
    bus = bu_summaries(db, date_from, date_to)
    company = income_statement(db, date_from, date_to, bu_id="none")["net_profit"]
    alloc_total = round(sum(b["allocated_overhead"] for b in bus), 2)
    company_after_alloc = round(company + alloc_total, 2)  # overhead already charged to units is not double-counted
    reserve = float(db.setting("reserve_pct", "0.4") or 0)
    draws = {r["party"]: to_major(r["n"]) for r in db.q(
        f"SELECT {PARTY} AS party, SUM(l.debit-l.credit) AS n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
        f"JOIN accounts a ON a.code=l.account WHERE {LIVE} AND a.subtype='drawings' AND e.kind<>'Profit Appropriation'"
        + (" AND e.date>=?" if date_from else "") + (" AND e.date<=?" if date_to else "") + " GROUP BY 1",
        [x for x in (date_from, date_to) if x])}
    from . import equity
    ratio, typed = equity.profit_ratios(db, partners)
    ratio = {int(k): v for k, v in ratio.items()}
    # agreed interest on capital comes out of the company's result before the rest is shared
    cap_int = equity.capital_interest(db, date_from, date_to)
    pool = round(company_after_alloc - sum(cap_int.values()), 2)
    # profit is only distributable once BILLED: a project's work done but not invoiced (unbilled revenue) carries
    # its share of the profit, which is earned but held in the company until the invoice goes out
    from .partner_pack import billed_profit, wip_accounts
    wip_codes = wip_accounts(db)
    w, p = _where(date_to=date_to)
    wip_bu = {r["bu_id"]: to_major(r["n"]) for r in db.q(
        f"""SELECT e.bu_id, SUM(l.debit-l.credit) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
            WHERE {w} AND l.account IN ({','.join('?' * len(wip_codes)) or "''"}) GROUP BY e.bu_id""", p + list(wip_codes))}
    billed = {b["id"]: billed_profit(b["revenue"], wip_bu.get(b["id"], 0), b["net_profit"]) for b in bus}
    own = ownership(db, date_from, date_to)
    rows = []
    for pt in partners:
        per, total, total_billed = {}, 0.0, 0.0
        for b in bus:
            s = own(pt["id"], b["id"], ratio[pt["id"]])
            per[b["code"]] = {"share": s, "profit": round(b["net_profit"] * s, 2), "billed": round(billed[b["id"]] * s, 2)}
            total += b["net_profit"] * s
            total_billed += billed[b["id"]] * s
        rest = pool * ratio[pt["id"]] + cap_int.get(pt["name"], 0)
        total += rest
        total_billed += rest
        distributable = total_billed * (1 - reserve) if total_billed > 0 else total_billed
        drawn = draws.get(pt["name"], 0)
        rows.append({"id": pt["id"], "name": pt["name"], "default_share": pt["default_share"], "units": per,
                     "active": pt["active"], "joined": pt["joined"], "left_on": pt["left_on"],
                     "capital_interest_rate": pt["capital_interest"], "loan_rate": pt["loan_rate"], "notes": pt["notes"],
                     "profit_ratio": ratio[pt["id"]], "profit_share_typed": pt["profit_share"], "salary": to_major(pt["salary"] or 0),
                     "drawings_interest": pt["drawings_interest"],
                     "capital_interest": cap_int.get(pt["name"], 0), "accounts": equity.balances(db, pt["name"], date_to),
                     "profit_share": round(total, 2), "billed_share": round(total_billed, 2),
                     "held_in_wip": round(total - total_billed, 2), "distributable": round(distributable, 2), "drawn": drawn,
                     "balance": round(distributable - drawn, 2)})
    checks = {b["code"]: round(sum(own(pt["id"], b["id"], ratio[pt["id"]]) for pt in partners), 4)
              for b in bus}
    return {"partners": rows, "units": [{"id": b["id"], "code": b["code"], "net_profit": b["net_profit"], "billed_profit": billed[b["id"]],
                                         "wip": wip_bu.get(b["id"], 0)} for b in bus],
            "company_result": company_after_alloc, "reserve_pct": reserve, "share_checks": checks,
            "default_check": round(sum(pt["default_share"] or 0 for pt in partners), 4), "ratio_typed": typed,
            "ratio_check": round(sum(ratio.values()), 4), "appropriations": equity.appropriations(db),
            "legal_reserve_pct": float(db.setting("legal_reserve_pct", "0") or 0),
            "legal_reserve_cap": float(db.setting("legal_reserve_cap", "0.5") or 0)}


def cash_position(db):
    rows = account_totals(db)
    cash = sum(r["net"] for r in rows if r["subtype"] == "cash")
    span = db.one(f"SELECT MIN(date) AS a FROM journal_entries e WHERE {LIVE}")
    n = 1
    if span["a"]:
        a, b = _dt.date.fromisoformat(span["a"]), _dt.date.today()
        n = max(1, (b.year - a.year) * 12 + b.month - a.month + 1)
    opex = sum(r["net"] for r in rows if r["subtype"] in ("employee", "general_admin", "marketing", "utilities",
                                                           "other_operating"))
    owed = -sum(r["net"] for r in rows if r["subtype"] in ("payable", "accrued"))
    floor = opex / n * 3 + owed
    out = {"cash": to_major(cash), "monthly_overhead": to_major(opex / n), "months": n, "owed": to_major(owed),
           "floor": to_major(floor), "gap": to_major(max(floor - cash, 0)), "facility": None}
    # the funding partner's account is the company's bank (facility mode): what matters is the room left in his
    # facility once suppliers and staff are paid, not the company's own cash against a floor
    if (db.setting("funding_mode") or "capital") == "facility":
        from . import settlement
        f = settlement.facility(db)
        if f and f["limit"]:
            out["facility"] = {k: f[k] for k in ("partner", "limit", "fronted", "after_suppliers", "room_after", "used", "state", "status", "status_ar")}
            out["gap"] = round(max(0.0, -f["room_after"]), 2)
    # the other funding partners: money of the company they hold and the room left under their limits is money too
    from . import funders
    others = funders.others_money(db)
    if others["rows"]:
        out["other_funders"] = others
        if not out["facility"]:
            out["gap"] = round(max(0.0, out["floor"] - out["cash"] - others["held"] - others["room"]), 2)
    return out


def dashboard(db, date_from=None, date_to=None, bu_id=None):
    rows = {r["subtype"]: 0 for r in []}
    for r in account_totals(db, date_to=date_to, bu_id=bu_id):
        rows[r["subtype"]] = rows.get(r["subtype"], 0) + r["net"]
    pnl = income_statement(db, date_from, date_to, bu_id)
    start = (_dt.date.today().replace(day=1) - _dt.timedelta(days=330)).replace(day=1).isoformat()
    w, p = _where(date_from=start, bu_id=bu_id)
    series = db.q(f"""SELECT substr(e.date,1,7) AS month,
            SUM(CASE WHEN a.type='Revenue' THEN l.credit-l.debit ELSE 0 END) AS revenue,
            SUM(CASE WHEN a.type IN ('COGS','Expense') THEN l.debit-l.credit ELSE 0 END) AS costs
            FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
            WHERE {w} GROUP BY month ORDER BY month""", p)
    for s in series:
        s["revenue"], s["costs"] = to_major(s["revenue"]), to_major(s["costs"])
    return {"cash": to_major(rows.get("cash", 0)), "receivable": to_major(rows.get("receivable", 0)),
            "payable": to_major(-rows.get("payable", 0)), "accrued": to_major(-rows.get("accrued", 0)),
            "advances": to_major(-rows.get("deferred_revenue", 0)), "inventory": to_major(rows.get("inventory", 0)),
            "revenue": pnl["net_sales"], "ebitda": pnl["ebitda"], "net_profit": pnl["net_profit"],
            "margins": pnl["margins"], "series": series, "units": bu_summaries(db, date_from, date_to),
            "cash_position": cash_position(db), "audit": audit(db)}


@memo
def audit(db):
    tb = trial_balance(db)
    bs = balance_sheet(db)
    stock_val = round(sum(r["value"] for r in stock_report(db)), 2)
    inv_accounts = {w["account"] or db.acct("inventory") for w in db.q("SELECT account FROM warehouses")}
    inv = to_major(sum(r["net"] for r in account_totals(db) if r["code"] in inv_accounts))
    bad = db.q("SELECT e.id FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id "
               "GROUP BY e.id HAVING SUM(l.debit)<>SUM(l.credit)")
    shares = db.one("SELECT COALESCE(SUM(overhead_pct),0) AS s FROM business_units")["s"]
    cf = cash_flow(db)
    return [
        {"check": "Trial balance: debits = credits", "check_ar": "ميزان المراجعة: المدين = الدائن", "a": tb["totals"]["debit"], "b": tb["totals"]["credit"],
         "ok": tb["balanced"]},
        {"check": "Balance sheet: assets = liabilities + equity", "check_ar": "الميزانية: الأصول = الالتزامات + حقوق الملكية", "a": bs["total_assets"],
         "b": bs["total_liabilities_equity"], "ok": bs["balanced"]},
        {"check": "Cash flow reconciles to change in cash", "check_ar": "التدفقات النقدية تطابق التغير في النقدية", "a": cf["change_in_cash"],
         "b": round(cf["ending_cash"] - cf["beginning_cash"], 2), "ok": cf["reconciles"]},
        {"check": "Stock register = inventory accounts", "check_ar": "سجل المخزون = حسابات المخزون", "a": stock_val, "b": inv, "ok": abs(stock_val - inv) < 0.05},
        {"check": "Every journal entry balances", "check_ar": "كل قيد متوازن", "a": len(bad), "b": 0, "ok": not bad},
        {"check": "Shared-overhead % across business units ≤ 100%", "check_ar": "نسب المصروفات المشتركة على الوحدات ≤ 100%", "a": round(shares * 100, 2), "b": 100,
         "ok": shares <= 1.0001},
    ]


def _fixed_asset_tie(db):
    """(book value in the register, the same accounts in the books) — or None with an empty register. The rental fleet's
    own accounts are left out (it has its own register); an asset whose purchase was never booked shows up here."""
    from . import assets
    regs = db.q("SELECT * FROM fixed_assets")
    if not regs:
        return None
    pol = assets.policy(db)
    book = 0.0
    for a in regs:
        st = assets.status(db, a, pol=pol)
        book += 0 if st["disposed"] else st["book_value"]          # a sold or scrapped asset has left the books
    book = round(book, 2)
    codes = {a["account"] for a in regs} | {a["accum_account"] or db.acct("accum_depreciation") for a in regs}
    for role in ("rental_fleet", "rental_accum"):
        r = db.one("SELECT code FROM accounts WHERE role=?", (role,))
        if r:
            codes.discard(r["code"])
    codes.discard(None)
    net = to_major(sum(r["net"] for r in account_totals(db) if r["code"] in codes))
    return book, net



def review_flags(db, date_from=None, date_to=None, partners=None):
    """Reasonableness flags: not errors in the books, but figures someone should look at. Each links to its record.
    partners: the Partner Pack's partner rows when the caller already has them (their share of CLEAN profit)."""
    from urllib.parse import quote
    out = []

    def add(en, ar, detail, href):
        out.append({"flag": en, "flag_ar": ar, "detail": detail, "href": href})

    for b in bu_summaries(db, date_from, date_to):
        if b["revenue"] and b["gross_profit"] < 0:
            add(f"{b['code']}: costs are higher than income by {-b['gross_profit']:,.2f}",
                f"{b['code']}: التكاليف أعلى من الإيراد بمقدار {-b['gross_profit']:,.2f}", b["code"], f"#/bu/{b['id']}")
        # only where the expected billing is a real expectation (cost-plus or a fixed price), not "what was collected"
        if b["basis"] in ("Cost-Plus %", "Fixed Price") and b["expected_billing"] > 0 and b["revenue"] > b["expected_billing"] + 0.005:
            add(f"{b['code']}: invoiced {b['revenue']:,.2f}, more than the expected {b['expected_billing']:,.2f}",
                f"{b['code']}: المفوتر {b['revenue']:,.2f} أكثر من المتوقع {b['expected_billing']:,.2f}", b["code"], f"#/bu/{b['id']}")
    if partners is None:          # drawn ahead = drawn more than the share of CLEAN profit (after costs still to come)
        from . import partner_pack
        try:
            partners = partner_pack.compute(db, date_from, date_to)["partners"]
        except (LookupError, ValidationError):
            partners = []
    for p in partners:
        if p["undistributed"] < -0.005:
            add(f"{p['name']} has taken {-p['undistributed']:,.2f} more than their share of the profit",
                f"{p['name']} سحب {-p['undistributed']:,.2f} أكثر من نصيبه في الربح", p["name"], "#/statement?party=" + quote(p["name"]))
    # cash / bank that went below zero on any day: money was paid out before it came in (someone paid from their pocket,
    # or a receipt is missing) — the year-end check only sees the closing balance
    for acct in db.q("SELECT code, name, name_ar FROM accounts WHERE subtype='cash' ORDER BY code"):
        days = db.q(f"""SELECT e.date, SUM(l.debit-l.credit) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                        WHERE {LIVE} AND l.account=? {"AND e.date<=?" if date_to else ""} GROUP BY e.date ORDER BY e.date""",
                    (acct["code"],) + ((date_to,) if date_to else ()))
        run, low, low_on, first = 0, 0, "", ""
        for d in days:
            run += d["n"]
            if run < low:
                low, low_on = run, d["date"]
            if run < 0 and not first:
                first = d["date"]
        if low < 0:
            add(f"{acct['name']} went below zero from {first} (lowest {to_major(low):,.2f} on {low_on}): money was paid out before it "
                f"came in — record where it came from", f"رصيد {acct['name_ar'] or acct['name']} أصبح بالسالب من {first} (أدنى رصيد "
                f"{to_major(low):,.2f} في {low_on}): صُرف مبلغ قبل وروده — سجّل مصدره", acct["code"],
                "#/ledger?account=" + quote(acct["code"]))
    fa = _fixed_asset_tie(db) if not date_to else None     # the register as it stands, against the same accounts
    if fa and abs(fa[0] - fa[1]) >= 0.05:
        add(f"The fixed asset register's book value ({fa[0]:,.2f}) differs from the asset accounts net of depreciation ({fa[1]:,.2f}): "
            "an asset's purchase may not be booked, or was booked to another account",
            f"القيمة الدفترية في سجل الأصول الثابتة ({fa[0]:,.2f}) تختلف عن حسابات الأصول بعد مجمع الإهلاك ({fa[1]:,.2f}): ربما لم يُسجل "
            "شراء أصل، أو سُجل على حساب آخر", "", "#/assets")
    from . import settlement
    f = settlement.facility(db, date_to)
    if f and f["state"] in ("approaching", "near", "over"):     # the full picture (fronted, peak, weeks) is on the linked screen
        lim, left = f"{f['limit']:,.0f}", f"{abs(f['room_after']):,.0f}"
        en, ar = ((f"over its {lim} limit by {left}", f"تجاوز حده البالغ {lim} بمقدار {left}") if f["state"] == "over" else
                  (f"near its {lim} limit, {left} left", f"قريب جداً من حده البالغ {lim}، متبقٍ {left}") if f["state"] == "near" else
                  (f"getting close to its {lim} limit, {left} left", f"يقترب من حده البالغ {lim}، متبقٍ {left}"))
        add(f"{f['partner']}'s funding is {en}", f"تمويل {f['partner']} {ar}", f["partner"], "#/partner-exit")
    from . import funders
    for o in funders.limit_states(db, date_to):          # each other funding partner against his own limit
        if o["state"] in ("approaching", "near", "over"):
            lim, left = f"{o['limit']:,.0f}", f"{abs(o['left']):,.0f}"
            en, ar = ((f"over its {lim} limit by {left}", f"تجاوز حده البالغ {lim} بمقدار {left}") if o["state"] == "over" else
                      (f"near its {lim} limit, {left} left", f"قريب جداً من حده البالغ {lim}، متبقٍ {left}") if o["state"] == "near" else
                      (f"getting close to its {lim} limit, {left} left", f"يقترب من حده البالغ {lim}، متبقٍ {left}"))
            add(f"{o['name']}'s funding is {en}", f"تمويل {o['name']} {ar}", o["name"], "#/partner-exit")
    from . import ratecap
    n = len(ratecap.candidates(db))
    if n:
        add(f"{n} prices used in the books are not in the price list yet", f"{n} سعرًا مستخدمًا في الدفاتر غير موجود في قائمة الأسعار بعد", "", "#/prices?ratecap=1")
    return out


# ================================================================== tax module
PARTY_FIELDS = ("name", "type", "receiver_type", "tax_id", "national_id", "country", "governate", "region", "street",
                "building", "postal_code", "email", "phone", "default_vat", "default_wht", "notes",
                "category", "contact_person", "phone2", "whatsapp", "specialty", "vehicle", "bank_details", "currency")
# suggested directory categories; any other word typed by the user is kept as-is
CONTACT_CATEGORIES = ("supplier", "subcontractor", "driver", "transport", "technician", "consultant", "customer",
                      "employee", "other")
# accounting type implied by a category when the user did not pick one
_CATEGORY_TYPE = {"customer": "customer", "employee": "employee", "driver": "supplier", "supplier": "supplier",
                  "subcontractor": "supplier", "transport": "supplier", "technician": "supplier",
                  "consultant": "supplier"}


PARTY_NAME_COLUMNS = ("party", "customer", "supplier", "vendor", "client")


def rename_party_everywhere(db, old, new):
    """A contact's new name in every table that stores it by name (journal lines included), in one go."""
    for (t,) in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for c in db.conn.execute(f"PRAGMA table_info({t})").fetchall():
            if c[1] in PARTY_NAME_COLUMNS:
                db.conn.execute(f"UPDATE {t} SET {c[1]}=? WHERE {c[1]}=?", (new, old))


def save_party(db, d, party_id=None):
    v = {k: text(d.get(k), maxlen=2000 if k in ("notes", "bank_details") else 300) for k in PARTY_FIELDS}
    if not v["name"]:
        raise ValidationError("name is required")
    v["category"] = v["category"].strip().lower()[:40]
    from . import fx
    if "currency" not in d and party_id:              # forms that do not show it keep it as it was
        v["currency"] = (db.one("SELECT currency FROM parties WHERE id=?", (int(party_id),)) or {}).get("currency") or ""
    v["currency"] = fx.code(v["currency"])            # the currency they usually deal in (documents start in it)
    if v["currency"] == fx.base(db):
        v["currency"] = ""
    if v["type"] not in ("customer", "supplier", "both", "employee", "other"):
        v["type"] = _CATEGORY_TYPE.get(v["category"], "other" if v["category"] else "customer")
    v["receiver_type"] = v["receiver_type"] if v["receiver_type"] in ("B", "P", "F") else "B"
    v["country"] = (v["country"] or db.setting("country", "") or "EG").upper()[:2]     # the company's own country by default
    if v["tax_id"] and v["receiver_type"] == "B" and v["country"] == "EG" and not re.fullmatch(r"\d{9}", v["tax_id"]):
        raise ValidationError("An Egyptian tax registration number has 9 digits")
    if v["national_id"] and v["country"] == "EG" and not re.fullmatch(r"\d{14}", v["national_id"]):
        raise ValidationError("An Egyptian national ID has 14 digits")
    for k in ("default_vat", "default_wht"):
        if v[k]:
            tax_code(db, v[k], "vat" if k == "default_vat" else "wht")
    with db.tx():
        dup = db.one("SELECT id FROM parties WHERE name=?", (v["name"],))
        if party_id:
            if dup and dup["id"] != int(party_id):
                raise ValidationError("Another party already has this name")
            old = db.one("SELECT name FROM parties WHERE id=?", (int(party_id),))
            if not old:
                raise ValidationError("Party not found")
            db.conn.execute(f"UPDATE parties SET {','.join(k + '=?' for k in PARTY_FIELDS)} WHERE id=?",
                            tuple(v[k] for k in PARTY_FIELDS) + (int(party_id),))
            if old["name"] != v["name"]:  # everything that names the party follows it (its statements, contracts, orders…)
                rename_party_everywhere(db, old["name"], v["name"])
            return int(party_id)
        if dup:
            raise ValidationError("A party with this name already exists")
        return db.conn.execute(f"INSERT INTO parties({','.join(PARTY_FIELDS)}) VALUES ({','.join('?' * len(PARTY_FIELDS))})",
                               tuple(v[k] for k in PARTY_FIELDS)).lastrowid


def save_tax_code(db, d, old_code=None):
    code = text(d.get("code"), "code", required=True, maxlen=20).upper()
    kind = d.get("kind")
    if kind not in ("vat", "wht"):
        raise ValidationError("kind must be vat or wht")
    rate = num(d.get("rate"), "rate")
    rate = rate / 100 if rate > 1 else rate
    vals = (code, text(d.get("name"), "name", required=True), text(d.get("name_ar")), kind, rate,
            text(d.get("eta_type")) or ("T1" if kind == "vat" else "T4"), text(d.get("eta_subtype")),
            0 if d.get("active") in (0, "0", False, "No", "no") else 1)
    with db.tx():
        if old_code:
            db.conn.execute("UPDATE tax_codes SET code=?,name=?,name_ar=?,kind=?,rate=?,eta_type=?,eta_subtype=?,active=? "
                            "WHERE code=?", vals + (old_code,))
        else:
            if db.one("SELECT 1 FROM tax_codes WHERE code=?", (code,)):
                raise ValidationError("Code already exists")
            db.conn.execute("INSERT INTO tax_codes(code,name,name_ar,kind,rate,eta_type,eta_subtype,active) "
                            "VALUES (?,?,?,?,?,?,?,?)", vals)
    return code


def tax_summary(db, date_from=None, date_to=None):
    """VAT return figures and withholding-tax schedules for a period."""
    w, p = _where(date_from=date_from, date_to=date_to)
    roles = {r: db.acct(r) for r in ("vat_output", "vat_input", "wht_payable", "wht_receivable")}

    # a settlement (VAT offset against input VAT, tax paid to the authority) moves the balances but is not part of
    # the period's return: an entry whose every line is on a tax account or on cash / bank
    tax_accts = [c for c in roles.values() if c]
    marks = ",".join("?" * len(tax_accts)) or "''"
    settle = (f"EXISTS (SELECT 1 FROM journal_lines x WHERE x.entry_id=e.id AND x.account NOT IN ({marks}) "
              f"AND x.account NOT IN (SELECT code FROM accounts WHERE subtype='cash'))")

    def mv(code):
        r = db.one(f"SELECT COALESCE(SUM(l.debit),0) dr, COALESCE(SUM(l.credit),0) cr FROM journal_lines l "
                   f"JOIN journal_entries e ON e.id=l.entry_id WHERE {w} AND l.account=? AND {settle}", p + [code] + tax_accts)
        return r["dr"], r["cr"]

    o_dr, o_cr = mv(roles["vat_output"])
    i_dr, i_cr = mv(roles["vat_input"])
    output_vat, input_vat = o_cr - o_dr, i_dr - i_cr
    tax_ids = {r["name"]: r for r in db.q("SELECT name, tax_id, national_id FROM parties")}

    def by_party(code, sign):  # sign 1: credit-positive
        rows = db.q(f"""SELECT {PARTY} AS party, SUM(l.credit-l.debit) AS n, COUNT(DISTINCT e.id) AS docs,
                        SUM(CASE WHEN e.kind IN ('Journal Voucher','Payment Voucher') AND l.debit>0 THEN l.debit ELSE 0 END) AS settled
                        FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                        WHERE {w} AND l.account=? AND {settle} GROUP BY 1 ORDER BY 1""", p + [code] + tax_accts)
        return [{"party": r["party"], "tax_id": (tax_ids.get(r["party"]) or {}).get("tax_id", ""),
                 "amount": to_major(sign * r["n"]), "documents": r["docs"]} for r in rows if r["n"]]

    # taxable base per VAT code from invoices / purchases in the period (from voucher metadata)
    base = {}
    for r in db.q(f"SELECT e.id, e.kind, e.meta FROM journal_entries e WHERE {w} AND e.meta LIKE '%vat_code%'", p):
        m = json.loads(r["meta"] or "{}")
        if not m.get("vat_code"):
            continue
        e = get_entry(db, r["id"])
        side = "sales" if r["kind"] in ("Sales Invoice", "Receipt Voucher", "Credit Note", "Debit Note") else "purchases"
        vat_line = sum(ln["credit"] - ln["debit"] if side == "sales" else ln["debit"] - ln["credit"]
                       for ln in e["lines"] if ln["account"] == roles["vat_output" if side == "sales" else "vat_input"])
        net = sum(ln["credit"] - ln["debit"] for ln in e["lines"] if side == "sales" and db.one(   # a credit note: negative
            "SELECT type FROM accounts WHERE code=?", (ln["account"],))["type"] == "Revenue") if side == "sales" else \
            sum(ln["debit"] for ln in e["lines"] if ln["debit"] and ln["account"] != roles["vat_input"])
        k = (side, m["vat_code"])
        b = base.setdefault(k, {"side": side, "code": m["vat_code"], "net": 0.0, "vat": 0.0, "documents": 0})
        b["net"] = round(b["net"] + net, 2)
        b["vat"] = round(b["vat"] + vat_line, 2)
        b["documents"] += 1
    return {"date_from": date_from, "date_to": date_to, "accounts": roles,
            "output_vat": to_major(output_vat), "input_vat": to_major(input_vat),
            "net_vat": to_major(output_vat - input_vat),
            "vat_by_code": sorted(base.values(), key=lambda b: (b["side"], b["code"])),
            "wht_withheld_from_suppliers": by_party(roles["wht_payable"], 1),
            "wht_deducted_by_clients": by_party(roles["wht_receivable"], -1),
            "wht_payable_balance": party_total(db, roles["wht_payable"], date_to, 1),
            "wht_receivable_balance": party_total(db, roles["wht_receivable"], date_to, -1)}


def party_total(db, code, date_to=None, sign=1):
    w, p = _where(date_to=date_to)
    r = db.one(f"SELECT COALESCE(SUM(l.credit-l.debit),0) n FROM journal_lines l JOIN journal_entries e "
               f"ON e.id=l.entry_id WHERE {w} AND l.account=?", p + [code])
    return to_major(sign * r["n"])
