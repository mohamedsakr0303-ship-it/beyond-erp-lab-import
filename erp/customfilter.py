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


# ------------------------------------------------------------------ lists other than the journal
# Each list's fields: (field, kind, row key(s), English, Arabic). The rules are applied to the list's own rows inside the
# module that builds it, before its totals are added up, so the screen, its totals and its export all agree.
def _F(f, kind, key, en, ar):
    return (f, kind, key, en, ar)


T_, M_, D_, B_ = "text", "money", "date", "bool"
LISTS = {
    "parties": [_F("party", T_, "party", "Name", "الاسم"), _F("account", T_, "account", "Account", "الحساب"),
                _F("owed", M_, "owed", "Owed", "المستحق"), _F("paid", M_, "paid", "Paid", "المدفوع"),
                _F("balance", M_, "balance", "Balance", "الرصيد"), _F("last_date", D_, "last_date", "Last movement", "آخر حركة")],
    "stock": [_F("name", T_, "name", "Item", "الصنف"), _F("sku", T_, "sku", "Code", "الكود"), _F("category", T_, "category", "Category", "التصنيف"),
              _F("warehouse", T_, "warehouse", "Warehouse", "المخزن"), _F("qty", M_, "qty", "Quantity", "الكمية"),
              _F("avg_cost", M_, "avg_cost", "Average cost", "متوسط التكلفة"), _F("value", M_, "value", "Value", "القيمة"),
              _F("last_date", D_, "last_date", "Last movement", "آخر حركة"), _F("low", B_, "low", "Below minimum", "أقل من الحد الأدنى")],
    "assets": [_F("code", T_, "code", "Code", "الكود"), _F("name", T_, "name", "Name", "الاسم"), _F("account", T_, "account", "Account", "الحساب"),
               _F("acquired", D_, "acquired", "Acquired", "تاريخ الاقتناء"), _F("cost", M_, "cost", "Cost", "التكلفة"),
               _F("accumulated", M_, "accumulated", "Accumulated depreciation", "مجمع الإهلاك"), _F("book_value", M_, "book_value", "Book value", "القيمة الدفترية"),
               _F("monthly", M_, "monthly", "Monthly depreciation", "الإهلاك الشهري"), _F("months_left", M_, "months_left", "Months left", "الأشهر المتبقية"),
               _F("location", T_, "location", "Location", "الموقع"), _F("serial", T_, "serial", "Serial", "الرقم التسلسلي"),
               _F("project", T_, "bu_code", "Business unit", "مركز التكلفة"), _F("disposed", B_, "disposed", "Disposed", "مستبعد"),
               _F("fully_depreciated", B_, "fully_depreciated", "Fully depreciated", "مهلك بالكامل")],
    "rentals": [_F("code", T_, "code", "Code", "الكود"), _F("client", T_, "client", "Client", "العميل"), _F("site", T_, "site", "Site", "الموقع"),
                _F("status", T_, "status", "Status", "الحالة"), _F("quote_date", D_, "quote_date", "Quote date", "تاريخ العرض"),
                _F("start_date", D_, "start_date", "Start", "البداية"), _F("monthly_on_site", M_, "monthly_on_site", "Monthly on site", "الشهري بالموقع"),
                _F("monthly_quoted", M_, "monthly_quoted", "Monthly quoted", "الشهري بالعرض"), _F("pieces_out", M_, "pieces_out", "Pieces out", "القطع بالموقع")],
    "purchase_orders": [_F("code", T_, "code", "Code", "الكود"), _F("date", D_, "date", "Date", "التاريخ"), _F("supplier", T_, "supplier", "Supplier", "المورد"),
                        _F("project", T_, "bu_code", "Business unit", "مركز التكلفة"), _F("status", T_, "status", "Status", "الحالة"),
                        _F("grand_total", M_, "grand_total", "Total", "الإجمالي"), _F("open_value", M_, "open_value", "Still to receive", "المتبقي للاستلام"),
                        _F("received_value", M_, "received_value", "Received", "المستلم"), _F("created_by", T_, "created_by", "Created by", "أنشأه"),
                        _F("approved_by", T_, "approved_by", "Approved by", "اعتمده"), _F("overdue", B_, "overdue", "Late", "متأخر")],
    "purchase_requests": [_F("code", T_, "code", "Code", "الكود"), _F("date", D_, "date", "Date", "التاريخ"), _F("requested_by", T_, "requested_by", "Requested by", "طلبه"),
                          _F("project", T_, "bu_code", "Business unit", "مركز التكلفة"), _F("warehouse", T_, "warehouse", "Warehouse", "المخزن"),
                          _F("needed_by", D_, "needed_by", "Needed by", "مطلوب بتاريخ"), _F("status", T_, "status", "Status", "الحالة"),
                          _F("estimate", M_, "estimate", "Estimate", "التقدير"), _F("quotes", M_, "quotes", "Quotes", "عروض الأسعار")],
    "sales": [_F("code", T_, "code", "Code", "الكود"), _F("date", D_, "date", "Date", "التاريخ"), _F("customer", T_, "customer", "Customer", "العميل"),
              _F("project", T_, "bu_code", "Business unit", "مركز التكلفة"), _F("status", T_, "status", "Status", "الحالة"),
              _F("salesperson", T_, "salesperson", "Salesperson", "مندوب المبيعات"), _F("total", M_, "total", "Total", "الإجمالي"),
              _F("paid", M_, "paid", "Paid", "المدفوع"), _F("balance", M_, "balance", "Balance", "الرصيد"),
              _F("overdue", M_, "overdue", "Overdue amount", "المتأخر"), _F("next_due", D_, "next_due", "Next due", "الاستحقاق التالي"),
              _F("invoiced", B_, "invoiced", "Invoiced", "مفوتر")],
    "products": [_F("code", T_, "code", "Code", "الكود"), _F("name", T_, ("name", "name_ar"), "Name", "الاسم"), _F("kind", T_, "kind", "Type", "النوع"),
                 _F("category", T_, "category", "Category", "التصنيف"), _F("status", T_, "status", "Status", "الحالة"),
                 _F("price", M_, "price", "Price", "السعر"), _F("cost", M_, "cost", "Cost", "التكلفة"), _F("project", T_, "bu_code", "Business unit", "مركز التكلفة")],
    "employees": [_F("code", T_, "code", "Code", "الكود"), _F("name", T_, ("name", "name_ar"), "Name", "الاسم"), _F("job_title", T_, "job_title", "Job title", "الوظيفة"),
                  _F("department", T_, "department", "Department", "القسم"), _F("emp_type", T_, "emp_type", "Pay type", "نوع الأجر"),
                  _F("status", T_, "status", "Status", "الحالة"), _F("hire_date", D_, "hire_date", "Hired", "تاريخ التعيين"),
                  _F("basic", M_, "basic", "Basic salary", "الراتب الأساسي"), _F("daily_rate", M_, "daily_rate", "Daily rate", "الأجر اليومي"),
                  _F("project", T_, "bu_code", "Business unit", "مركز التكلفة"), _F("phone", T_, "phone", "Phone", "الهاتف"), _F("insured", B_, "insured", "Insured", "مؤمن عليه")],
    "bus": [_F("code", T_, "code", "Code", "الكود"), _F("name", T_, "name", "Name", "الاسم"), _F("type", T_, "type", "Type", "النوع"),
            _F("location", T_, "location", "Location", "الموقع"), _F("status", T_, "status", "Status", "الحالة"), _F("customer", T_, "customer", "Customer", "العميل"),
            _F("grp", T_, "grp", "Group", "المجموعة"), _F("start_date", D_, "start_date", "Start", "البداية"), _F("revenue", M_, "revenue", "Revenue", "الإيراد"),
            _F("cost_to_date", M_, "cost_to_date", "Cost to date", "التكلفة حتى الآن"), _F("net_profit", M_, "net_profit", "Net profit", "صافي الربح"),
            _F("receivable", M_, "receivable", "Receivable", "المستحق"), _F("to_collect", M_, "to_collect", "To collect", "المتبقي للتحصيل")],
    "contracts": [_F("code", T_, "code", "Code", "الكود"), _F("kind", T_, "kind", "Type", "النوع"), _F("party", T_, "party", "Party", "الطرف"),
                  _F("title", T_, "title", "Title", "العنوان"), _F("project", T_, "bu_code", "Business unit", "مركز التكلفة"),
                  _F("status", T_, "status", "Status", "الحالة"), _F("start", D_, "start", "Start", "البداية"), _F("finish", D_, "finish", "Finish", "النهاية"),
                  _F("value", M_, "value", "Value", "القيمة"), _F("revised_value", M_, "revised_value", "Revised value", "القيمة المعدلة"),
                  _F("certified", M_, "certified", "Certified", "المعتمد"), _F("payable", M_, "payable", "Payable", "المستحق"),
                  _F("ending_soon", B_, "ending_soon", "Ending soon", "ينتهي قريباً"), _F("expired", B_, "expired", "Expired", "منتهي")],
    "offers": [_F("number", T_, "number", "Number", "الرقم"), _F("date", D_, "date", "Date", "التاريخ"), _F("client", T_, "client", "Client", "العميل"),
               _F("project", T_, "project", "Project", "المشروع"), _F("status", T_, "status", "Status", "الحالة"),
               _F("type_name", T_, "type_name", "Offer type", "نوع العرض"), _F("net", M_, "net", "Net", "الصافي")],
    "extracts": [_F("number", T_, "number", "Number", "الرقم"), _F("date", D_, "date", "Date", "التاريخ"), _F("customer", T_, "customer", "Customer", "العميل"),
                 _F("project", T_, "bu_code", "Business unit", "مركز التكلفة"), _F("status", T_, "status", "Status", "الحالة"),
                 _F("total", M_, "total", "Total", "الإجمالي"), _F("eta_status", T_, "eta_status", "E-invoice status", "حالة الفاتورة الإلكترونية")],
    "loans": [_F("code", T_, "code", "Code", "الكود"), _F("direction", T_, "direction", "Direction", "الاتجاه"), _F("party", T_, "party", "Party", "الطرف"),
              _F("purpose", T_, "purpose", "Purpose", "الغرض"), _F("status", T_, "status", "Status", "الحالة"), _F("start_date", D_, "start_date", "Start", "البداية"),
              _F("principal", M_, "principal", "Principal", "أصل القرض"), _F("outstanding", M_, "outstanding", "Outstanding", "المتبقي"),
              _F("overdue", M_, "overdue", "Overdue", "المتأخر"), _F("next_due", D_, "next_due", "Next due", "الاستحقاق التالي"),
              _F("maturity", D_, "maturity", "Maturity", "تاريخ الانتهاء"), _F("settled", B_, "settled", "Settled", "مسدد")],
    "custody": [_F("code", T_, "code", "Code", "الكود"), _F("holder", T_, "holder", "Holder", "صاحب العهدة"), _F("kind", T_, "kind", "Type", "النوع"),
                _F("purpose", T_, "purpose", "Purpose", "الغرض"), _F("status", T_, "status", "Status", "الحالة"), _F("issued_on", D_, "issued_on", "Issued", "تاريخ الصرف"),
                _F("due_date", D_, "due_date", "Due", "الاستحقاق"), _F("balance", M_, "balance", "Balance", "الرصيد"), _F("spent", M_, "spent", "Spent", "المنصرف"),
                _F("days_open", M_, "days_open", "Days open", "أيام مفتوحة"), _F("overdue", B_, "overdue", "Overdue", "متأخرة")],
    "installments": [_F("code", T_, "code", "Sale", "البيع"), _F("customer", T_, "customer", "Customer", "العميل"), _F("label", T_, "label", "Installment", "القسط"),
                     _F("due", D_, "due", "Due", "الاستحقاق"), _F("amount", M_, "amount", "Amount", "المبلغ"), _F("paid", M_, "paid", "Paid", "المدفوع"),
                     _F("left", M_, "left", "Left", "المتبقي"), _F("status", T_, "status", "Status", "الحالة"), _F("days_late", M_, "days_late", "Days late", "أيام التأخير")],
    "boqs": [_F("title", T_, "title", "Title", "العنوان"), _F("rev", T_, "rev", "Revision", "الإصدار"), _F("date", D_, "date", "Date", "التاريخ"),
             _F("client", T_, "client", "Client", "العميل"), _F("location", T_, "location", "Location", "الموقع"),
             _F("project", T_, "bu_code", "Business unit", "مركز التكلفة"), _F("total", M_, "total", "Total", "الإجمالي")],
    "equipment": [_F("code", T_, "code", "Code", "الكود"), _F("name", T_, "name", "Name", "الاسم"), _F("category", T_, "category", "Category", "التصنيف"),
                  _F("serial", T_, "serial", "Serial", "الرقم التسلسلي"), _F("status", T_, "status", "Status", "الحالة"),
                  _F("project", T_, "bu_code", "Business unit", "مركز التكلفة"), _F("hours", M_, "hours", "Hours", "الساعات"),
                  _F("charged", M_, "charged", "Charged", "المحمّل"), _F("maintenance_cost", M_, "maintenance_cost", "Maintenance cost", "تكلفة الصيانة"),
                  _F("fuel_cost", M_, "fuel_cost", "Fuel cost", "تكلفة الوقود")],
    "subscriptions": [_F("code", T_, "code", "Code", "الكود"), _F("customer", T_, "customer", "Customer", "العميل"), _F("title", T_, "title", "Title", "العنوان"),
                      _F("kind", T_, "kind", "Type", "النوع"), _F("status", T_, "status", "Status", "الحالة"), _F("start", D_, "start", "Start", "البداية"),
                      _F("next_bill", D_, "next_bill", "Next bill", "الفاتورة التالية"), _F("price", M_, "price", "Price", "السعر"),
                      _F("monthly", M_, "monthly", "Monthly", "الشهري"), _F("auto_renew", B_, "auto_renew", "Renews by itself", "تجديد تلقائي")],
    "crm_contacts": [_F("name", T_, "name", "Name", "الاسم"), _F("type", T_, "type", "Type", "النوع"), _F("phone", T_, "phone", "Phone", "الهاتف"),
                     _F("email", T_, "email", "Email", "البريد"), _F("last_contact", D_, "last_contact", "Last contact", "آخر تواصل"),
                     _F("next_followup", D_, "next_followup", "Next follow-up", "المتابعة التالية"), _F("open_deals", M_, "open_deals", "Open deals", "الصفقات المفتوحة"),
                     _F("open_value", M_, "open_value", "Open value", "قيمة الصفقات")],
}
# which list a screen's address or an export asks for
LIST_OF_PATH = {"/api/parties": "parties", "/api/stock": "stock", "/api/assets": "assets", "/api/rentals": "rentals",
                "/api/purchase-orders": "purchase_orders", "/api/purchase-requests": "purchase_requests", "/api/sales": "sales",
                "/api/products": "products", "/api/employees": "employees", "/api/bus": "bus", "/api/contracts": "contracts",
                "/api/offers": "offers", "/api/extracts": "extracts", "/api/loans": "loans", "/api/custody": "custody",
                "/api/installments": "installments", "/api/boqs": "boqs", "/api/equipment": "equipment",
                "/api/subscriptions": "subscriptions", "/api/crm/contacts": "crm_contacts"}
LIST_OF_REPORT = {"fixed_assets": "assets", "rentals": "rentals", "loans": "loans", "custody": "custody", "sales": "sales",
                  "products": "products", "installments": "installments", "subscriptions": "subscriptions",
                  "purchase_orders": "purchase_orders", "stock": "stock", "payables": "parties", "bu_pnl": "bus"}

import contextvars  # noqa: E402

_ACTIVE = contextvars.ContextVar("custom_filter", default=None)


def activate(list_key, raw, match):
    """This request asks for `list_key` with these rules (checked now: a wrong rule is refused before any work)."""
    if not raw or list_key not in LISTS:
        return None
    rules = parse(raw)
    defs = {f[0]: f for f in LISTS[list_key]}
    for r in rules:
        f, op = r.get("f"), r.get("op")
        if f not in defs:
            raise FilterError(f"Custom filter: unknown field {f!r}")
        if op not in OPS[defs[f][1]]:
            raise FilterError(f"Custom filter: {f} cannot use {op!r}")
        _value_ok(defs[f][1], op, r.get("v"), r.get("v2"), f)
    return _ACTIVE.set((list_key, rules, "any" if match == "any" else "all"))


def active():
    """Is a custom filter narrowing a list in this request? (results kept between calls are not used then)"""
    return _ACTIVE.get() is not None


def deactivate(token):
    if token is not None:
        _ACTIVE.reset(token)


def _value_ok(kind, op, v, v2, what):
    if op in NO_VALUE:
        return
    if kind == "text" and not str(v or "").strip():
        raise FilterError(f"Custom filter: {what} needs a value")
    if kind == "money":
        _money(v, what)
        if op == "between":
            _money(v2, what)
    if kind == "date":
        if op == "last_days":
            try:
                n = int(str(v).strip())
            except ValueError:
                raise FilterError(f"Custom filter: {what} needs a number of days") from None
            if not 0 < n <= 3660:
                raise FilterError(f"Custom filter: {what}: between 1 and 3660 days")
        else:
            _date(v, what)
            if op == "between":
                _date(v2, what)


def _value(row, key):
    if isinstance(key, tuple):
        return " ".join(str(row.get(k) or "") for k in key)
    return row.get(key)


def _row_ok(row, r, defs):
    f = defs[r["f"]]
    kind, x, op, v, v2 = f[1], _value(row, f[2]), r["op"], r.get("v"), r.get("v2")
    if kind == "bool":
        return bool(x) if op == "yes" else not x
    if kind == "money":
        n = float(x or 0)
        a = _money(v, f[0]) / 100
        if op == "between":
            b = _money(v2, f[0]) / 100
            return min(a, b) - 0.005 <= n <= max(a, b) + 0.005
        return {"gt": n > a + 0.005, "gte": n >= a - 0.005, "lt": n < a - 0.005, "lte": n <= a + 0.005,
                "eq": abs(n - a) < 0.005, "ne": abs(n - a) >= 0.005}[op]
    if kind == "date":
        s = str(x or "")[:10]
        today = _dt.date.fromisoformat(acc.today())
        if op == "this_month":
            return today.replace(day=1).isoformat() <= s <= today.isoformat()
        if op == "this_year":
            return today.replace(month=1, day=1).isoformat() <= s <= today.isoformat()
        if op == "last_days":
            return (today - _dt.timedelta(days=int(str(v).strip()) - 1)).isoformat() <= s <= today.isoformat()
        a = _date(v, f[0])
        if op == "between":
            b = _date(v2, f[0])
            return min(a, b) <= s <= max(a, b)
        return {"eq": s == a, "before": bool(s) and s < a, "after": s > a}[op]
    s, w = str(x or "").lower(), str(v or "").strip().lower()
    return {"contains": w in s, "not_contains": w not in s, "eq": s.strip() == w, "ne": s.strip() != w, "starts": s.startswith(w),
            "ends": s.endswith(w), "empty": not s.strip(), "not_empty": bool(s.strip())}[op]


def keep(rows, list_key):
    """The list's rows that meet the rules of this request (all of them when it has none, or asks for another list)."""
    act = _ACTIVE.get()
    if not act or act[0] != list_key:
        return rows
    _, rules, match = act
    defs = {f[0]: f for f in LISTS[list_key]}
    test = any if match == "any" else all
    return [row for row in rows if test(_row_ok(row, r, defs) for r in rules)]


def fields():
    """Every list's fields for the screens' rule builder: {list: [[field, English, Arabic, kind]]}."""
    return {k: [[f[0], f[3], f[4], f[1]] for f in v] for k, v in {**LISTS, **SQL_LISTS}.items()}


# lists the database pages through: the rules become part of the query (every record, then the page)
SQL_LISTS = {
    "contacts": [_F("name", T_, "name", "Name", "الاسم"), _F("type", T_, "type", "Type", "النوع"), _F("category", T_, "category", "Category", "التصنيف"),
                 _F("phone", T_, "(COALESCE(phone,'') || ' ' || COALESCE(phone2,'') || ' ' || COALESCE(whatsapp,''))", "Phone", "الهاتف"),
                 _F("email", T_, "email", "Email", "البريد"), _F("tax_id", T_, "tax_id", "Tax ID", "الرقم الضريبي"),
                 _F("contact_person", T_, "contact_person", "Contact person", "مسؤول التواصل"), _F("specialty", T_, "specialty", "Specialty", "التخصص"),
                 _F("governate", T_, "governate", "Governorate", "المحافظة"), _F("notes", T_, "notes", "Notes", "ملاحظات")],
}


def sql_where(db, list_key, raw, match="all"):
    """Rules on a table the database pages through → (" AND (...)", params)."""
    rules = parse(raw)
    if not rules:
        return "", []
    defs = {f[0]: f for f in SQL_LISTS[list_key]}
    parts, params = [], []
    for r in rules:
        f, op = r.get("f"), r.get("op")
        if f not in defs:
            raise FilterError(f"Custom filter: unknown field {f!r}")
        kind, expr = defs[f][1], defs[f][2]
        if op not in OPS[kind]:
            raise FilterError(f"Custom filter: {f} cannot use {op!r}")
        if kind == "bool":
            s, p = (expr if op == "yes" else f"NOT ({expr})"), []
        else:
            s, p = _cond(db, kind, expr, op, r.get("v"), r.get("v2"), f)
        parts.append(f"({s})")
        params += p
    return " AND (" + (" OR " if match == "any" else " AND ").join(parts) + ")", params
