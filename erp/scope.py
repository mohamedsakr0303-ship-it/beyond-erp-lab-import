"""Project-level access: a person who may only see some projects (business units).

A super administrator picks the projects in Users (stored with the person's permissions as "_bus": [codes]; empty =
every project). For such a person, on top of the permission grid:

* lists only hold rows of their projects — anything tied to another project, or to no project (company overhead),
  is left out of the reply (journal, projects, BOQs, invoices, contracts, purchase orders, tasks, materials …);
* one record of another project (an entry, a BOQ, a contract …) is refused;
* reports run for one of their projects (the only one is picked by itself); company-wide reports and money views —
  cash & bank, bank reconciliation, taxes, partners & equity, loans, payroll runs, forecasts, year-end, full exports —
  are refused;
* what they record must be on one of their projects (an expense with no project would be company overhead).

Super administrators are never limited. Checked on every request (also when a change from the shared workspace is
replayed, so every device keeps the same books).
"""
import re

from .accounting import ValidationError

# money and company-wide views a project-limited person never opens
COMPANY_WIDE = re.compile(r"^/api/(?:cash-accounts|assets-overview|bank(?:-statements(?:/.*)?)?|tax/summary|cash-forecast|forecast(?:/.*)?"
                          r"|year-end(?:/.*)?|partners|equity/.*|funding-schedule|loans(?:/.*)?|payroll/runs(?:/.*)?|export/(?:json|workbook|csv)|backup"
                          r"|depreciation-history|budgets)$")
# reports that run for one project
PROJECT_REPORTS = {"income_statement", "balance_sheet", "cash_flow", "trial_balance", "general_ledger", "bu_pnl", "materials_used",
                   "negotiations", "budget", "vouchers", "profit_categories"}
# new entries: must name one of the person's projects
POSTING = re.compile(r"^/api/(?:docs/[\w-]+|journal|entry/[\w-]+)$")
ONE_ENTRY = re.compile(r"^/api/(?:journal|docs/[\w-]+)/(\d+)(?:/.*)?$")
BU_KEYS = ("bu_code", "bu_id", "bu")
# one record (and its files / documents) by address → its table (each has bu_id)
RECORDS = re.compile(r"^/api/(boqs|extracts|offers|sales|rentals|purchase-orders|contracts|purchase-requests|custody|projects)/(\w[\w-]*)(?:/.*)?$")
TABLE = {"boqs": "boqs", "extracts": "extracts", "offers": "offers", "sales": "sales_orders", "rentals": "rentals",
         "purchase-orders": "purchase_orders", "contracts": "contracts", "purchase-requests": "purchase_requests", "custody": "custodies"}


# a row that links to one record ("href": "#/rental/3") belongs to that record's project
LINK = re.compile(r"^#/(bu|project|rental|sale|sales|offer|po|invoice|contract|custody)/(\d+)\b")
LINK_TABLE = {"rental": "rentals", "sale": "sales_orders", "sales": "sales_orders", "offer": "offers", "po": "purchase_orders",
              "invoice": "extracts", "contract": "contracts", "custody": "custodies"}


class Scope:
    def __init__(self, db, codes):
        self.db, self._links = db, {}
        rows = db.q("SELECT id, code FROM business_units")
        want = {str(c).strip().lower() for c in codes}
        self.codes = {r["code"] for r in rows if r["code"].lower() in want}
        self.ids = {r["id"] for r in rows if r["code"] in self.codes}
        self.by_id = {r["id"]: r["code"] for r in rows}
        self.by_code = {r["code"].lower(): r["id"] for r in rows}

    def ok(self, v):
        """Is this project (id or code) one of theirs? None / '' = no project = not theirs."""
        if v in (None, "", 0, "0"):
            return False
        if isinstance(v, int) or (isinstance(v, str) and v.isdigit() and int(v) in self.by_id and v.lower() not in self.by_code):
            return int(v) in self.ids
        return str(v).strip().lower() in {c.lower() for c in self.codes}

    def first(self):
        return min(self.ids) if self.ids else None

    def link_ok(self, href):
        """Does a link to one record stay inside their projects? Links to other kinds of pages are left alone."""
        m = LINK.match(href) if isinstance(href, str) else None
        if not m:
            return True
        kind, rid = m.group(1), int(m.group(2))
        if kind in ("bu", "project"):
            return rid in self.ids
        key = (kind, rid)
        if key not in self._links:
            r = self.db.one(f"SELECT bu_id FROM {LINK_TABLE[kind]} WHERE id=?", (rid,))
            self._links[key] = bool(r) and self.ok(r["bu_id"])
        return self._links[key]


def of(db, user):
    """The person's project limit, or None (every project)."""
    if not user or user.get("is_super"):
        return None
    codes = (user.get("perms") or {}).get("_bus") or []
    return Scope(db, codes) if codes else None


def _deny(msg):
    raise PermissionError(msg)


def _bus_in(body):
    """Every project a request body names (top level and its lines / splits / items)."""
    out = []
    if not isinstance(body, dict):
        return out
    for k in BU_KEYS:
        if body.get(k) not in (None, ""):
            out.append(body[k])
    for key in ("lines", "split", "items", "rows"):
        for ln in body.get(key) or []:
            if isinstance(ln, dict):
                out += [ln[k] for k in BU_KEYS if ln.get(k) not in (None, "")]
    return out


def guard(db, sc, method, path, q, body):
    """Check (and narrow) a request before it runs. q may be changed (a report's project)."""
    if COMPANY_WIDE.match(path):
        _deny("This covers the whole company — you can see your projects only")
    for k in ("bu_id", "bu_code", "bu"):
        v = q.get(k)
        if v not in (None, "") and not sc.ok(v):
            _deny("That project is not one of yours")
    m = re.match(r"^/api/reports/([\w-]+)$", path)
    if m:
        if m.group(1) not in PROJECT_REPORTS:
            _deny("This report covers the whole company — you can see your projects only")
        if not q.get("bu_id"):
            if len(sc.ids) == 1:
                q["bu_id"] = str(sc.first())
            else:
                raise ValidationError("Choose one of your permitted projects for this report.")
    # cost items: the report and the suggestions are per project; tagging only lines of their projects
    if path in ("/api/cost-items/report", "/api/cost-items/suggest") and not q.get("bu"):
        if len(sc.ids) == 1:
            q["bu"] = str(sc.first())
        else:
            raise ValidationError("Choose one of your permitted projects.")
    if path == "/api/cost-items/tag" and method == "POST":
        for x in (body or {}).get("lines") or []:
            r = db.one("SELECT e.bu_id FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id WHERE l.id=?", (int(x.get("line_id") or 0),))
            if r and not sc.ok(r["bu_id"]):
                _deny("This is not on one of your projects")
    # the dashboard, its cards and insights: figures of their project only
    if path in ("/api/dashboard", "/api/dashboard/view", "/api/insights") and not q.get("bu_id") and sc.ids:
        q["bu_id"] = str(sc.first())
    m = ONE_ENTRY.match(path)
    if m:
        e = db.one("SELECT bu_id FROM journal_entries WHERE id=?", (int(m.group(1)),))
        if e and not sc.ok(e["bu_id"]):
            _deny("This entry is not on one of your projects")
    m = RECORDS.match(path)
    if m:
        kind, key = m.groups()
        if kind == "projects":
            if not sc.ok(key) and not (key.isdigit() and sc.ok(int(key))):
                _deny("This project is not one of yours")
        elif key.isdigit():
            r = db.one(f"SELECT bu_id FROM {TABLE[kind]} WHERE id=?", (int(key),))
            if r and not sc.ok(r["bu_id"]):
                _deny("This is not on one of your projects")
    if method != "GET":
        named = _bus_in(body)
        for v in named:
            if not sc.ok(v):
                _deny("You can only record on your own projects")
        if method == "POST" and POSTING.match(path) and not named:
            raise ValidationError("Choose one of your permitted projects.")


def _keep(sc, row):
    if "href" in row and not sc.link_ok(row["href"]):
        return False
    for k in ("bu_code", "bu_id"):
        if k in row:
            return sc.ok(row[k])
    return True


def _walk(sc, x):
    if isinstance(x, list):
        return [_walk(sc, v) for v in x if not (isinstance(v, dict) and not _keep(sc, v))]
    if isinstance(x, dict):
        return {k: _walk(sc, v) for k, v in x.items()}
    return x


def narrow(sc, path, data):
    """Leave other projects (and company-wide rows) out of a reply; refuse one record of another project."""
    if not isinstance(data, (dict, list)):
        return data
    if isinstance(data, dict) and data.get("error"):
        return data
    if path in ("/api/bus", "/api/projects") and isinstance(data, (dict, list)):
        rows = data.get("rows") if isinstance(data, dict) else data
        if isinstance(rows, list):
            rows = [r for r in rows if not isinstance(r, dict) or sc.ok(r.get("code") or r.get("bu_code"))]
            data = {**data, "rows": rows} if isinstance(data, dict) else rows
        if isinstance(data, dict):
            data.pop("shared_overhead", None)
    if isinstance(data, dict):
        _company_figures(sc, path, data)
    if path == "/api/accounts" and isinstance(data, list):     # balances are company-wide
        data = [{**r, "balance": None} if isinstance(r, dict) else r for r in data]
    if isinstance(data, dict) and re.match(r"^/api/[\w-]+/[^/]+$", path):
        for k in ("bu_code", "bu_id"):
            if k in data and data[k] not in (None, "") and not sc.ok(data[k]):
                _deny("This is not on one of your projects")
    return _walk(sc, data)


def _no_totals(checks):
    """Books checks (trial balance, balance sheet …): whether they pass, without the company's totals."""
    for c in checks or []:
        if isinstance(c, dict):
            for k in ("a", "b", "diff"):
                if k in c:
                    c[k] = None


def _company_figures(sc, path, data):
    """Parts of a reply that cover the whole company: the other projects' rows and the company's own totals."""
    if path == "/api/dashboard":
        if isinstance(data.get("units"), list):        # one row per project, with its figures
            data["units"] = [u for u in data["units"] if isinstance(u, dict) and sc.ok(u.get("code"))]
        rn = data.get("rentals")
        if isinstance(rn, dict) and isinstance(rn.get("rows"), list):
            rn["rows"] = [r for r in rn["rows"] if isinstance(r, dict) and sc.link_ok(f"#/rental/{r.get('id')}")]
            for k in list(rn):
                if k != "rows" and isinstance(rn[k], (int, float)):
                    rn[k] = None                       # totals across every rental
        data["cash_position"] = None                   # the company's cash and how long it lasts
        _no_totals(data.get("audit"))
    if path == "/api/model-audit":
        _no_totals(data.get("checks"))
    if path == "/api/alloc-rules":
        # overhead shared out to every project: each rule shows only their projects' share
        pv = data.get("preview")
        if isinstance(pv, dict):
            for k in ("pool", "company"):
                pv[k] = None
            for r in pv.get("rules") or []:
                if isinstance(r, dict) and isinstance(r.get("split"), dict):
                    r["split"] = {c: v for c, v in r["split"].items() if sc.ok(c)}
                    r["pooled"] = None
                    r["allocated"] = round(sum(v for v in r["split"].values() if isinstance(v, (int, float))), 2)
    if path == "/api/alerts" or path == "/api/dashboard/view":
        pass                                           # rows that link to another project's record are left out (_keep)


def extracts(sc, db, ids):
    """Invoices (by id) that are on one of their projects."""
    if sc is None or not ids:
        return list(ids)
    keep = {r["id"] for r in db.q(f"SELECT id, bu_id FROM extracts WHERE id IN ({','.join('?' * len(ids))})", tuple(ids)) if sc.ok(r["bu_id"])}
    return [i for i in ids if i in keep]


def clean(db, codes):
    """A project list as stored: known codes only."""
    if not isinstance(codes, (list, tuple)):
        return []
    have = {r["code"].lower(): r["code"] for r in db.q("SELECT code FROM business_units")}
    out = []
    for c in codes:
        c = have.get(str(c).strip().lower())
        if c and c not in out:
            out.append(c)
    return out
