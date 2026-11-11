"""Cost items (بنود التكلفة): one catalogue of work items shared by every project, tagged on cost lines.

A contractor's chart often repeats the same trades under every project ("costs of project A — excavation",
"costs of project B — excavation" …), which multiplies the accounts and still says nothing about budget against
actual. Here a project is a business unit, the account says what kind of cost it is (materials, subcontractors,
labour …) and the cost item says which part of the work it was for (excavation → by loader → debris removal):

  * cost_items: the catalogue, as a tree by code ("01", "01.02", "01.02.03"), with Arabic and English names, a
    unit and the words that recognise it in an entry's description;
  * journal_lines.cost_item: the item a cost line belongs to (blank = not tagged);
  * cost_budgets: what each project expects to spend on each item.

The report gives, per project and item: budget, actual, the difference and what is left, rolled up the tree. Lines
without an item can be tagged from their descriptions (`suggest`), and the user confirms them (`tag_lines`).
"""
import re

from . import accounting as acc
from .accounting import ValidationError, num, text
from .db import to_major, to_minor

# A starting catalogue for building work, by trade (codes are the user's to change). Words are matched against
# descriptions to suggest a trade; they are deliberately common words, the company adds its own.
STANDARD = [
    ("01", "Excavation & backfill", "أعمال الحفر والإحلال", "m3", "حفر ردم احلال إحلال لودر مخلفات تسوية دك نقل_رمل excavation backfill"),
    ("02", "Plain concrete", "أعمال الخرسانة العادية", "m3", "خرسانة عادية فرشة نظافة plain concrete blinding"),
    ("03", "Reinforced concrete", "أعمال الخرسانة المسلحة", "m3", "خرسانة مسلحة حديد تسليح حداد شدة صب سقف اعمدة قواعد ميدات جاهزة اسمنت rebar formwork concrete"),
    ("04", "Masonry", "أعمال المباني", "m2", "مباني طوب بلوك بناء بنا البنا masonry block brick"),
    ("05", "Plastering", "أعمال البياض", "m2", "بياض لياسة محارة محاره مبيض plaster"),
    ("06", "Insulation", "العزل", "m2", "عزل رطوبة حراري insulation waterproofing"),
    ("07", "Painting", "أعمال الدهانات", "m2", "دهان دهانات نقاش نقاشة معجون بوية سيتوكس paint"),
    ("08", "Ceramics & tiling", "أعمال السيراميك", "m2", "سيراميك بورسلين بلاط مبلط موزايكو tiles ceramic porcelain"),
    ("09", "Marble & granite", "أعمال الرخام", "m2", "رخام جرانيت رخامجي marble granite"),
    ("10", "Roof tiles", "أعمال القرميد", "m2", "قرميد roof tile"),
    ("11", "Carpentry & joinery", "أعمال النجارة", "no", "نجارة نجار ابواب شبابيك خشب باب الوميتال carpentry doors"),
    ("12", "Electrical works", "أعمال الكهرباء", "point", "كهرباء كهربا كهربائي كابلات سلك لوحة مفاتيح electrical cable"),
    ("13", "Plumbing & drainage", "أعمال الصحي", "point", "صحي سباكة سباك مواسير صرف خلاطات تيفلون اطقم plumbing pipes"),
]


def _code(v):
    c = text(v, "item code", required=True, maxlen=40).strip(" .")
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._\-]*", c):
        raise ValidationError("An item code uses letters, digits, dots and dashes (e.g. 01.02)")
    return c


def parent_of(code):
    """The item above in the tree: "01.02.03" → "01.02"; a code without dots is a trade."""
    return code.rsplit(".", 1)[0] if "." in code else ""


def items(db, active_only=False):
    rows = db.q("SELECT * FROM cost_items" + (" WHERE active=1" if active_only else "") + " ORDER BY code")
    return [dict(r) for r in rows]


def get(db, code):
    r = db.one("SELECT * FROM cost_items WHERE code=?", (text(code),))
    if not r:
        raise ValidationError(f"Unknown cost item {code}")
    return dict(r)


def save(db, d, old_code=None):
    code = _code(d.get("code"))
    name = text(d.get("name"), maxlen=160) or text(d.get("name_ar"), maxlen=160)
    if not name:
        raise ValidationError("Give the cost item a name")
    vals = (code, name, text(d.get("name_ar"), maxlen=160), parent_of(code), text(d.get("unit"), maxlen=20),
            text(d.get("keywords"), maxlen=1000), 0 if str(d.get("active", 1)).lower() in ("0", "false", "no") else 1)
    p = parent_of(code)
    if p and not db.one("SELECT 1 FROM cost_items WHERE code=?", (p,)):
        raise ValidationError(f"Add {p} first: {code} sits under it")
    with db.tx():
        if old_code and old_code != code:
            if db.one("SELECT 1 FROM cost_items WHERE code=?", (code,)):
                raise ValidationError(f"Cost item {code} already exists")
            if db.one("SELECT 1 FROM journal_lines WHERE cost_item=? LIMIT 1", (old_code,)):
                db.x("UPDATE journal_lines SET cost_item=? WHERE cost_item=?", (code, old_code))
            db.x("UPDATE cost_budgets SET item=? WHERE item=?", (code, old_code))
            db.x("DELETE FROM cost_items WHERE code=?", (old_code,))
        db.x("INSERT INTO cost_items(code,name,name_ar,parent,unit,keywords,active) VALUES (?,?,?,?,?,?,?) "
             "ON CONFLICT(code) DO UPDATE SET name=excluded.name, name_ar=excluded.name_ar, parent=excluded.parent, "
             "unit=excluded.unit, keywords=excluded.keywords, active=excluded.active", vals)
    return code


def delete(db, code):
    code = text(code)
    if db.one("SELECT 1 FROM cost_items WHERE parent=?", (code,)):
        raise ValidationError("Delete or move the items under it first")
    if db.one("SELECT 1 FROM journal_lines WHERE cost_item=? LIMIT 1", (code,)):
        raise ValidationError("Costs are tagged with this item. Deactivate it instead, or move its costs first.")
    db.x("DELETE FROM cost_budgets WHERE item=?", (code,))
    db.x("DELETE FROM cost_items WHERE code=?", (code,))


def load_standard(db):
    """Add the standard trades (existing codes are left as they are)."""
    n = 0
    for code, en, ar, unit, words in STANDARD:
        if not db.one("SELECT 1 FROM cost_items WHERE code=?", (code,)):
            db.x("INSERT INTO cost_items(code,name,name_ar,parent,unit,keywords,active) VALUES (?,?,?,?,?,?,1)",
                 (code, en, ar, "", unit, words))
            n += 1
    return n


# ---------------------------------------------------------------- recognising an item from a description
def _norm(s):
    s = re.sub(r"[إأآ]", "ا", str(s or "")).replace("ة", "ه").replace("ى", "ي").lower()
    return re.sub(r"[^\w\s]", " ", s)


def _words(s):
    out = set()
    for w in _norm(s).split():
        out.add(w)
        if w.startswith("ال") and len(w) > 4:       # الحديد = حديد
            out.add(w[2:])
    return out


def classify(db, description, catalogue=None):
    """The best item for a description by its words (deepest item wins a tie), or "" when nothing matches."""
    ws = _words(description)
    if not ws:
        return ""
    best, score = "", 0
    for it in catalogue if catalogue is not None else items(db, active_only=True):
        keys = _words(it.get("keywords")) | _words(it.get("name_ar")) | _words(it.get("name"))
        keys = {k for k in keys if len(k) > 2}
        # whole words, or a word that starts with a key of four letters or more (سباك → سباكه, حديد → حديده)
        hit = sum(1 for w in ws if w in keys or any(len(k) >= 4 and w.startswith(k) for k in keys))
        if hit and (hit > score or (hit == score and it["code"].count(".") > best.count("."))):
            best, score = it["code"], hit
    return best


def _cost_line_sql():
    return ("FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account "
            f"WHERE {acc.LIVE} AND a.type IN ('COGS','Expense') AND l.debit>0")


def suggest(db, bu_id=None, limit=500):
    """Untagged cost lines with the item their description points to."""
    cat = items(db, active_only=True)
    w, p = [], []
    if bu_id:
        w.append("e.bu_id=?")
        p.append(int(bu_id))
    rows = db.q(f"SELECT l.id, e.date, COALESCE(NULLIF(l.memo,''), e.memo) AS memo, l.account, l.debit, e.bu_id {_cost_line_sql()} AND COALESCE(l.cost_item,'')='' "
                + "".join(" AND " + x for x in w) + " ORDER BY e.date, l.id LIMIT ?", p + [int(limit)])
    out = []
    for r in rows:
        c = classify(db, r["memo"], cat)
        out.append({"line_id": r["id"], "date": r["date"], "memo": r["memo"], "account": r["account"],
                    "amount": to_major(r["debit"]), "bu_id": r["bu_id"], "suggested": c})
    return out


def tag_lines(db, pairs):
    """Set the item of cost lines: [{"line_id": …, "item": "01.02"}] (blank item = untag)."""
    known = {r["code"] for r in db.q("SELECT code FROM cost_items")}
    n = 0
    with db.tx():
        for x in pairs or []:
            it = text(x.get("item"))
            if it and it not in known:
                raise ValidationError(f"Unknown cost item {it}")
            n += db.x("UPDATE journal_lines SET cost_item=? WHERE id=?", (it, int(x["line_id"]))).rowcount
    return n


# ---------------------------------------------------------------- budgets and the report
def save_budget(db, bu_id, item, amount, qty=None):
    bu = acc.bu_id_or_none(db, bu_id)
    if not bu:
        raise ValidationError("Choose the project")
    get(db, item)
    db.x("INSERT INTO cost_budgets(bu_id,item,amount,qty) VALUES (?,?,?,?) ON CONFLICT(bu_id,item) DO UPDATE SET "
         "amount=excluded.amount, qty=excluded.qty", (bu, text(item), to_minor(num(amount or 0, "budget")),
                                                       float(num(qty or 0, "quantity"))))


def report(db, bu_id=None, date_from=None, date_to=None, lang="en"):
    """Budget against actual per item, rolled up the tree, for one project or all."""
    ar = lang == "ar"
    w, p = [], []
    if bu_id:
        w.append("e.bu_id=?")
        p.append(acc.bu_id_or_none(db, bu_id))
    if date_from:
        w.append("e.date>=?")
        p.append(acc.clean_date(date_from))
    if date_to:
        w.append("e.date<=?")
        p.append(acc.clean_date(date_to))
    where = "".join(" AND " + x for x in w)
    actual = {r["it"]: r["n"] for r in db.q(f"SELECT COALESCE(l.cost_item,'') it, SUM(l.debit-l.credit) n {_cost_line_sql().replace(' AND l.debit>0', '')}"
                                            f"{where} GROUP BY 1", p)}
    bw, bp = ("WHERE bu_id=?", [p[0]]) if bu_id else ("", [])
    budget = {r["item"]: r["n"] for r in db.q(f"SELECT item, SUM(amount) n FROM cost_budgets {bw} GROUP BY item", bp)}
    cat = {c["code"]: c for c in items(db)}
    roll_a, roll_b = {}, {}
    for code, n in actual.items():
        k = code
        while k:
            roll_a[k] = roll_a.get(k, 0) + n
            k = parent_of(k) if k in cat else ""
    for code, n in budget.items():
        k = code
        while k:
            roll_b[k] = roll_b.get(k, 0) + n
            k = parent_of(k)
    rows = []
    for code in sorted(cat):
        a, b = roll_a.get(code, 0), roll_b.get(code, 0)
        if not (a or b):
            continue
        c = cat[code]
        rows.append({"code": code, "name": (c["name_ar"] or c["name"]) if ar else c["name"], "level": code.count("."),
                     "budget": to_major(b), "actual": to_major(a), "left": to_major(b - a) if b else None,
                     "used_pct": round(a / b * 100, 1) if b else None})
    untagged = actual.get("", 0)
    unknown = sum(n for k, n in actual.items() if k and k not in cat)
    total_a = sum(actual.values())
    return {"rows": rows, "untagged": to_major(untagged), "unknown": to_major(unknown), "total": to_major(total_a),
            "budget": to_major(sum(budget.values())),
            "tagged_pct": round((total_a - untagged) / total_a * 100, 1) if total_a else None}


# ---------------------------------------------------------------- backup / import-export
def export_rows(db):
    return {"cost_items": items(db),
            "cost_budgets": [dict(r) | {"bu_code": r["bu_code"]} for r in db.q(
                "SELECT c.*, b.code AS bu_code FROM cost_budgets c JOIN business_units b ON b.id=c.bu_id ORDER BY b.code, c.item")]}


def import_rows(db, ex, counts=None):
    n = 0
    for r in sorted(ex.get("cost_items") or [], key=lambda r: str(r.get("code"))):
        save(db, r)
        n += 1
    for r in ex.get("cost_budgets") or []:
        save_budget(db, r.get("bu_code") or r.get("bu_id"), r["item"], to_major(r.get("amount") or 0), r.get("qty"))   # backups hold minor units
    if counts is not None and n:
        counts["Cost Items"] = n
    return n
