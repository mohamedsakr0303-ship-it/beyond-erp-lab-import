"""Stock planning: minimum quantities with their alerts, and a forecast of what comes in and goes out, from history.

Minimum quantity
  Each item can have a minimum (stock_items.reorder_level), the same in every warehouse that holds it, and a different
  one for chosen warehouses (stock_minimums). How it is announced is the item's choice (stock_items.alert_mode):
    both    notification (bell, device pop-up, phone push) and on screen (highlighted on Stock, its banner and filter)
    notify  notification only
    visual  on screen only
    none    neither: the minimum is kept for the forecast and reports only
  A phone push goes once each time an item falls to its minimum in a warehouse (stock_alert_log); when it is back above,
  the next fall announces again.
  With "automatic minimum" (stock_items.auto_min) the minimum used is the forecast's suggestion, once there is one.

Forecast (nothing to fill in; it reads the stock movements)
  Months of history per item and warehouse — what came in (receipts, transfers in) and what went out (issues, sales,
  transfers out); count corrections are left out, they are not use — and per project, what it received (issued from a store, or bought straight for it on a
  journal voucher's item lines). Each series is carried forward with the method that suits it:
    average   fewer than 4 months seen
    croston   intermittent use (on average more than 1.32 months between movements): the average size of a movement
              over the average gap between movements (Syntetos–Boylan correction)
    holt      otherwise: exponential smoothing with a damped trend
  From the forecast: days of cover, the day stock is expected to run out if nothing more comes in, and — when the
  item's delivery time (stock_items.lead_days) is known — a suggested minimum: use during the delivery time plus
  safety stock for a 95% service level (1.65 × the spread of monthly use × √(delivery time in months)).
  Nothing is invented: without a delivery time there is no suggestion, and without history there is no forecast.
"""
import datetime as _dt
import math

from . import accounting as acc
from .accounting import ValidationError
from .db import to_major

MODES = ("both", "notify", "visual", "none")
NOTIFY = ("both", "notify")
VISUAL = ("both", "visual")
Z95 = 1.65


# ------------------------------------------------------------------ months
def _month(d):
    return d[:7]


def _add_months(m, n):
    y, mm = int(m[:4]), int(m[5:7]) - 1 + n
    return f"{y + mm // 12:04d}-{mm % 12 + 1:02d}"


def month_span(back=12, today=None):
    """The last `back` full months, oldest first (the current month is not finished, so it is not history)."""
    cur = _month(today or acc.today())
    return [_add_months(cur, -k) for k in range(back, 0, -1)]


# ------------------------------------------------------------------ forecasting methods
def _strip(hist):
    i = 0
    while i < len(hist) and not hist[i]:
        i += 1
    return hist[i:]


def project(hist, n=3):
    """(forecast for the next n months, method, months used). hist: monthly quantities, oldest first (all >= 0)."""
    h = _strip([max(0.0, float(v or 0)) for v in hist])
    if not h:
        return [0.0] * n, "none", 0
    m = len(h)
    if m < 4:
        avg = sum(h) / m
        return [round(avg, 3)] * n, "average", m
    zeros = sum(1 for v in h if not v)
    if m - zeros < 2:                         # a single movement: spread it over the months seen
        return [round(sum(h) / m, 3)] * n, "average", m
    if m / (m - zeros) > 1.32:                # intermittent (average gap between movements > 1.32 months, Syntetos–Boylan): Croston
        a = 0.1
        z = p = None
        gap = 1
        for v in h:
            if v:
                z = v if z is None else z + a * (v - z)
                p = gap if p is None else p + a * (gap - p)
                gap = 1
            else:
                gap += 1
        p = max(p, gap - 1) if gap > 1 else p       # months quiet since the last movement count too
        rate = (1 - a / 2) * z / p if z and p else 0.0
        return [round(rate, 3)] * n, "croston", m
    alpha, beta, phi = 0.5, 0.3, 0.8         # Holt, damped trend
    level, trend = h[0], (h[-1] - h[0]) / (m - 1)      # start from the average slope, not the first jump
    for v in h[1:]:
        prev = level
        level = alpha * v + (1 - alpha) * (level + phi * trend)
        trend = beta * (level - prev) + (1 - beta) * phi * trend
    out, damp = [], 0.0
    for k in range(1, n + 1):
        damp += phi ** k
        out.append(round(max(0.0, level + damp * trend), 3))
    return out, "holt", m


def _spread(h):
    h = _strip(h)
    if len(h) < 2:
        return 0.0
    mu = sum(h) / len(h)
    return math.sqrt(sum((v - mu) ** 2 for v in h) / (len(h) - 1))


# ------------------------------------------------------------------ minimums
def item_settings(db, item_id):
    it = db.one("SELECT id, name, reorder_level, alert_mode, lead_days, auto_min FROM stock_items WHERE id=?", (int(item_id),))
    if not it:
        raise ValidationError("Not found")
    it["warehouses"] = db.q("SELECT m.warehouse_id, w.name AS warehouse, m.min_qty FROM stock_minimums m JOIN warehouses w ON w.id=m.warehouse_id "
                            "WHERE m.item_id=? ORDER BY w.name", (int(item_id),))
    return it


def save_settings(db, item_id, d):
    """The item's alert choice, delivery time, automatic minimum and per-warehouse minimums ({warehouse_id: qty or ''})."""
    item_id = int(item_id)
    if not db.one("SELECT 1 FROM stock_items WHERE id=?", (item_id,)):
        raise ValidationError("Not found")
    sets, vals = [], []
    if "alert_mode" in d:
        mode = d.get("alert_mode") or "both"
        if mode not in MODES:
            raise ValidationError("Choose how to be alerted at the minimum")
        sets.append("alert_mode=?")
        vals.append(mode)
    if "lead_days" in d:
        lead = acc.num(d.get("lead_days"), "delivery time (days)")
        if lead < 0 or lead > 365:
            raise ValidationError("The delivery time must be between 0 and 365 days")
        sets.append("lead_days=?")
        vals.append(int(round(lead)))
    if "auto_min" in d:
        sets.append("auto_min=?")
        vals.append(1 if d.get("auto_min") in (1, True, "1", "true", "on", "yes") else 0)
    with db.tx():
        if sets:
            db.conn.execute(f"UPDATE stock_items SET {', '.join(sets)} WHERE id=?", (*vals, item_id))
        if isinstance(d.get("warehouses"), dict):
            for wid, q in d["warehouses"].items():
                wid = int(wid)
                if not db.one("SELECT 1 FROM warehouses WHERE id=?", (wid,)):
                    raise ValidationError("Unknown warehouse")
                if q in ("", None):
                    db.conn.execute("DELETE FROM stock_minimums WHERE item_id=? AND warehouse_id=?", (item_id, wid))
                    continue
                q = acc.num(q, "minimum quantity")
                if q < 0:
                    raise ValidationError("A minimum quantity cannot be negative")
                db.conn.execute("INSERT INTO stock_minimums(item_id, warehouse_id, min_qty) VALUES (?,?,?) "
                                "ON CONFLICT(item_id, warehouse_id) DO UPDATE SET min_qty=excluded.min_qty", (item_id, wid, q))
    return item_settings(db, item_id)


def _suggestions(db, today=None):
    """{(item_id, warehouse_id): suggested minimum} for items with a delivery time and history of use."""
    out = {}
    if not db.one("SELECT 1 FROM stock_items WHERE lead_days>0 LIMIT 1"):
        return out
    for r in forecast(db, today=today)["rows"]:
        if r.get("suggested_min") is not None:
            out[(r["item_id"], r["warehouse_id"])] = r["suggested_min"]
    return out


def levels(db, warehouse_id=None, today=None):
    """Every item × warehouse a minimum applies to, with what is on hand: the item's minimum where it is held, and the
    warehouse's own minimum wherever one is set (even with nothing there yet)."""
    w, p = "", []
    if warehouse_id:
        w, p = " AND w.id=?", [int(warehouse_id)]
    rows = db.q(f"""SELECT i.id AS item_id, i.name, i.unit, i.sku, i.reorder_level, i.alert_mode, i.auto_min, i.lead_days,
                           w.id AS warehouse_id, w.name AS warehouse, COALESCE(l.qty, 0) AS qty, m.min_qty AS wh_min
                    FROM stock_items i JOIN warehouses w
                    LEFT JOIN stock_levels l ON l.item_id=i.id AND l.warehouse_id=w.id
                    LEFT JOIN stock_minimums m ON m.item_id=i.id AND m.warehouse_id=w.id
                    WHERE (m.min_qty IS NOT NULL OR (l.item_id IS NOT NULL AND (i.reorder_level>0 OR i.auto_min=1))){w}
                    ORDER BY i.name, w.name""", p)
    sug = _suggestions(db, today) if any(r["auto_min"] for r in rows) else {}
    out = []
    for r in rows:
        auto = sug.get((r["item_id"], r["warehouse_id"])) if r["auto_min"] else None
        mn = auto if auto is not None else (r["wh_min"] if r["wh_min"] is not None else (r["reorder_level"] or 0))
        if not mn:
            continue
        r.update(min=round(mn, 3), source="auto" if auto is not None else "warehouse" if r["wh_min"] is not None else "item",
                 below=r["qty"] <= mn + 1e-9, short=round(max(0.0, mn - r["qty"]), 3), alert_mode=r["alert_mode"] or "both")
        out.append(r)
    return out


def below(db, modes=None, warehouse_id=None):
    return [r for r in levels(db, warehouse_id) if r["below"] and (modes is None or r["alert_mode"] in modes)]


def text_of(r):
    return (f"{r['name']} is at or below its minimum in {r['warehouse']}: {r['qty']:g} {r['unit']} left (minimum {r['min']:g})",
            f"{r['name']} وصل إلى حده الأدنى أو أقل في {r['warehouse']}: متبقٍ {r['qty']:g} {r['unit']} (الحد الأدنى {r['min']:g})")


def bell_rows(db):
    """The bell: one line per item and warehouse at or below its minimum, for items announced by notification."""
    out = []
    for r in below(db, NOTIFY):
        en, ar = text_of(r)
        out.append({"level": "bad" if r["qty"] <= 0 else "warn", "en": en, "ar": ar, "href": "#/stock?low=1", "count": 1,
                    "kind": "stock_low", "type": "stock_low"})
    return out


def pushes(db, viewer=None):
    """Phone pushes for items that newly fell to their minimum (once per fall), to everyone who sees stock and receives
    stock alerts. Items back above their minimum are forgotten, so the next fall is announced again."""
    from . import alerttypes, auth
    low = {(r["item_id"], r["warehouse_id"]): r for r in below(db, NOTIFY)}
    logged = {(x["item_id"], x["warehouse_id"]) for x in db.q("SELECT item_id, warehouse_id FROM stock_alert_log")}
    for key in logged - set(low):
        db.x("DELETE FROM stock_alert_log WHERE item_id=? AND warehouse_id=?", key)
    new = [r for k, r in low.items() if k not in logged]
    if not new:
        return []
    people = []
    for u in db.q("SELECT * FROM users WHERE active=1"):
        pu = auth.public_user(dict(u))
        if auth.can(pu, "stock.balances") and alerttypes.filter_rows(db, pu, [{"kind": "stock_low", "type": "stock_low", "href": "#/stock", "en": ""}]):
            people.append(u["username"])
    out = []
    for r in new:
        db.x("INSERT OR REPLACE INTO stock_alert_log(item_id, warehouse_id, at) VALUES (?,?,?)", (r["item_id"], r["warehouse_id"], acc.now_iso()))
        if people:
            en, _ar = text_of(r)
            out.append({"kind": "notify", "to": people, "title": "Stock below minimum", "body": en[:180], "href": "#/stock?low=1",
                        "tag": f"stock{r['item_id']}-{r['warehouse_id']}", "pref": "reminders"})
    return out


# ------------------------------------------------------------------ forecast
def _series(db, months, warehouse_id=None, item_id=None):
    """{(item, warehouse): {"in": [...], "out": [...]}} monthly quantities over `months`."""
    idx = {m: i for i, m in enumerate(months)}
    # count corrections (adjustments, stock counts) put the books right; they are not use, so they are not forecast
    w, p = ["substr(m.date,1,7) BETWEEN ? AND ?", "m.kind<>'Adjustment'"], [months[0], months[-1]]
    if warehouse_id:
        w.append("m.warehouse_id=?")
        p.append(int(warehouse_id))
    if item_id:
        w.append("m.item_id=?")
        p.append(int(item_id))
    out = {}
    for r in db.q(f"SELECT m.item_id, m.warehouse_id, substr(m.date,1,7) AS mo, SUM(CASE WHEN m.qty>0 THEN m.qty ELSE 0 END) AS qin, "
                  f"SUM(CASE WHEN m.qty<0 THEN -m.qty ELSE 0 END) AS qout FROM stock_moves m WHERE {' AND '.join(w)} "
                  f"GROUP BY 1, 2, 3", p):
        s = out.setdefault((r["item_id"], r["warehouse_id"]), {"in": [0.0] * len(months), "out": [0.0] * len(months)})
        s["in"][idx[r["mo"]]] += r["qin"]
        s["out"][idx[r["mo"]]] += r["qout"]
    return out


def _horizon_months(today, n):
    cur = _month(today)
    return [_add_months(cur, k) for k in range(0, n)]


def forecast(db, back=12, horizon=3, warehouse_id=None, item_id=None, today=None):
    """Per item and warehouse: history in / out, the forecast for the current month and the next ones, days of cover,
    the expected run-out day if nothing more comes in, and the suggested minimum (with a delivery time)."""
    today = today or acc.today()
    back, horizon = max(3, min(int(back or 12), 36)), max(1, min(int(horizon or 3), 12))
    months = month_span(back, today)
    ahead = _horizon_months(today, horizon)
    ser = _series(db, months, warehouse_id, item_id)
    lv = {(r["item_id"], r["warehouse_id"]): r for r in db.q("SELECT item_id, warehouse_id, qty, avg_cost FROM stock_levels")}
    items = {r["id"]: r for r in db.q("SELECT id, name, unit, sku, category, reorder_level, lead_days, auto_min, alert_mode FROM stock_items")}
    whs = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM warehouses")}
    mins = {(r["item_id"], r["warehouse_id"]): r["min_qty"] for r in db.q("SELECT * FROM stock_minimums")}
    keys = set(ser) | {k for k, v in lv.items() if v["qty"]}
    if warehouse_id:
        keys = {k for k in keys if k[1] == int(warehouse_id)}
    if item_id:
        keys = {k for k in keys if k[0] == int(item_id)}
    rows = []
    for key in keys:
        it, wh = items.get(key[0]), whs.get(key[1])
        if not it or wh is None:
            continue
        s = ser.get(key) or {"in": [0.0] * len(months), "out": [0.0] * len(months)}
        fin, m_in, n_in = project(s["in"], horizon)
        fout, m_out, n_out = project(s["out"], horizon)
        qty = (lv.get(key) or {}).get("qty", 0.0)
        cost = (lv.get(key) or {}).get("avg_cost", 0.0)
        bal, path = qty, []
        for a, b in zip(fin, fout):
            bal += a - b
            path.append(round(bal, 3))
        daily = fout[0] / 30 if fout and fout[0] else 0.0
        cover = round(qty / daily) if daily > 0 and qty > 0 else (0 if qty <= 0 and daily > 0 else None)
        runs_out = (_dt.date.fromisoformat(today) + _dt.timedelta(days=cover)).isoformat() if cover is not None else None
        sug = None
        if it["lead_days"] and n_out:
            lt = it["lead_days"] / 30
            sug = round(fout[0] * lt + Z95 * _spread(s["out"]) * math.sqrt(lt), 1)
        set_min = mins.get(key, it["reorder_level"] or 0)
        rows.append({"item_id": key[0], "warehouse_id": key[1], "item": it["name"], "unit": it["unit"], "sku": it["sku"],
                     "category": it["category"], "warehouse": wh, "qty": round(qty, 3), "avg_cost": to_major(cost),
                     "hist_in": [round(v, 3) for v in s["in"]], "hist_out": [round(v, 3) for v in s["out"]],
                     "fc_in": fin, "fc_out": fout, "method_in": m_in, "method_out": m_out, "months_in": n_in, "months_out": n_out,
                     "balance": path, "out_value": round(sum(fout) * to_major(cost), 2), "cover_days": cover, "runs_out": runs_out,
                     "lead_days": it["lead_days"] or 0, "min": set_min, "suggested_min": sug, "auto_min": bool(it["auto_min"]),
                     "below_after": next((ahead[i] for i, b in enumerate(path) if set_min and b <= set_min), None)})
    rows.sort(key=lambda r: (r["cover_days"] is None, r["cover_days"] if r["cover_days"] is not None else 0, r["item"], r["warehouse"]))
    return {"months": months, "ahead": ahead, "rows": rows, "today": today}


def project_forecast(db, back=12, horizon=3, bu_id=None, today=None):
    """Per project and item: what it received each month (issued from a store, or bought straight for it), and the
    forecast — the materials it is expected to need next, in quantity and at the latest cost."""
    today = today or acc.today()
    back, horizon = max(3, min(int(back or 12), 36)), max(1, min(int(horizon or 3), 12))
    months = month_span(back, today)
    idx = {m: i for i, m in enumerate(months)}
    live = f"{acc.LIVE} AND e.reversed_by IS NULL AND e.reversal_of IS NULL"
    w, p = "", [months[0], months[-1]]
    if bu_id:
        w, p = " AND x.bu_id=?", p + [int(bu_id)]
    q = f"""SELECT x.bu_id, x.item_id, x.mo, SUM(x.qty) AS qty, SUM(x.val) AS val FROM (
              SELECT s.bu_id, s.item_id, substr(s.date,1,7) AS mo, -s.qty AS qty, -s.qty*s.rate AS val FROM stock_moves s
                JOIN journal_entries e ON e.id=s.entry_id WHERE {live} AND s.qty<0 AND s.bu_id IS NOT NULL
              UNION ALL
              SELECT m.bu_id, m.item_id, substr(m.date,1,7), m.qty, m.amount FROM material_lines m JOIN journal_entries e ON e.id=m.entry_id
                WHERE {live} AND m.warehouse_id IS NULL AND m.item_id IS NOT NULL AND m.bu_id IS NOT NULL) x
            WHERE x.mo BETWEEN ? AND ?{w} GROUP BY 1, 2, 3"""
    ser = {}
    for r in db.q(q, p):
        s = ser.setdefault((r["bu_id"], r["item_id"]), {"qty": [0.0] * len(months), "val": [0.0] * len(months)})
        s["qty"][idx[r["mo"]]] += r["qty"]
        s["val"][idx[r["mo"]]] += r["val"]
    bus = {r["id"]: r for r in db.q("SELECT id, code, name, status FROM business_units")}
    items = {r["id"]: r for r in db.q("SELECT id, name, unit FROM stock_items")}
    rows = []
    for (bu, iid), s in ser.items():
        if bu not in bus or iid not in items:
            continue
        fq, meth, n = project(s["qty"], horizon)
        tq = sum(s["qty"])
        unit_cost = to_major(sum(s["val"]) / tq) if tq else 0.0
        rows.append({"bu_id": bu, "bu_code": bus[bu]["code"], "bu": bus[bu]["name"], "status": bus[bu]["status"], "item_id": iid,
                     "item": items[iid]["name"], "unit": items[iid]["unit"], "hist": [round(v, 3) for v in s["qty"]], "fc": fq,
                     "method": meth, "months": n, "unit_cost": unit_cost, "fc_value": round(sum(fq) * unit_cost, 2)})
    rows.sort(key=lambda r: (r["bu_code"], -r["fc_value"], r["item"]))
    totals = {}
    for r in rows:
        t = totals.setdefault(r["bu_id"], {"bu_id": r["bu_id"], "bu_code": r["bu_code"], "bu": r["bu"], "fc_value": 0.0, "items": 0})
        t["fc_value"] = round(t["fc_value"] + r["fc_value"], 2)
        t["items"] += 1
    return {"months": months, "ahead": _horizon_months(today, horizon), "rows": rows, "projects": sorted(totals.values(), key=lambda t: t["bu_code"])}


def apply_suggestions(db, item_ids=None):
    """Set each chosen item's minimum per warehouse to the forecast's suggestion (only where there is one)."""
    n = 0
    with db.tx():
        for (iid, wid), v in _suggestions(db).items():
            if item_ids and iid not in item_ids:
                continue
            db.conn.execute("INSERT INTO stock_minimums(item_id, warehouse_id, min_qty) VALUES (?,?,?) "
                            "ON CONFLICT(item_id, warehouse_id) DO UPDATE SET min_qty=excluded.min_qty", (iid, wid, v))
            n += 1
    return n
