"""Insights: what moved the figures, where the month is heading, how each measure stands against its target, the deal
funnel and how much of what the business has is in use. Read from the books only (nothing is stored).

Every measure comes from the posted journal, so the figures match the reports: revenue is net sales (as in the
income statement: operating revenue less discounts, other income apart), costs what COGS and Expense accounts took,
collections the cash that came in from customers (receipts and cleared cheques, less bounced ones: a cheque received
is collected when it clears). A measure has a value (money) and a volume (how many
documents made it), so a change between two periods splits exactly into

    volume effect = (documents now − documents before) × value per document before
    value effect  = documents now × (value per document now − before)

which add up to the change (a group that is new has only a volume effect; one that stopped, only a volume effect).
"""
import calendar
import datetime as _dt
import json
import statistics

from . import accounting as acc
from .db import to_major

LIVE = acc.LIVE

# measure: (en, ar, account filter SQL, sign, lower is better, dimensions it can be split by, permission (any of))
MEASURES = {
    "revenue": ("Revenue", "الإيرادات", "a.type='Revenue' AND a.subtype<>'other_income'", "l.credit-l.debit", False, ("party", "unit", "source", "account"),
                ("reports", "sales")),
    "costs": ("Costs", "التكاليف", "a.type IN ('COGS','Expense')", "l.debit-l.credit", True, ("party", "unit", "account", "source"),
              ("reports", "purchasing")),
    "collections": ("Collected from customers", "المحصّل من العملاء",
                    "a.subtype='cash' AND e.kind IN ('Receipt Voucher','Cheque Cleared','Cheque Bounced')", "l.debit-l.credit", False,
                    ("party", "unit"), ("reports", "sales")),
    "payroll": ("Payroll cost", "تكلفة الرواتب", "a.type IN ('COGS','Expense') AND e.kind='Payroll Accrual'", "l.debit-l.credit", True,
                ("unit", "account"), ("reports", "payroll")),
}
DIMS = {"party": ("Customer / supplier", "العميل / المورد"), "unit": ("Business unit", "وحدة الأعمال"),
        "source": ("Where it comes from", "المصدر"), "account": ("Account", "الحساب")}
DIM_SQL = {"party": "COALESCE(NULLIF(l.party,''), NULLIF(e.party,''), '—')",
           "unit": "COALESCE((SELECT b.code || ' ' || b.name FROM business_units b WHERE b.id=e.bu_id), '—')",
           "source": "e.kind", "account": "a.code || ' ' || a.name"}

# what each part of the app looks at: the measures, the split that suits it, and the extra views
MODULES = {
    "business": {"en": "Whole business", "ar": "الشركة كلها", "perm": ("reports",), "measures": ["revenue", "costs", "collections", "payroll"],
                 "profit": True, "split": "unit", "extras": []},
    "sales": {"en": "Sales & collections", "ar": "المبيعات والتحصيل", "perm": ("sales", "reports"), "measures": ["revenue", "collections"],
              "split": "party", "extras": []},
    "purchasing": {"en": "Purchasing & costs", "ar": "المشتريات والتكاليف", "perm": ("purchasing", "reports"), "measures": ["costs"],
                   "split": "party", "extras": []},
    "crm": {"en": "Customers & deals", "ar": "العملاء والصفقات", "perm": ("crm",), "measures": [], "split": None, "extras": ["funnel"]},
    "units": {"en": "Business units & projects", "ar": "وحدات الأعمال والمشروعات", "perm": ("business_units", "projects", "reports"),
              "measures": ["revenue", "costs"], "profit": True, "split": "unit", "extras": []},
    "rentals": {"en": "Equipment rental", "ar": "تأجير المعدات", "perm": ("rentals",), "measures": ["revenue"], "split": "party",
                "scope": "e.id IN (SELECT x.entry_id FROM extracts x JOIN rental_invoices ri ON ri.extract_id=x.id)",
                "extras": ["capacity_rentals"]},
    "equipment": {"en": "Plant & equipment", "ar": "المعدات", "perm": ("equipment",), "measures": [], "split": None,
                  "extras": ["capacity_equipment"]},
    "people": {"en": "People & payroll", "ar": "الموظفون والرواتب", "perm": ("payroll",), "measures": ["payroll"], "split": "unit",
               "extras": ["capacity_people"]},
}


def _month_add(ym, n):
    y, m = int(ym[:4]), int(ym[5:7]) + n
    while m < 1:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return f"{y:04d}-{m:02d}"


def _month_end(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{ym}-{calendar.monthrange(y, m)[1]:02d}"


def _filters(measure, bu_id=None, scope=None):
    """scope: a fixed condition of a part of the app (MODULES[...]["scope"], never from a request)."""
    w, p = [LIVE, MEASURES[measure][2]], []
    if bu_id not in (None, ""):
        w.append("e.bu_id=?")
        p.append(int(bu_id))
    if scope:
        w.append(f"({scope})")
    return w, p


def totals(db, measure, date_from, date_to, dim=None, bu_id=None, scope=None):
    """{group: (value, documents)} for one period (group '' when not split)."""
    w, p = _filters(measure, bu_id, scope)
    g = DIM_SQL[dim] if dim else "''"
    rows = db.q(f"""SELECT {g} AS g, SUM({MEASURES[measure][3]}) AS v, COUNT(DISTINCT e.id) AS n
                    FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                    WHERE {' AND '.join(w)} AND e.date>=? AND e.date<=? GROUP BY g""", (*p, date_from, date_to))
    return {r["g"]: (to_major(r["v"] or 0), int(r["n"] or 0)) for r in rows if (r["v"] or 0) != 0 or r["n"]}


def monthly(db, measure, months=12, upto=None, bu_id=None, scope=None):
    """The last `months` months up to `upto` (YYYY-MM, default this month): [{month, value, documents}]."""
    last = upto or acc.today()[:7]
    first = _month_add(last, -(months - 1))
    w, p = _filters(measure, bu_id, scope)
    rows = {r["m"]: r for r in db.q(f"""SELECT substr(e.date,1,7) AS m, SUM({MEASURES[measure][3]}) AS v, COUNT(DISTINCT e.id) AS n
                    FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                    WHERE {' AND '.join(w)} AND e.date>=? AND e.date<=? GROUP BY m""", (*p, first + "-01", _month_end(last)))}
    out, m = [], first
    while m <= last:
        r = rows.get(m) or {}
        out.append({"month": m, "value": to_major(r.get("v") or 0), "documents": int(r.get("n") or 0)})
        m = _month_add(m, 1)
    return out


def _split(before, after):
    """Volume and value effects of one group: (change, volume effect, value effect) adding up exactly."""
    (va, na), (vb, nb) = before, after
    change = round(vb - va, 2)
    if na == 0:
        return change, change, 0.0
    vol = round((nb - na) * (va / na), 2)
    return change, vol, round(change - vol, 2)


def bridge(db, measure, dim, a, b, bu_id=None, scope=None, top=8):
    """From period a to period b ((from, to) each): each group's change split into volume and value, biggest first;
    the rest folded into one line."""
    ta, tb = totals(db, measure, *a, dim, bu_id, scope), totals(db, measure, *b, dim, bu_id, scope)
    rows = []
    for g in set(ta) | set(tb):
        before, after = ta.get(g, (0.0, 0)), tb.get(g, (0.0, 0))
        change, vol, val = _split(before, after)
        rows.append({"group": g, "before": before[0], "after": after[0], "docs_before": before[1], "docs_after": after[1],
                     "change": change, "volume_effect": vol, "value_effect": val})
    rows.sort(key=lambda r: -abs(r["change"]))
    if len(rows) > top:
        rest = rows[top:]
        rows = rows[:top] + [{"group": "…", "other": len(rest), **{k: round(sum(r[k] for r in rest), 2) for k in
                              ("before", "after", "change", "volume_effect", "value_effect")},
                              "docs_before": sum(r["docs_before"] for r in rest), "docs_after": sum(r["docs_after"] for r in rest)}]
    tot = lambda k: round(sum(r[k] for r in rows), 2)
    before, after = tot("before"), tot("after")
    lower = MEASURES[measure][4]
    up = [r for r in rows if r["change"] > 0 and r["group"] != "…"]
    down = [r for r in rows if r["change"] < 0 and r["group"] != "…"]
    lift = max(up, key=lambda r: r["change"]) if up else None
    drag = min(down, key=lambda r: r["change"]) if down else None
    if lower:     # for a cost, going up is the drag
        lift, drag = (min(down, key=lambda r: r["change"]) if down else None), (max(up, key=lambda r: r["change"]) if up else None)
    return {"measure": measure, "dim": dim, "a": list(a), "b": list(b), "before": before, "after": after, "change": round(after - before, 2),
            "volume_effect": tot("volume_effect"), "value_effect": tot("value_effect"), "rows": rows,
            "lift": lift and lift["group"], "drag": drag and drag["group"], "lower_is_better": lower}


def months_table(db, measure, dim, months=12, upto=None, bu_id=None, scope=None):
    """Month by month: the change on the month before, split into volume and value, and the group that lifted it most
    and the one that held it back most."""
    last = upto or acc.today()[:7]
    out = []
    for i in range(months - 1, -1, -1):
        m = _month_add(last, -i)
        prev = _month_add(m, -1)
        br = bridge(db, measure, dim, (prev + "-01", _month_end(prev)), (m + "-01", _month_end(m)), bu_id, scope, top=10 ** 6)
        lift = next((r for r in br["rows"] if r["group"] == br["lift"]), None)
        drag = next((r for r in br["rows"] if r["group"] == br["drag"]), None)
        out.append({"month": m, "value": br["after"], "change": br["change"], "pct": round(br["change"] / br["before"], 4) if br["before"] else None,
                    "volume_effect": br["volume_effect"], "value_effect": br["value_effect"],
                    "lift": lift and {"group": lift["group"], "change": lift["change"]}, "drag": drag and {"group": drag["group"], "change": drag["change"]}})
    return out


def trend(values):
    """How a series moves: {direction: up | down | flat | swings, pct_per_month, swing} from a straight line through
    the months and the average size of the month-to-month moves."""
    v = [x for x in values]
    while v and v[0] == 0:
        v = v[1:]
    if len(v) < 3:
        return {"direction": "short", "months": len(v)}
    n = len(v)
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(v) / n
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, v)) / sum((x - mx) ** 2 for x in xs)
    per = slope / abs(my) if my else 0.0
    moves = [abs(b - a) / abs(a) for a, b in zip(v, v[1:]) if a]
    swing = statistics.mean(moves) if moves else 0.0
    direction = "up" if per >= 0.015 else "down" if per <= -0.015 else ("swings" if swing >= 0.08 else "flat")
    return {"direction": direction, "pct_per_month": round(per, 4), "swing": round(swing, 4), "months": n}


def _budget(db, measure, ym, bu_id=None):
    """The budget for a measure in a month (budgets of its accounts), or None when nothing is budgeted."""
    if measure not in ("revenue", "costs", "payroll"):
        return None
    y, m = int(ym[:4]), int(ym[5:7])
    cond = {"revenue": "a.type='Revenue'", "costs": "a.type IN ('COGS','Expense')", "payroll": "a.subtype='employee'"}[measure]
    rows = db.q(f"SELECT b.months, b.bu_id FROM budgets b JOIN accounts a ON a.code=b.account WHERE b.year=? AND {cond}", (y,))
    tot, any_ = 0, False
    for r in rows:      # the whole business: every budget line (company-wide and per unit); one unit: its own
        if bu_id not in (None, "") and int(r["bu_id"] or 0) != int(bu_id):
            continue
        try:
            tot += int(json.loads(r["months"] or "[]")[m - 1] or 0)
            any_ = True
        except (ValueError, IndexError, TypeError):
            continue
    return to_major(tot) if any_ and tot else None


def pace(db, measure, target=None, today=None, bu_id=None, scope=None):
    """This month so far, where it is heading at the same pace, against last month and the target (given, or the
    budget), and what each remaining day needs to reach the target."""
    t = _dt.date.fromisoformat(today or acc.today())
    ym = t.isoformat()[:7]
    days = calendar.monthrange(t.year, t.month)[1]
    done = t.day
    so_far = totals(db, measure, ym + "-01", t.isoformat(), None, bu_id, scope).get("", (0.0, 0))[0]
    last_m = _month_add(ym, -1)
    last = totals(db, measure, last_m + "-01", _month_end(last_m), None, bu_id, scope).get("", (0.0, 0))[0]
    projected = round(so_far / done * days, 2) if done else so_far
    # a budget is for the measure as a whole (or a unit): one part of it (e.g. rentals' revenue) has no budget of its own
    tgt = float(target) if target not in (None, "", 0) else (None if scope else _budget(db, measure, ym, bu_id))
    left = days - done
    need = round((tgt - so_far) / left, 2) if tgt is not None and left > 0 else None
    return {"measure": measure, "month": ym, "days": days, "day": done, "days_left": left, "so_far": so_far, "projected": projected,
            "last_month": last, "vs_last": round(projected - last, 2), "vs_last_pct": round((projected - last) / last, 4) if last else None,
            "target": tgt, "target_source": "set" if target not in (None, "", 0) else ("budget" if tgt is not None else None),
            "projected_pct": round(projected / tgt, 4) if tgt else None, "need_per_day": need,
            "pace_per_day": round(so_far / done, 2) if done else 0.0, "lower_is_better": MEASURES[measure][4],
            "early": done < 7}     # a projection from the first days of a month is rough


def kpi(db, measure, target=None, bu_id=None, scope=None, today=None):
    """A card: this month's projection against the target and last month, with the last 12 months to draw."""
    p = pace(db, measure, target, today, bu_id, scope)
    series = monthly(db, measure, 12, p["month"], bu_id, scope)
    vals = [s["value"] for s in series[:-1]] + [p["projected"]]
    return {**p, "label": [MEASURES[measure][0], MEASURES[measure][1]], "series": vals, "trend": trend(vals[:-1])}


def profit_by_unit(db, a, b):
    """Net profit per business unit in two periods (revenue less costs), biggest change first."""
    def per(f, t):
        rows = db.q(f"""SELECT COALESCE((SELECT b.code || ' ' || b.name FROM business_units b WHERE b.id=e.bu_id), '—') AS g,
                        SUM(CASE WHEN a.type='Revenue' THEN l.credit-l.debit WHEN a.type IN ('COGS','Expense') THEN l.debit-l.credit ELSE 0 END) AS gross,
                        SUM(CASE WHEN a.type='Revenue' THEN l.credit-l.debit ELSE 0 END) AS rev,
                        SUM(CASE WHEN a.type IN ('COGS','Expense') THEN l.debit-l.credit ELSE 0 END) AS cost
                        FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                        WHERE {LIVE} AND a.type IN ('Revenue','COGS','Expense') AND e.date>=? AND e.date<=? GROUP BY g""", (f, t))
        return {r["g"]: (to_major(r["rev"] or 0), to_major(r["cost"] or 0)) for r in rows}
    pa, pb = per(*a), per(*b)
    rows = []
    for g in set(pa) | set(pb):
        (ra, ca), (rb, cb) = pa.get(g, (0.0, 0.0)), pb.get(g, (0.0, 0.0))
        rows.append({"group": g, "before": round(ra - ca, 2), "after": round(rb - cb, 2), "change": round((rb - cb) - (ra - ca), 2),
                     "revenue_change": round(rb - ra, 2), "cost_change": round(-(cb - ca), 2)})
    rows.sort(key=lambda r: -abs(r["change"]))
    return {"rows": rows, "before": round(sum(r["before"] for r in rows), 2), "after": round(sum(r["after"] for r in rows), 2)}


# ------------------------------------------------------------------ operations
def funnel(db, date_from=None, date_to=None):
    """Deals: how many reached each stage, won and lost (with why), by source and by owner (values are kept in minor units)."""
    from .crm import STAGES, STAGE_KEYS
    w, p = ["1=1"], []
    if date_from:
        w.append("substr(created_at,1,10)>=?")
        p.append(date_from)
    if date_to:
        w.append("substr(created_at,1,10)<=?")
        p.append(date_to)
    deals = db.q(f"SELECT stage, value, source, owner, lost_reason FROM crm_deals WHERE {' AND '.join(w)}", p)
    order = [k for k in STAGE_KEYS if k != "lost"]
    idx = {k: i for i, k in enumerate(order)}
    stages = []
    for i, k in enumerate(order):
        reached = [d for d in deals if d["stage"] != "lost" and idx.get(d["stage"], -1) >= i]
        stages.append({"stage": k, "en": STAGES[STAGE_KEYS.index(k)][1], "ar": STAGES[STAGE_KEYS.index(k)][2],
                       "count": len(reached), "value": to_major(sum(int(d["value"] or 0) for d in reached))})
    won = [d for d in deals if d["stage"] == "won"]
    lost = [d for d in deals if d["stage"] == "lost"]
    closed = len(won) + len(lost)

    def by(key):
        g = {}
        for d in deals:
            x = (d[key] or "").strip() or "—"
            r = g.setdefault(x, {"group": x, "deals": 0, "won": 0, "lost": 0, "open": 0, "won_value": 0.0})
            r["deals"] += 1
            r["won" if d["stage"] == "won" else "lost" if d["stage"] == "lost" else "open"] += 1
            if d["stage"] == "won":
                r["won_value"] += to_major(d["value"] or 0)
        for r in g.values():
            r["win_rate"] = round(r["won"] / (r["won"] + r["lost"]), 4) if (r["won"] + r["lost"]) else None
            r["won_value"] = round(r["won_value"], 2)
        return sorted(g.values(), key=lambda r: -r["deals"])
    reasons = {}
    for d in lost:
        x = (d["lost_reason"] or "").strip() or "—"
        reasons[x] = reasons.get(x, 0) + 1
    return {"deals": len(deals), "open": len(deals) - closed, "won": len(won), "lost": len(lost),
            "win_rate": round(len(won) / closed, 4) if closed else None, "won_value": to_major(sum(int(d["value"] or 0) for d in won)),
            "stages": stages, "by_source": by("source"), "by_owner": by("owner"),
            "lost_reasons": sorted(({"reason": k, "count": v} for k, v in reasons.items()), key=lambda r: -r["count"])}


RENT_CATS = {"scaffolding": ("Scaffolding", "سقالات معدنية"), "elevator": ("Construction elevators", "مصاعد إنشائية"),
             "cradle_manual": ("Cradles (manual)", "سقالات معلقة يدوية"), "cradle_motorized": ("Cradles (motorized)", "سقالات معلقة كهربائية"),
             "other": ("Other equipment", "معدات أخرى"), "general": ("General", "عام")}


def capacity_rentals(db):
    """Rental items: how many the business owns, how many are out with customers, and the share in use."""
    rows = db.q("""SELECT i.id, i.code, i.name, i.name_ar, i.category, i.unit,
                   COALESCE((SELECT SUM(qty) FROM rent_lots WHERE item_id=i.id),0) AS owned,
                   COALESCE((SELECT SUM(CASE WHEN kind='out' THEN qty WHEN kind IN ('back','lost','damaged') THEN -qty ELSE 0 END)
                             FROM rent_moves WHERE item_id=i.id),0) AS out_now,
                   COALESCE((SELECT SUM(qty) FROM rent_moves WHERE item_id=i.id AND kind IN ('lost','damaged')),0) AS gone
                   FROM rent_items i WHERE i.active=1 ORDER BY i.category, i.code""")
    items = []
    for r in rows:
        owned = float(r["owned"] or 0) - float(r["gone"] or 0)
        out = max(0.0, float(r["out_now"] or 0))
        if owned <= 0 and out <= 0:
            continue      # nothing of it owned yet
        cat = RENT_CATS.get(r["category"] or "", (r["category"] or "", r["category"] or ""))
        items.append({"code": r["code"], "name": r["name"], "name_ar": r["name_ar"], "group": cat[0], "group_ar": cat[1], "unit": r["unit"],
                      "total": owned, "used": out, "share": round(out / owned, 4) if owned > 0 else None})
    return _cap("rentals", items)


def capacity_equipment(db, today=None):
    """Plant and equipment: units used this month (hours booked) against all units in service."""
    t = (today or acc.today())
    ym = t[:7]
    rows = db.q("""SELECT e.code, e.name, e.category, e.status,
                   COALESCE((SELECT SUM(hours) FROM equipment_usage u WHERE u.equipment_id=e.id AND u.date>=? AND u.date<=?),0) AS hours
                   FROM equipment e ORDER BY e.category, e.code""", (ym + "-01", t))
    items = [{"code": r["code"], "name": r["name"], "group": r["category"] or "", "status": r["status"], "hours": float(r["hours"] or 0),
              "total": 1, "used": 1 if (r["hours"] or 0) > 0 else 0, "share": 1.0 if (r["hours"] or 0) > 0 else 0.0}
             for r in rows if (r["status"] or "").lower() not in ("sold", "disposed", "retired")]
    return _cap("equipment", items)


def capacity_people(db, today=None):
    """People: who is recorded at work today against everyone active."""
    t = today or acc.today()
    emps = db.q("SELECT id, code, name, department FROM employees WHERE status='active' AND (hire_date='' OR hire_date IS NULL OR hire_date<=?)", (t,))
    att = {r["employee_id"]: r["status"] for r in db.q("SELECT employee_id, status FROM attendance WHERE date=?", (t,))}
    items = [{"code": e["code"], "name": e["name"], "group": e["department"] or "", "status": att.get(e["id"], ""),
              "total": 1, "used": 1 if att.get(e["id"]) in ("present", "half") else 0} for e in emps]
    for i in items:
        i["share"] = float(i["used"])
    return _cap("people", items)


def _cap(kind, items):
    groups = {}
    for i in items:
        g = groups.setdefault(i["group"], {"group": i["group"], "group_ar": i.get("group_ar") or i["group"], "total": 0.0, "used": 0.0})
        g["total"] += i["total"]
        g["used"] += i["used"]
    for g in groups.values():
        g["share"] = round(g["used"] / g["total"], 4) if g["total"] else None
    total, used = sum(i["total"] for i in items), sum(i["used"] for i in items)
    return {"kind": kind, "total": total, "used": used, "share": round(used / total, 4) if total else None,
            "groups": sorted(groups.values(), key=lambda g: g["group"]), "items": items}


# ------------------------------------------------------------------ one module's page
def allowed(can, mod):
    return any(can(p) for p in MODULES[mod]["perm"])


def page(db, mod, can, q=None):
    """Everything the Insights page shows for one part of the app (only what the person may see)."""
    q = q or {}
    if mod not in MODULES or not allowed(can, mod):
        raise PermissionError()
    cfg = MODULES[mod]
    sl = cfg.get("scope")
    bu = q.get("bu_id") or None
    today = acc.today()
    this_m = today[:7]
    # periods compared: the last complete month against the one before (or what was asked for)
    b_m = q.get("month") or _month_add(this_m, -1)
    a_m = q.get("vs") or _month_add(b_m, -1)
    a, b = (a_m + "-01", _month_end(a_m)), (b_m + "-01", _month_end(b_m))
    out = {"module": mod, "modules": [{"key": k, "en": v["en"], "ar": v["ar"]} for k, v in MODULES.items() if allowed(can, k)],
           "month": b_m, "vs": a_m, "today": today, "measures": [], "dims": {k: list(v) for k, v in DIMS.items()}}
    split = q.get("dim") if q.get("dim") in DIMS else cfg.get("split")
    for m in cfg["measures"]:
        if not any(can(p) for p in MEASURES[m][6]):
            continue
        dims = [d for d in MEASURES[m][5] if d in DIMS]
        d = split if split in dims else dims[0]
        card = kpi(db, m, None, bu, sl, today)
        if not any(s for s in card["series"]) and not card["so_far"]:
            out["measures"].append({"measure": m, "empty": True, "label": card["label"]})
            continue
        out["measures"].append({"measure": m, "label": card["label"], "kpi": card, "dims": dims, "dim": d,
                                "bridge": bridge(db, m, d, a, b, bu, sl),
                                "history": months_table(db, m, d, 12, b_m, bu, sl)})
    if cfg.get("profit") and can("reports"):
        out["profit"] = profit_by_unit(db, a, b)
    for x in cfg["extras"]:
        if x == "funnel":
            out["funnel"] = funnel(db, q.get("from"), q.get("to"))
        elif x == "capacity_rentals":
            out["capacity"] = capacity_rentals(db)
        elif x == "capacity_equipment":
            out["capacity"] = capacity_equipment(db, today)
        elif x == "capacity_people":
            out["capacity"] = capacity_people(db, today)
    return out
