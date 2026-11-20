"""Stock costing, per item and warehouse, in date order: FIFO (first in, first out; the default) or moving average.

Every movement of an item is replayed in order: by date; on the same day, what comes in from outside (receipts, count
gains) before what goes out or moves between stores; then in the order entered. Each warehouse keeps its own stock.

FIFO, the way the Excel workbook computes it too: the units that came into a warehouse are numbered in that order, each
batch at its own unit value. Taking out q units after A units already went out costs what units A+1 … A+q cost:
C(A+q) − C(A), where C(X) is the cost of the first X units in. A transfer takes its units out of the source that way and
arrives as one batch at that value. A count gain comes in at the unit cost given on the count, else at the average of
what is on hand then.

Moving average: each receipt re-averages what is on hand; issues and transfers take out at that average.

A movement dated before later movements of the same item changes what those later movements cost. Their entries are not
rewritten: each changed movement gets a "Stock Cost Adjustment" entry for the difference, dated on that movement's own
date (always an open period, as the earlier movement itself could only be posted in one), linked in stock_recosts so it
is undone with its movement. Stock that would fall below zero at any point from the change on is refused.
"""
import bisect
import datetime as _dt
from decimal import Decimal, ROUND_HALF_UP

from .db import to_major

EPS = 1e-9
METHODS = ("fifo", "average")


class StockShort(Exception):
    """The change would leave a warehouse with less than nothing of an item at some point."""

    def __init__(self, move, available):
        super().__init__("stock short")
        self.move, self.available = move, available


def method(db):
    return "average" if (db.setting("stock_costing") or "").strip() == "average" else "fifo"


def half_up(x):
    """Round to a whole minor unit the way a spreadsheet's ROUND does (halves away from zero)."""
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def phase(m):
    """0: comes in from outside (receipt, count gain); 1: goes out, or arrives from another store."""
    return 0 if m["qty"] > 0 and m["kind"] != "Transfer" else 1


def order_key(m):
    return (m["date"], phase(m), m["id"])


def moves(db, item_id):
    return sorted(db.q("SELECT id, item_id, warehouse_id, date, qty, rate, value, booked, link_id, fixed, kind, entry_id, bu_id "
                       "FROM stock_moves WHERE item_id=?", (item_id,)), key=order_key)


class _Store:
    """One warehouse's stock of the item as the movements go by."""

    def __init__(self):
        self.batches = []        # (units in before this batch, units, unit value) — FIFO
        self.starts = []         # units in before each batch, to find a batch by bisection
        self.whole = [0.0]       # the cost of the first k whole batches, summed in the same order as one by one
        self.qin = self.qout = 0.0
        self.vin = self.vout = 0   # minor units
        self.qty, self.avg = 0.0, 0.0
        self.last_unit = 0.0

    def first_cost(self, x):
        """C(x): the cost of the first x units that came in (minor units, not rounded)."""
        k = bisect.bisect_left(self.starts, x)      # batches 0 … k-1 begin before x
        c = self.whole[k - 1] if k else 0.0         # the whole batches before the last one touched …
        if k:
            before, q, unit = self.batches[k - 1]
            c += min(q, x - before) * unit          # … and as much of that one as x reaches
        if x > self.qin + EPS:               # more out than ever came in (an older, already short history)
            c += (x - self.qin) * self.last_unit
        return c

    def take_in(self, q, value, unit_avg):
        unit = value / q if q > EPS else 0.0
        self.batches.append((self.qin, q, unit))
        self.starts.append(self.qin)
        self.whole.append(self.whole[-1] + q * unit)
        self.qin += q
        self.vin += value
        self.last_unit = unit
        new = self.qty + q
        self.avg = ((max(self.qty, 0) * self.avg) + q * unit_avg) / max(new, EPS) if new > 0 else unit_avg
        self.qty = new

    def take_out(self, q, value):
        self.qout += q
        self.vout += value
        self.qty -= q

    def on_hand_unit(self):
        """What a unit on hand is worth: the batches still in, at their unit values (the workbook computes the same)."""
        left = self.qin - self.qout
        return (self.vin - self.first_cost(self.qout)) / left if left > EPS else self.last_unit


def compute(db, item_id, how=None):
    """Replay the item's movements: the value of each (minor units), the unit cost behind it, every movement that
    leaves its store short, and what each store holds at the end."""
    how = how or method(db)
    stores, values, units, short = {}, {}, {}, []
    rows = moves(db, item_id)
    for m in rows:
        s = stores.setdefault(m["warehouse_id"], _Store())
        q = abs(m["qty"])
        if m["qty"] > 0:
            if m["kind"] == "Transfer" and m["link_id"] in values:
                v, unit_avg = values[m["link_id"]], units[m["link_id"]]
            elif m["fixed"]:
                v = int(m["value"] if m["value"] is not None else half_up(q * (m["rate"] or 0)))
                unit_avg = m["rate"] or (v / q if q > EPS else 0.0)
            else:                             # a count gain at the cost of what is on hand
                unit_avg = s.avg if how == "average" else s.on_hand_unit()
                v = half_up(q * unit_avg)
            s.take_in(q, v, unit_avg)
            values[m["id"]], units[m["id"]] = v, unit_avg if how == "average" else (v / q if q > EPS else 0.0)
        else:
            if q > s.qty + EPS:
                short.append((m, max(s.qty, 0.0)))
            if how == "average":
                v, unit = half_up(q * s.avg), s.avg
            else:
                v = half_up(s.first_cost(s.qout + q) - s.first_cost(s.qout))
                unit = v / q if q > EPS else 0.0
            s.take_out(q, v)
            values[m["id"]], units[m["id"]] = v, unit
    levels = {}
    for wid, s in stores.items():
        qty = round(s.qty, 6)
        val = s.vin - s.vout        # what came in less what went out, as the entries carry it (to the cent)
        levels[wid] = (qty, val, (val / qty) if qty > EPS else (s.avg if how == "average" else s.last_unit))
    return {"rows": rows, "values": values, "units": units, "short": short, "levels": levels}


def save_levels(db, item_id, levels):
    have = {r["warehouse_id"] for r in db.q("SELECT warehouse_id FROM stock_levels WHERE item_id=?", (item_id,))}
    for wid in have - set(levels):
        db.conn.execute("UPDATE stock_levels SET qty=0, value=0 WHERE item_id=? AND warehouse_id=?", (item_id, wid))
    for wid, (qty, val, unit) in levels.items():
        db.conn.execute("INSERT OR REPLACE INTO stock_levels(item_id,warehouse_id,qty,avg_cost,value) VALUES (?,?,?,?,?)",
                        (item_id, wid, qty, unit, val))


def refresh(db, item_id):
    """What each store holds of the item, from its movements (no entries change)."""
    save_levels(db, item_id, compute(db, item_id)["levels"])


def first_open_date(db, date):
    lock = (db.setting("lock_date") or "").strip()
    if lock and date <= lock:
        return (_dt.date.fromisoformat(lock[:10]) + _dt.timedelta(days=1)).isoformat()
    return date


def _accounts(db, m):
    """(debit, credit) accounts of a movement's cost, as its own entry booked it."""
    from . import accounting as acc
    wh = db.one("SELECT * FROM warehouses WHERE id=?", (m["warehouse_id"],))
    inv = acc._wh_account(db, wh)
    if m["kind"] == "Issue":
        dr = db.one("SELECT account FROM journal_lines WHERE entry_id=? AND debit>0 ORDER BY id LIMIT 1", (m["entry_id"],)) if m["entry_id"] else None
        return (dr["account"] if dr else db.acct("materials")), inv
    if m["kind"] == "Transfer":
        arrival = db.one("SELECT warehouse_id FROM stock_moves WHERE link_id=?", (m["id"],))
        to = db.one("SELECT * FROM warehouses WHERE id=?", (arrival["warehouse_id"],)) if arrival else None
        return (acc._wh_account(db, to) if to else inv), inv
    if m["kind"] == "Adjustment":
        return (db.acct("stock_adjust"), inv) if m["qty"] < 0 else (inv, db.acct("stock_adjust"))
    return None, None


def _post_adjustment(db, m, delta, why, user, how):
    from . import accounting as acc
    dr, cr = _accounts(db, m)
    if not dr or not cr or dr == cr or not delta:
        return None
    if delta < 0:
        dr, cr = cr, dr
    item = db.one("SELECT name FROM stock_items WHERE id=?", (m["item_id"],))["name"]
    bu = m["bu_id"] or ((db.one("SELECT bu_id FROM journal_entries WHERE id=?", (m["entry_id"],)) or {}).get("bu_id") if m["entry_id"] else None)
    label = {"Issue": "issue", "Transfer": "transfer", "Adjustment": "count adjustment"}.get(m["kind"], m["kind"].lower())
    amt = to_major(abs(delta))
    eid = acc._insert_entry(db, date=first_open_date(db, m["date"]), kind="Stock Cost Adjustment", bu_id=bu, user=user,
                            memo=f"{item}: {label} of {m['date']} re-costed ({'FIFO' if how == 'fifo' else 'moving average'}) — {why}",
                            lines=[{"account": dr, "debit": amt}, {"account": cr, "credit": amt}],
                            meta={"stock_move": m["id"], "of_entry": m["entry_id"], "delta": to_major(delta)})
    db.conn.execute("INSERT INTO stock_recosts(entry_id, move_id, origin_entry, delta) VALUES (?,?,?,?)",
                    (eid, m["id"], m["entry_id"], delta))
    return eid


def short_message(db, short, new_ids=()):
    m, avail = short
    it = db.one("SELECT name, unit FROM stock_items WHERE id=?", (m["item_id"],))
    wh = db.one("SELECT name FROM warehouses WHERE id=?", (m["warehouse_id"],))["name"]
    if m["id"] in new_ids:
        return f"Only {avail:g} {it['unit']} of {it['name']} in {wh} on {m['date']}"
    return (f"{it['name']} in {wh} would run short on {m['date']}: {abs(m['qty']):g} {it['unit']} go out with only {avail:g} "
            f"left. Undo or move the later movements first, or correct the quantity with a count.")


def apply(db, item_id, after=None, why="", user="", new_ids=(), check=True, computed=None):
    """Re-cost the item after a change at position `after` (an order_key; None = every movement): refuse stock that
    runs short from there on, bring each later movement's value up to date and post the difference of those already
    in the books. Caller holds a transaction. Returns the adjustment entries posted. computed: compute()'s answer
    when the caller has just worked it out and only booked the new movement since (nothing it depends on changed)."""
    from .accounting import ValidationError
    how = method(db)
    r = computed or compute(db, item_id, how)
    for sh in r["short"] if check else ():
        if after is None or order_key(sh[0]) >= after:
            raise ValidationError(short_message(db, sh, new_ids))
    posted = []
    for m in r["rows"]:
        if after is not None and order_key(m) < after:
            continue
        v, unit = r["values"][m["id"]], r["units"][m["id"]]
        if m["fixed"]:
            continue
        if v != m["value"] or abs((m["rate"] or 0) - unit) > 1e-9:
            db.conn.execute("UPDATE stock_moves SET value=?, rate=? WHERE id=?", (v, unit, m["id"]))
        if m["booked"] is None or v == m["booked"]:
            continue
        if not (m["kind"] == "Transfer" and m["qty"] > 0):      # an arrival's value goes with its departure's entry
            eid = _post_adjustment(db, m, v - m["booked"], why, user, how)
            if eid:
                posted.append(eid)
        db.conn.execute("UPDATE stock_moves SET booked=? WHERE id=?", (v, m["id"]))
    save_levels(db, item_id, r["levels"])
    return posted


def recost_all(db, how=None, user="", preview=False):
    """Every item re-costed from its first movement (switching the method, or bringing older books in line). With
    preview: nothing is changed; returns what would be adjusted."""
    from .accounting import ValidationError
    how = how or method(db)
    if how not in METHODS:
        raise ValidationError("Choose FIFO or moving average")
    out = {"method": how, "items": [], "total": 0.0, "short": []}
    items = db.q("SELECT DISTINCT m.item_id, i.name FROM stock_moves m JOIN stock_items i ON i.id=m.item_id ORDER BY i.name")
    for it in items:
        r = compute(db, it["item_id"], how)
        diff = sum(r["values"][m["id"]] - m["booked"] for m in r["rows"]
                   if m["booked"] is not None and not m["fixed"] and not (m["kind"] == "Transfer" and m["qty"] > 0))
        if diff:
            out["items"].append({"item": it["name"], "change": to_major(diff)})
            out["total"] += to_major(diff)
        out["short"] += [short_message(db, sh) for sh in r["short"]]
    out["total"] = round(out["total"], 2)
    if preview:
        return out
    with db.tx():
        db.set_setting("stock_costing", how)
        for it in items:     # a history that was already short is reported, not refused: nothing in it changes here
            apply(db, it["item_id"], None, why="costing method " + ("FIFO" if how == "fifo" else "moving average"),
                  user=user, check=False)
    return out
