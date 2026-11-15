"""Custom filters: conditions a person builds themselves ("amount over 50,000 AND party contains Ahmed AND account in
group 122"), applied by the server so they cover every record, not just the page on screen — and so exports, saved
views and links carry them too.

A filter is a list of rules [{"f": field, "op": operator, "v": value, "v2": second value}] and a match ("all" or "any").
Every field and operator is checked against the lists below; anything else is refused with a clear message, never
silently ignored (a filter that is quietly dropped would show the wrong figures).
"""
import datetime as _dt
import json

from . import accounting as acc
from .db import to_minor

MAX_RULES = 20

# operators per kind of value
OPS = {
    "text": ("contains", "not_contains", "eq", "ne", "starts", "ends", "empty", "not_empty"),
    "money": ("eq", "ne", "gt", "gte", "lt", "lte", "between"),
    "date": ("eq", "before", "after", "between", "last_days", "this_month", "this_year"),
    "choice": ("eq", "ne"),
    "bool": ("yes", "no"),
    "account": ("eq", "ne", "starts", "in_group", "not_in_group"),
}
NO_VALUE = {"empty", "not_empty", "this_month", "this_year", "yes", "no"}

# entry fields: (kind, SQL on the entry e, or ("line", SQL on a line l) when any line of the entry may match)
ENTRY_FIELDS = {
    "date": ("date", "e.date"),
    "created": ("date", "substr(e.created_at,1,10)"),
    "amount": ("money", "(SELECT COALESCE(SUM(debit),0) FROM journal_lines WHERE entry_id=e.id)"),
    "line_amount": ("money", ("line", "(l.debit + l.credit)")),
    "memo": ("text", "e.memo"),
    "ref": ("text", "e.ref"),
    "kind": ("choice", "e.kind"),
    "status": ("choice", "e.status"),
    "party": ("text", ("party", None)),
    "account": ("account", ("line", "l.account")),
    "account_name": ("text", ("line", "(SELECT COALESCE(a.name,'') || ' ' || COALESCE(a.name_ar,'') FROM accounts a WHERE a.code=l.account)")),
    "line_memo": ("text", ("line", "l.memo")),
    "cost_item": ("text", ("line", "l.cost_item")),
    "project": ("text", "COALESCE((SELECT b.code || ' ' || b.name FROM business_units b WHERE b.id=e.bu_id), '')"),
    "project_group": ("text", "COALESCE((SELECT b.grp FROM business_units b WHERE b.id=e.bu_id), '')"),
    "created_by": ("text", "COALESCE(NULLIF(json_extract(e.meta,'$.entered_by'),''), e.created_by, '')"),
    "approved_by": ("text", "COALESCE(json_extract(e.meta,'$.approved_by'), '')"),
    "tag": ("text", ("tag", None)),
    "attachment": ("bool", "EXISTS (SELECT 1 FROM attachments t WHERE t.owner='entry' AND t.owner_id=e.id)"),
}


class FilterError(acc.ValidationError):
    pass


def _like(s):
    return "%" + str(s).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _date(v, what):
    try:
        return _dt.date.fromisoformat(str(v).strip()[:10]).isoformat()
    except ValueError:
        raise FilterError(f"Custom filter: {what} needs a date (YYYY-MM-DD)") from None


def _money(v, what):
    try:
        return to_minor(acc.num(str(v).replace(",", "")))
    except (ValueError, TypeError, acc.ValidationError):
        raise FilterError(f"Custom filter: {what} needs a number") from None


def _cond(db, kind, expr, op, v, v2, what):
    """One comparison on a plain SQL expression → (sql, params)."""
    if kind == "text" or kind == "choice":
        s = "LOWER(COALESCE(" + expr + ",''))"
        val = str(v or "").strip().lower()
        if op not in NO_VALUE and not val:
            raise FilterError(f"Custom filter: {what} needs a value")
        return {"contains": (f"{s} LIKE ? ESCAPE '\\'", [_like(val)]), "not_contains": (f"{s} NOT LIKE ? ESCAPE '\\'", [_like(val)]),
                "eq": (f"{s}=?", [val]), "ne": (f"{s}<>?", [val]),
                "starts": (f"{s} LIKE ? ESCAPE '\\'", [_like(val)[1:]]), "ends": (f"{s} LIKE ? ESCAPE '\\'", [_like(val)[:-1]]),
                "empty": (f"{s}=''", []), "not_empty": (f"{s}<>''", [])}[op]
    if kind == "money":
        a = _money(v, what)
        if op == "between":
            b = _money(v2, what)
            return f"{expr} BETWEEN ? AND ?", [min(a, b), max(a, b)]
        return f"{expr} {({'eq': '=', 'ne': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='})[op]} ?", [a]
    if kind == "date":
        today = _dt.date.fromisoformat(acc.today())
        if op == "this_month":
            return f"{expr} BETWEEN ? AND ?", [today.replace(day=1).isoformat(), today.isoformat()]
        if op == "this_year":
            return f"{expr} BETWEEN ? AND ?", [today.replace(month=1, day=1).isoformat(), today.isoformat()]
        if op == "last_days":
            try:
                n = int(str(v).strip())
            except ValueError:
                raise FilterError(f"Custom filter: {what} needs a number of days") from None
            if not 0 < n <= 3660:
                raise FilterError(f"Custom filter: {what}: between 1 and 3660 days")
            return f"{expr} BETWEEN ? AND ?", [(today - _dt.timedelta(days=n - 1)).isoformat(), today.isoformat()]
        a = _date(v, what)
        if op == "between":
            b = _date(v2, what)
            return f"{expr} BETWEEN ? AND ?", [min(a, b), max(a, b)]
        return f"{expr} {({'eq': '=', 'before': '<', 'after': '>'})[op]} ?", [a]
    if kind == "account":
        code = str(v or "").strip()
        if not code:
            raise FilterError(f"Custom filter: {what} needs an account or group code")
        if op in ("in_group", "not_in_group"):
            if not db.one("SELECT 1 FROM account_groups WHERE code=?", (code,)):
                raise FilterError(f"Custom filter: there is no account group {code}")
            codes = acc.group_accounts(db, code) or [""]
            return f"{expr} IN ({','.join('?' * len(codes))})", list(codes)
        if op == "starts":
            return f"{expr} LIKE ? ESCAPE '\\'", [_like(code)[1:]]
        return f"{expr} {'=' if op == 'eq' else '<>'} ?", [code]
    raise FilterError(f"Custom filter: {what}: unknown kind")


NEGATIVE = {"ne", "not_contains", "not_in_group", "empty"}


def _entry_rule(db, r):
    f, op = r.get("f"), r.get("op")
    if f not in ENTRY_FIELDS:
        raise FilterError(f"Custom filter: unknown field {f!r}")
    kind, where = ENTRY_FIELDS[f]
    if op not in OPS[kind]:
        raise FilterError(f"Custom filter: {f} cannot use {op!r}")
    v, v2 = r.get("v"), r.get("v2")
    if kind == "bool":
        return (where if op == "yes" else f"NOT {where}"), []
    if isinstance(where, str):
        return _cond(db, kind, where, op, v, v2, f)
    # a value that may sit on any line (or several places): "is not / does not contain" means no line matches
    pos = {"ne": "eq", "not_contains": "contains", "not_in_group": "in_group", "empty": "not_empty"}.get(op, op)
    sub, where_kind = where[1], where[0]
    if where_kind == "line":
        sql, p = _cond(db, kind, sub, pos, v, v2, f)
        exists = f"EXISTS (SELECT 1 FROM journal_lines l WHERE l.entry_id=e.id AND {sql})"
    elif where_kind == "party":
        s1, p1 = _cond(db, kind, "e.party", pos, v, v2, f)
        s2, p2 = _cond(db, kind, "l.party", pos, v, v2, f)
        exists, p = f"({s1} OR EXISTS (SELECT 1 FROM journal_lines l WHERE l.entry_id=e.id AND {s2}))", p1 + p2
        sql = None
    else:   # tag
        s1, p = _cond(db, kind, "t.tag", pos, v, v2, f)
        exists = f"EXISTS (SELECT 1 FROM entry_tags t WHERE t.entry_id=e.id AND {s1})"
    return (f"NOT {exists}" if op in NEGATIVE else exists), p


def parse(raw):
    """The rules as sent (a JSON list, or already a list) → a clean list; [] when none."""
    if raw in (None, "", "[]"):
        return []
    try:
        rules = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        raise FilterError("Custom filter: not readable") from None
    if not isinstance(rules, list) or not all(isinstance(r, dict) for r in rules):
        raise FilterError("Custom filter: not readable")
    if len(rules) > MAX_RULES:
        raise FilterError(f"Custom filter: at most {MAX_RULES} conditions")
    return rules


def entry_where(db, raw, match="all"):
    """Rules on journal entries → (" AND (...)", params) to add to a WHERE on journal_entries e; ("", []) when none."""
    rules = parse(raw)
    if not rules:
        return "", []
    parts, params = [], []
    for r in rules:
        s, p = _entry_rule(db, r)
        parts.append(f"({s})")
        params += p
    joiner = " OR " if match == "any" else " AND "
    return " AND (" + joiner.join(parts) + ")", params
