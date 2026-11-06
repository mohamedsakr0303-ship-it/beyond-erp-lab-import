"""Stock planning: minimum quantities (per item, per warehouse, automatic) with their alert choice — notification, on
screen, both or none — and the forecast of what each warehouse receives and sends out and what each project needs, from
history. Also the stock corrections that must keep stock and the inventory accounts agreeing, and the demo books."""
import datetime as _dt
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from erp import accounting as acc, auth, demo, forecast, interchange, server, stockplan  # noqa: E402
from erp.db import Database  # noqa: E402

try:
    import openpyxl  # noqa: F401
    HAVE_XL = True
except ImportError:
    HAVE_XL = False


def month_day(back, day):
    """A date `back` whole months before this month, on `day` (so history months are full months)."""
    t = _dt.date.fromisoformat(acc.today())
    y, m = t.year, t.month - back
    while m < 1:
        m += 12
        y -= 1
    return _dt.date(y, m, day).isoformat()


def books():
    db = Database(":memory:")
    db.x("INSERT INTO warehouses(name) VALUES ('Site')")
    db.x("INSERT INTO business_units(code,name) VALUES ('P1','Villa'), ('P2','Office')")
    return db


def receive(db, wh, item, qty, rate, date):
    return acc.stock_move(db, {"type": "Receipt", "warehouse": wh, "item": item, "unit": "bag", "qty": qty, "rate": rate, "paid": qty * rate, "date": date})


def issue(db, wh, item, qty, bu, date):
    return acc.stock_move(db, {"type": "Issue", "warehouse": wh, "item": item, "qty": qty, "bu_code": bu, "date": date})


def item_id(db, name):
    return db.one("SELECT id FROM stock_items WHERE name=?", (name,))["id"]


def wh_id(db, name):
    return db.one("SELECT id FROM warehouses WHERE name=?", (name,))["id"]


class Methods(unittest.TestCase):
    def test_each_kind_of_history_gets_a_method_that_suits_it(self):
        self.assertEqual(stockplan.project([], 2), ([0.0, 0.0], "none", 0))
        self.assertEqual(stockplan.project([0, 0, 12, 18], 1), ([15.0], "average", 2))          # too little history
        self.assertEqual(stockplan.project([80, 0, 0, 0], 1)[1:], ("average", 4))               # one movement, spread out
        fc, meth, _ = stockplan.project([0, 0, 10, 0, 12, 0, 0, 9, 0, 11], 3)
        self.assertEqual(meth, "croston")                                                        # occasional use
        self.assertTrue(4 < fc[0] < 8 and fc[0] == fc[2])
        up, meth, _ = stockplan.project([50, 55, 60, 58, 62, 70], 3)
        self.assertEqual(meth, "holt")
        self.assertTrue(66 < up[0] < up[1] < up[2] < 75)                                        # rising, damped
        down = stockplan.project([100, 90, 80, 70, 60, 50], 3)[0]
        self.assertTrue(down[0] > down[1] > down[2] >= 0)


class Minimums(unittest.TestCase):
    def test_a_warehouse_minimum_wins_and_each_alert_choice_is_kept(self):
        db = books()
        receive(db, "Main Store", "Cement", 100, 10, month_day(1, 3))
        receive(db, "Site", "Cement", 20, 10, month_day(1, 3))
        cid = item_id(db, "Cement")
        db.x("UPDATE stock_items SET reorder_level=50 WHERE id=?", (cid,))
        stockplan.save_settings(db, cid, {"warehouses": {wh_id(db, "Site"): 25}, "alert_mode": "both"})
        lv = {r["warehouse"]: r for r in stockplan.levels(db)}
        self.assertEqual((lv["Main Store"]["min"], lv["Main Store"]["source"], lv["Main Store"]["below"]), (50, "item", False))
        self.assertEqual((lv["Site"]["min"], lv["Site"]["source"], lv["Site"]["below"], lv["Site"]["short"]), (25, "warehouse", True, 5))
        self.assertTrue(any(r["low"] for r in acc.stock_report(db) if r["warehouse"] == "Site"))   # the Stock page and reports agree
        self.assertEqual(len(stockplan.bell_rows(db)), 1)
        for mode, bell in (("visual", 0), ("none", 0), ("notify", 1), ("both", 1)):
            stockplan.save_settings(db, cid, {"alert_mode": mode})
            self.assertEqual(len(stockplan.bell_rows(db)), bell, mode)
            self.assertEqual(acc.stock_report(db)[1]["alert_mode"], mode)
        with self.assertRaises(acc.ValidationError):
            stockplan.save_settings(db, cid, {"alert_mode": "loud"})
        with self.assertRaises(acc.ValidationError):
            stockplan.save_settings(db, cid, {"warehouses": {wh_id(db, "Site"): -1}})
        stockplan.save_settings(db, cid, {"warehouses": {wh_id(db, "Site"): ""}})               # blank = the item's minimum again
        self.assertEqual({r["warehouse"]: r["min"] for r in stockplan.levels(db)}["Site"], 50)

    def test_a_warehouse_minimum_applies_even_before_anything_is_there(self):
        db = books()
        receive(db, "Main Store", "Cement", 10, 10, month_day(1, 3))
        stockplan.save_settings(db, item_id(db, "Cement"), {"warehouses": {wh_id(db, "Site"): 5}})
        r = [x for x in stockplan.levels(db) if x["warehouse"] == "Site"][0]
        self.assertEqual((r["qty"], r["below"]), (0, True))

    def test_a_phone_push_goes_once_per_fall_and_again_after_a_refill(self):
        db = books()
        auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        clerk = auth.create_user(db, {"username": "clerk", "password": "pw123456"})
        db.x("UPDATE users SET perms=? WHERE id=?", (json.dumps({"sales.invoices": "view"}), clerk))   # cannot see stock
        receive(db, "Main Store", "Cement", 30, 10, month_day(1, 3))
        db.x("UPDATE stock_items SET reorder_level=20")
        self.assertEqual(stockplan.pushes(db), [])
        issue(db, "Main Store", "Cement", 15, "P1", month_day(0, 1))
        p = stockplan.pushes(db)
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]["to"], ["boss"])                                                    # only those who see stock
        self.assertIn("Cement", p[0]["body"])
        self.assertEqual(stockplan.pushes(db), [])                                               # once per fall
        receive(db, "Main Store", "Cement", 30, 10, month_day(0, 1))
        self.assertEqual(stockplan.pushes(db), [])                                               # back above: forgotten
        issue(db, "Main Store", "Cement", 30, "P1", month_day(0, 1))
        self.assertEqual(len(stockplan.pushes(db)), 1)                                           # falls again: announced again
        stockplan.save_settings(db, item_id(db, "Cement"), {"alert_mode": "visual"})
        receive(db, "Main Store", "Cement", 30, 10, month_day(0, 1))
        stockplan.pushes(db)
        issue(db, "Main Store", "Cement", 30, "P1", month_day(0, 1))
        self.assertEqual(stockplan.pushes(db), [])                                               # on screen only: no push


class Forecast(unittest.TestCase):
    def history(self):
        db = books()
        for k, (rin, out) in enumerate(((150, 40), (0, 45), (80, 50), (0, 55), (90, 60))):
            back = 5 - k
            if rin:
                receive(db, "Main Store", "Cement", rin, 10 + k, month_day(back, 2))
            issue(db, "Main Store", "Cement", out, "P1" if k % 2 else "P2", month_day(back, 15))
        return db

    def test_in_and_out_per_warehouse_from_history(self):
        db = self.history()
        cid = item_id(db, "Cement")
        acc.stock_move(db, {"type": "Adjustment", "warehouse": "Main Store", "item": "Cement", "counted": 0, "date": month_day(1, 20)})
        f = stockplan.forecast(db, back=6, horizon=3)
        r = [x for x in f["rows"] if x["item_id"] == cid][0]
        self.assertEqual(r["hist_out"][-5:], [40, 45, 50, 55, 60])                                # count corrections are not use
        self.assertEqual(r["hist_in"][-5:], [150, 0, 80, 0, 90])
        self.assertEqual(r["method_out"], "holt")
        self.assertTrue(r["fc_out"][0] > 55)
        self.assertEqual(r["qty"], 0)
        self.assertEqual(r["cover_days"], 0)
        self.assertIsNone(r["suggested_min"])                                                     # no delivery time, no guess
        self.assertEqual(f["ahead"][0], acc.today()[:7])
        stockplan.save_settings(db, cid, {"lead_days": 15})
        r = [x for x in stockplan.forecast(db, back=6)["rows"] if x["item_id"] == cid][0]
        self.assertTrue(r["fc_out"][0] / 2 < r["suggested_min"] < r["fc_out"][0] * 1.5)            # half a month of use + safety

    def test_automatic_minimum_follows_the_suggestion(self):
        db = self.history()
        cid = item_id(db, "Cement")
        receive(db, "Main Store", "Cement", 200, 15, month_day(0, 1))
        db.x("UPDATE stock_items SET reorder_level=5")
        stockplan.save_settings(db, cid, {"lead_days": 30, "auto_min": True})
        r = stockplan.levels(db)[0]
        sug = [x for x in stockplan.forecast(db)["rows"] if x["item_id"] == cid][0]["suggested_min"]
        self.assertEqual((r["source"], r["min"]), ("auto", sug))
        stockplan.save_settings(db, cid, {"auto_min": False})
        self.assertEqual(stockplan.levels(db)[0]["min"], 5)
        stockplan.save_settings(db, cid, {"lead_days": 30})
        self.assertEqual(stockplan.apply_suggestions(db), 1)                                     # or set once, by hand
        self.assertEqual(stockplan.levels(db)[0]["source"], "warehouse")

    def test_projects_receive_from_stores_and_bought_straight(self):
        db = self.history()
        p1 = db.one("SELECT id FROM business_units WHERE code='P1'")["id"]
        acc.record_journal(db, {"date": month_day(2, 10), "memo": "Cement bought straight for the site", "bu_id": p1,
                                "lines": [{"account": "1020", "credit": 500}], "items": [{"item": "Cement", "qty": 20, "rate": 25, "bu_id": p1}]})
        f = stockplan.project_forecast(db, back=6, horizon=2)
        by = {r["bu_code"]: r for r in f["rows"]}
        self.assertEqual(by["P1"]["hist"][-5:][1], 45)
        self.assertEqual(by["P1"]["hist"][-5:][3], 55 + 20)                                      # issued + bought straight
        self.assertEqual(by["P2"]["hist"][-5:][0], 40)
        self.assertEqual(len(by["P1"]["fc"]), 2)
        self.assertTrue(by["P2"]["fc_value"] > 0)
        self.assertEqual({p["bu_code"] for p in f["projects"]}, {"P1", "P2"})


class StockAndBooksAgree(unittest.TestCase):
    def check(self, db):
        c = [x for x in acc.audit(db) if x["check"].startswith("Stock register")][0]
        self.assertTrue(c["ok"], c)

    def cost(self, db, eid):
        return round(sum(ln["debit"] for ln in acc.get_entry(db, eid)["lines"]), 2)

    def recosts(self, db):
        return [(r["date"], r["amount"]) for r in db.q(
            "SELECT e.date, SUM(l.debit)/100.0 amount FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id "
            "WHERE e.kind='Stock Cost Adjustment' AND e.status='posted' AND e.reversed_by IS NULL GROUP BY e.id ORDER BY e.id")]

    def test_fifo_takes_the_oldest_batches_first(self):
        db = books()
        self.assertEqual(db.setting("stock_costing"), "fifo")                   # new books
        receive(db, "Main Store", "Cement", 100, 10, month_day(2, 1))
        receive(db, "Main Store", "Cement", 50, 13, month_day(2, 2))
        i1 = issue(db, "Main Store", "Cement", 120, "P1", month_day(2, 3))     # 100 × 10 + 20 × 13
        self.assertEqual(self.cost(db, i1), 1260.0)
        i2 = issue(db, "Main Store", "Cement", 30, "P1", month_day(2, 4))      # the last 30 × 13
        self.assertEqual(self.cost(db, i2), 390.0)
        self.assertEqual(db.one("SELECT qty, value FROM stock_levels"), {"qty": 0, "value": 0})
        self.check(db)

    def test_each_warehouse_keeps_its_own_batches_and_a_transfer_carries_its_cost(self):
        db = books()
        receive(db, "Main Store", "Cement", 10, 10, month_day(2, 1))
        receive(db, "Main Store", "Cement", 10, 16, month_day(2, 2))
        receive(db, "Site", "Cement", 10, 50, month_day(2, 2))
        acc.stock_move(db, {"type": "Transfer", "warehouse": "Main Store", "to_warehouse": "Site", "item": "Cement", "qty": 15,
                            "date": month_day(2, 3)})                          # 10 × 10 + 5 × 16 = 180 leaves the main store
        lv = {r["warehouse_id"]: r for r in db.q("SELECT * FROM stock_levels")}
        self.assertEqual(lv[wh_id(db, "Main Store")]["value"], 8000)           # 5 × 16
        self.assertEqual(lv[wh_id(db, "Site")]["value"], 68000)                # 500 + 180
        i = issue(db, "Site", "Cement", 12, "P1", month_day(2, 4))             # the site's own 10 × 50 first, then 2 at 12 (180 / 15)
        self.assertEqual(self.cost(db, i), 524.0)
        self.check(db)

    def test_a_backdated_receipt_recosts_the_later_issues_with_adjustment_entries(self):
        db = books()
        receive(db, "Main Store", "Cement", 100, 20, month_day(2, 10))
        i1 = issue(db, "Main Store", "Cement", 60, "P1", month_day(2, 12))     # 60 × 20 = 1,200
        self.assertEqual(self.cost(db, i1), 1200.0)
        receive(db, "Main Store", "Cement", 50, 10, month_day(2, 5))           # earlier and cheaper: FIFO takes it first
        # the issue now costs 50 × 10 + 10 × 20 = 700: its entry stays, an adjustment of −500 is dated on the issue's day
        self.assertEqual(self.cost(db, i1), 1200.0)
        self.assertEqual(self.recosts(db), [(month_day(2, 12), 500.0)])
        cost_p1 = db.one("SELECT SUM(l.debit-l.credit) v FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                         "WHERE e.status='posted' AND e.bu_id=(SELECT id FROM business_units WHERE code='P1') AND l.account=?",
                         (db.acct("materials"),))["v"]
        self.assertEqual(cost_p1, 70000)                                        # the project's material cost is now 700
        self.assertEqual(db.one("SELECT qty, value FROM stock_levels"), {"qty": 90, "value": 180000})   # 90 left × 20
        self.check(db)

    def test_an_issue_entered_with_an_earlier_date_keeps_stock_and_books_agreeing(self):
        db = books()
        receive(db, "Main Store", "Cement", 100, 10, month_day(2, 1))
        issue(db, "Main Store", "Cement", 30, "P1", month_day(1, 1))
        receive(db, "Main Store", "Cement", 100, 20, month_day(1, 5))
        issue(db, "Main Store", "Cement", 50, "P1", month_day(2, 10))          # backdated, before the second receipt
        self.check(db)
        with self.assertRaisesRegex(acc.ValidationError, "Only 50 bag of Cement in Main Store on"):
            issue(db, "Main Store", "Cement", 51, "P1", month_day(2, 11))      # only 50 were there that day
        with self.assertRaisesRegex(acc.ValidationError, "would run short on " + month_day(1, 1)):
            issue(db, "Main Store", "Cement", 21, "P2", month_day(2, 11))      # fine that day, but then the issue of month 1 is short
        self.check(db)

    def test_a_receipt_drawn_on_can_go_when_stock_is_left_and_the_issues_are_recosted(self):
        db = books()
        r1 = receive(db, "Main Store", "Cement", 100, 10, month_day(2, 1))
        r2 = receive(db, "Main Store", "Cement", 100, 20, month_day(2, 2))
        i1 = issue(db, "Main Store", "Cement", 80, "P1", month_day(2, 3))      # 80 × 10
        self.assertEqual(self.cost(db, i1), 800.0)
        acc.void_entry(db, r1, "entered twice")                                # the issue now draws on the second: 80 × 20
        self.assertEqual(self.recosts(db), [(month_day(2, 3), 800.0)])
        self.check(db)
        for undo in (lambda: acc.void_entry(db, r2, "wrong"), lambda: acc.reverse_entry(db, r2)):
            with self.assertRaisesRegex(acc.ValidationError, "would run short"):
                undo()                                                          # nothing would be left for the issue
        adj = db.one("SELECT entry_id FROM stock_recosts")["entry_id"]
        with self.assertRaisesRegex(acc.ValidationError, "belongs to a stock movement"):
            acc.void_entry(db, adj, "by itself")
        acc.void_entry(db, i1, "undo the issue")                               # its adjustment goes with it
        self.assertEqual(self.recosts(db), [])
        acc.void_entry(db, r2, "now it can go")
        self.check(db)
        self.assertEqual(db.one("SELECT qty FROM stock_levels")["qty"], 0)

    def test_reversing_an_issue_reverses_its_cost_adjustments_too(self):
        db = books()
        receive(db, "Main Store", "Cement", 10, 20, month_day(2, 10))
        i1 = issue(db, "Main Store", "Cement", 10, "P1", month_day(2, 12))
        receive(db, "Main Store", "Cement", 10, 5, month_day(2, 5))            # −150 on the issue
        self.assertEqual(self.recosts(db), [(month_day(2, 12), 150.0)])
        acc.reverse_entry(db, i1)
        self.assertEqual(self.recosts(db), [])
        self.check(db)

    def test_moving_average_books_and_switching_them_to_fifo(self):
        from erp import stockcost
        db = books()
        db.set_setting("stock_costing", "average")
        receive(db, "Main Store", "Cement", 100, 10, month_day(2, 1))
        receive(db, "Main Store", "Cement", 100, 20, month_day(2, 2))
        i1 = issue(db, "Main Store", "Cement", 50, "P1", month_day(2, 3))      # at the average, 15
        self.assertEqual(self.cost(db, i1), 750.0)
        prev = stockcost.recost_all(db, "fifo", preview=True)
        self.assertEqual((prev["total"], db.setting("stock_costing")), (-250.0, "average"))   # 50 × 10 = 500 under FIFO
        stockcost.recost_all(db, "fifo", user="admin")
        self.assertEqual(db.setting("stock_costing"), "fifo")
        self.assertEqual(self.recosts(db), [(month_day(2, 3), 250.0)])
        self.check(db)

    def test_every_demo_ties_out_including_the_asset_register(self):
        from erp import industries
        for ind in (None, "contracting", "sales", "scaffolding", "healthcare", "investment"):
            db = Database(":memory:")
            if ind:
                industries.apply(db, ind)                    # as the app does: the business type first, then its demo
            demo.load(db, "admin", industry=ind)
            bad = [c for c in acc.audit(db) if not c["ok"]]
            self.assertEqual(bad, [], ind)
            self.assertFalse([f for f in acc.review_flags(db) if "asset register" in f["flag"]], ind)   # purchases booked

    def test_an_asset_whose_purchase_was_never_booked_is_flagged(self):
        db = books()
        from erp import assets
        assets.save(db, {"code": "FA-1", "name": "Truck", "account": "1420", "acquired": month_day(3, 1), "cost": 100000,
                         "life_months": 60, "pay": "none"})
        self.assertTrue([f for f in acc.review_flags(db) if "asset register" in f["flag"]])
        self.assertTrue(all(c["ok"] for c in acc.audit(db)))                    # not a broken entry: a figure to review


class Demo(unittest.TestCase):
    def test_the_demo_shows_every_alert_choice(self):
        db = Database(":memory:")
        demo.load(db, "admin")
        low = {(r["name"], r["alert_mode"]) for r in stockplan.levels(db) if r["below"]}
        self.assertEqual({m for _, m in low}, {"both", "visual", "notify", "none"})
        self.assertEqual({r["kind"] for r in stockplan.bell_rows(db)}, {"stock_low"})
        self.assertEqual(len(stockplan.bell_rows(db)), sum(1 for _, m in low if m in ("both", "notify")))
        f = stockplan.forecast(db)
        self.assertTrue(any(r["method_out"] == "holt" for r in f["rows"]))
        self.assertTrue(any(r["suggested_min"] for r in f["rows"]))
        self.assertTrue(stockplan.project_forecast(db)["projects"])

    def test_own_price_trend_from_what_was_paid(self):
        db = books()
        for back, rate in ((7, 100), (4, 104), (1, 108.16)):
            receive(db, "Main Store", "Cement", 10, rate, month_day(back, 2))
        t = forecast.own_price_trend(db)
        self.assertEqual(t["items"], 1)
        self.assertAlmostEqual(t["rate"], 0.04, places=3)                                       # 100 → 108.16 over two quarters
        self.assertIsNone(forecast.own_price_trend(books()))


class Api(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        auth.create_user(self.db, {"username": "boss", "password": "pw123456"}, first=True)
        self.clerk = auth.create_user(self.db, {"username": "clerk", "password": "pw123456"})
        self.tok, _ = auth.login(self.db, "boss", "pw123456")
        self.tok_clerk, _ = auth.login(self.db, "clerk", "pw123456")
        app = type("App", (), {})()
        app.db, app.backup = self.db, (lambda *a, **k: None)
        self.app = app

    def call(self, m, p, b=None, tok=None):
        r = server.handle(self.app, m, p, {"x-session": tok or self.tok, "x-requested-with": "BeyondERP", "content-type": "application/json"},
                          json.dumps(b or {}).encode())
        return r.status, json.loads(r.body or b"{}")

    def test_settings_bell_forecast_and_permissions(self):
        receive(self.db, "Main Store", "Cement", 30, 10, month_day(1, 3))
        iid = item_id(self.db, "Cement")
        st, r = self.call("PUT", f"/api/items/{iid}/stock-settings", {"alert_mode": "notify", "lead_days": 7, "auto_min": False,
                                                                       "warehouses": {"1": 40}})
        self.assertEqual(st, 200, r)
        st, r = self.call("GET", f"/api/items/{iid}/stock-settings")
        self.assertEqual((r["alert_mode"], r["lead_days"], r["warehouses"][0]["min_qty"]), ("notify", 7, 40))
        st, r = self.call("GET", "/api/alerts")
        self.assertTrue(any(x["kind"] == "stock_low" for x in r["rows"]), r["rows"])
        st, r = self.call("GET", "/api/stock")
        self.assertTrue(r["levels"][0]["low"])
        self.assertEqual(r["levels"][0]["min"], 40)
        st, r = self.call("GET", "/api/stock-plan")
        self.assertEqual(st, 200)
        self.assertEqual(r["levels"][0]["source"], "warehouse")
        st, r = self.call("GET", "/api/stock-plan/projects")
        self.assertEqual(st, 200)
        st, r = self.call("POST", "/api/automations/run", {"all": 1})
        self.assertEqual(st, 200)
        self.assertTrue(r.get("_push"), r)                                                       # the phone push, once
        st, r = self.call("POST", "/api/automations/run", {"all": 1})
        self.assertFalse(r.get("_push"))
        # someone who cannot see stock sees none of it, and someone who can only look cannot change it
        self.assertEqual(self.call("GET", "/api/stock-plan", tok=self.tok_clerk)[0], 403)
        self.assertFalse(any(x.get("kind") == "stock_low" for x in self.call("GET", "/api/alerts", tok=self.tok_clerk)[1].get("rows", [])))
        self.db.x("UPDATE users SET perms=? WHERE id=?", (json.dumps({"stock.balances": "view"}), self.clerk))
        self.assertEqual(self.call("GET", "/api/stock-plan", tok=self.tok_clerk)[0], 200)
        self.assertEqual(self.call("PUT", f"/api/items/{iid}/stock-settings", {"alert_mode": "none"}, tok=self.tok_clerk)[0], 403)
        self.assertEqual(self.call("POST", "/api/stock-plan/apply", {}, tok=self.tok_clerk)[0], 403)

    def test_forecast_automatic_mode_and_hidden_parts_keep_their_edits(self):
        st, r = self.call("PUT", "/api/forecast", {"auto": {"on": True, "every_days": 30}, "overrides": {"acct:6010": {"0": 999}}})
        self.assertEqual(st, 200, r)
        self.assertTrue(r["auto"]["on"] and r["auto"]["due"])                                    # on, never refreshed
        st, r = self.call("PUT", "/api/forecast", {"auto": {**r["auto"], "last": acc.today(), "source": "own"}})
        self.assertFalse(r["auto"]["due"])
        a = forecast.assumptions(self.db)
        a["auto"]["last"] = (_dt.date.fromisoformat(acc.today()) - _dt.timedelta(days=31)).isoformat()
        self.assertTrue(forecast.auto_due(a))
        self.db.x("UPDATE users SET perms=? WHERE id=?", (json.dumps({"reports.forecast": "edit", "reports.forecast.prices": "edit", "reports.forecast.expenses": "none"}), self.clerk))
        st, r = self.call("PUT", "/api/forecast", {"overrides": {}}, tok=self.tok_clerk)
        self.assertEqual(st, 200, r)
        self.assertEqual(forecast.assumptions(self.db)["overrides"].get("acct:6010"), {"0": 999.0})   # not this person's part: kept


@unittest.skipUnless(HAVE_XL, "openpyxl not installed")
class Workbook(unittest.TestCase):
    def test_minimum_settings_travel_in_the_workbook(self):
        db = books()
        receive(db, "Main Store", "Cement", 30, 10, month_day(1, 3))
        stockplan.save_settings(db, item_id(db, "Cement"), {"alert_mode": "visual", "lead_days": 9, "auto_min": True,
                                                            "warehouses": {wh_id(db, "Site"): 12}})
        row = [r for r in interchange.export_data(db)["Stock Items"] if r["name"] == "Cement"][0]
        self.assertEqual((row["alert_mode"], row["lead_days"], row["auto_min"], row["wh_mins"]), ("visual", 9, "Yes", "Site: 12"))
        db2 = books()
        interchange._imp_item(db2, {**row, "reorder_level": 3, "std_cost": 0, "price": 0})
        s = stockplan.item_settings(db2, item_id(db2, "Cement"))
        self.assertEqual((s["alert_mode"], s["lead_days"], s["auto_min"], s["warehouses"][0]["min_qty"]), ("visual", 9, 1, 12))
        interchange._imp_item(db2, {"name": "Cement", "unit": "bag", "reorder_level": 3})       # an older file: settings kept
        self.assertEqual(stockplan.item_settings(db2, item_id(db2, "Cement"))["alert_mode"], "visual")


if __name__ == "__main__":
    unittest.main()


class FifoWorkbook(unittest.TestCase):
    """The Excel workbook costs stock the way the app does: every Stock Moves row's value and the trial balance,
    recalculated by LibreOffice, with prices that change, transfers, counts and a movement entered with an earlier date."""

    def books(self, how="fifo"):
        db = books()
        db.set_setting("stock_costing", how)
        receive(db, "Main Store", "Cement", 100, 10, month_day(3, 1))
        receive(db, "Main Store", "Cement", 60, 12.5, month_day(3, 4))
        receive(db, "Site", "Cement", 40, 14, month_day(3, 5))
        issue(db, "Main Store", "Cement", 130, "P1", month_day(3, 8))                  # 100 × 10 + 30 × 12.5
        acc.stock_move(db, {"type": "Transfer", "warehouse": "Main Store", "to_warehouse": "Site", "item": "Cement", "qty": 20,
                            "date": month_day(3, 9)})
        issue(db, "Site", "Cement", 50, "P2", month_day(3, 10))                         # 40 × 14 + 10 from the transfer
        acc.stock_move(db, {"type": "Adjustment", "warehouse": "Site", "item": "Cement", "counted": 7, "date": month_day(3, 11)})
        receive(db, "Main Store", "Cement", 80, 11.3, month_day(2, 2))
        acc.stock_move(db, {"type": "Adjustment", "warehouse": "Main Store", "item": "Cement", "counted": 95, "date": month_day(2, 3)})
        acc.stock_move(db, {"type": "Adjustment", "warehouse": "Main Store", "item": "Cement", "counted": 97, "rate": 9.75,
                            "date": month_day(2, 4)})
        receive(db, "Main Store", "Tiles", 30, 101.07, month_day(2, 5))
        r = receive(db, "Main Store", "Tiles", 30, 97.33, month_day(2, 6))
        issue(db, "Main Store", "Tiles", 25, "P1", month_day(2, 9))
        receive(db, "Main Store", "Tiles", 10, 80, month_day(2, 1))                     # entered later, dated earlier
        issue(db, "Main Store", "Cement", 33, "P2", month_day(1, 3))
        issue(db, "Main Store", "Tiles", 7, "P2", month_day(1, 4))
        acc.void_entry(db, r, "wrong supplier")                                         # its tiles were partly drawn on
        return db

    def test_the_app_ties_out(self):
        db = self.books()
        c = [x for x in acc.audit(db) if x["check"].startswith("Stock register")][0]
        self.assertTrue(c["ok"], c)
        self.assertTrue(db.q("SELECT 1 FROM journal_entries WHERE kind='Stock Cost Adjustment' AND status='posted'"))

    @unittest.skipUnless(HAVE_XL and __import__("shutil").which("soffice"), "LibreOffice not installed")
    def test_the_workbook_recalculated_agrees_row_by_row(self):
        import subprocess
        import tempfile
        import openpyxl
        from erp import stockcost, workbook
        for how in ("fifo", "average"):
            db = self.books(how)
            app_values = interchange.export_data(db)["Stock Moves"]
            with tempfile.TemporaryDirectory() as d:
                src = Path(d) / "b.xlsx"
                src.write_bytes(workbook.build_workbook(db))
                subprocess.run(["soffice", "--headless", "--convert-to", "xlsx", "--outdir", str(Path(d) / "out"), str(src)],
                               capture_output=True, timeout=600)
                wb = openpyxl.load_workbook(Path(d) / "out" / "b.xlsx", data_only=True)
            ws = wb["Stock Moves"]
            heads = [c.value for c in ws[interchange.FIRST_DATA_ROW - 1]]
            col = heads.index("Value")
            xl = [ws.cell(interchange.FIRST_DATA_ROW + i, col + 1).value for i in range(len(app_values))]
            # the app's own value of each row, in the same order
            moves = [m for m in sorted(db.q("SELECT * FROM stock_moves"), key=stockcost.order_key)
                     if not (m["kind"] == "Transfer" and m["qty"] > 0)]
            self.assertEqual(len(moves), len(xl), how)
            for m, v in zip(moves, xl):
                self.assertAlmostEqual(m["value"] / 100, v or 0, delta=0.011,
                                       msg=f"{how}: {m['kind']} {m['qty']} on {m['date']}")
            to = max(r["date"] for r in db.q("SELECT date FROM journal_entries"))
            app = {r["account"]: (r["dr"] - r["cr"]) / 100 for r in db.q(
                "SELECT l.account, SUM(l.debit) dr, SUM(l.credit) cr FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                "WHERE e.date<=? AND e.status='posted' GROUP BY l.account", (to,))}
            tb = {str(r[0]): r[7] or 0 for r in wb["Trial Balance"].iter_rows(min_row=5, values_only=True) if r[0]}
            for k in set(app) | set(tb):
                self.assertAlmostEqual(app.get(k, 0), tb.get(k, 0), delta=0.011, msg=f"{how}: account {k}")

    def test_export_and_import_give_the_same_stock_and_books(self):
        import tempfile
        from erp import workbook
        for how in ("fifo", "average"):
            src = self.books(how)
            path = str(Path(tempfile.mkdtemp()) / "b.xlsx")
            Path(path).write_bytes(workbook.build_workbook(src))
            for label, data in (("json", interchange.read_json(interchange.to_json(src).encode())), ("xlsx", interchange.read_xlsx(path))):
                dst = Database(":memory:")
                res = interchange.import_data(dst, data, mode="replace", user="t")
                self.assertFalse(res.get("errors"), (how, label, res.get("errors")))
                self.assertEqual(dst.setting("stock_costing"), how)
                q = ("SELECT i.name, w.name wh, l.qty, l.value FROM stock_levels l JOIN stock_items i ON i.id=l.item_id "
                     "JOIN warehouses w ON w.id=l.warehouse_id WHERE l.qty<>0 ORDER BY 1, 2")
                self.assertEqual(src.q(q), dst.q(q), (how, label))
                tb = lambda db: {r["account"]: r["v"] for r in db.q(
                    "SELECT l.account, SUM(l.debit-l.credit) v FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                    "WHERE e.status='posted' GROUP BY 1 HAVING v<>0")}
                self.assertEqual(tb(src), tb(dst), (how, label))
                c = [x for x in acc.audit(dst) if x["check"].startswith("Stock register")][0]
                self.assertTrue(c["ok"], (how, label, c))


class CostingSwitch(unittest.TestCase):
    def test_the_demo_switched_to_average_and_back_to_fifo_ties_out_each_time(self):
        from erp import stockcost
        db = Database(":memory:")
        demo.load(db, "admin")
        fifo = db.one("SELECT SUM(value) v FROM stock_levels")["v"]
        for how in ("average", "fifo"):
            stockcost.recost_all(db, how, user="admin")
            c = [x for x in acc.audit(db) if x["check"].startswith("Stock register")][0]
            self.assertTrue(c["ok"] and abs(c["a"] - c["b"]) < 0.005, (how, c))
        self.assertEqual(db.one("SELECT SUM(value) v FROM stock_levels")["v"], fifo)
        self.assertEqual(stockcost.recost_all(db, "fifo", preview=True)["items"], [])     # nothing left to change


class HandTypedWorkbook(unittest.TestCase):
    """A workbook typed by hand: stock at changing prices, a transfer, a count gain without a cost, and sales of stock
    items (Sales Contracts). LibreOffice's figures equal the app's after importing the same rows, line by line."""

    def sheets(self, how):
        d = lambda m, day: month_day(m, day)
        rcv = lambda m, day, wh, item, q, r: {"date": d(m, day), "type": "Receipt", "warehouse": wh, "item": item, "unit": "pcs",
                                                "qty": q, "rate": r, "vendor": "Supplier", "paid": round(q * r, 2)}
        return {
            "Settings": [{"key": "stock_costing", "value": how}],
            "Business Units": [{"code": "SHOP", "name": "Showroom", "type": "Trading"}, {"code": "P1", "name": "Villa"}],
            "Warehouses": [{"name": "Main Store"}, {"name": "Branch"}],
            "Stock Items": [{"name": "Tiles", "unit": "pcs"}, {"name": "Sinks", "unit": "pcs"}],
            "Stock Moves": [
                rcv(3, 1, "Main Store", "Tiles", 100, 40), rcv(3, 2, "Branch", "Tiles", 30, 47.5), rcv(3, 3, "Main Store", "Sinks", 10, 900),
                {"date": d(3, 5), "type": "Issue", "warehouse": "Main Store", "item": "Tiles", "qty": 30, "bu_code": "P1"},
                rcv(3, 6, "Main Store", "Tiles", 50, 43.25),
                {"date": d(3, 8), "type": "Transfer", "warehouse": "Main Store", "to_warehouse": "Branch", "item": "Tiles", "qty": 20},
                {"date": d(3, 12), "type": "Adjustment", "warehouse": "Branch", "item": "Tiles", "qty": 3},
                rcv(2, 2, "Main Store", "Tiles", 40, 46.1), rcv(2, 3, "Main Store", "Sinks", 5, 1020),
                {"date": d(2, 20), "type": "Issue", "warehouse": "Branch", "item": "Tiles", "qty": 25, "bu_code": "P1"}],
            "Products": [{"code": "G-T", "name": "Tiles retail", "kind": "item", "price": 80, "stock_item": "Tiles"},
                         {"code": "G-TB", "name": "Tiles retail (branch)", "kind": "item", "price": 80, "stock_item": "Tiles", "warehouse": "Branch"},
                         {"code": "G-S", "name": "Sink set", "kind": "item", "price": 1900, "stock_item": "Sinks"}],
            "Sales Contracts": [
                {"code": "S1", "date": d(3, 10), "customer": "Buyer A", "bu_code": "SHOP", "product": "G-T", "qty": 60, "confirm": "Yes", "collected": 4800},
                {"code": "S1", "date": d(3, 10), "customer": "Buyer A", "bu_code": "SHOP", "product": "G-S", "qty": 6},
                {"code": "S2", "date": d(2, 2), "customer": "Buyer B", "bu_code": "SHOP", "product": "G-T", "qty": 45, "confirm": "Yes"},
                {"code": "S3", "date": d(2, 2), "customer": "Buyer C", "bu_code": "SHOP", "product": "G-TB", "qty": 12, "confirm": "Yes"},
                {"code": "S4", "date": d(1, 4), "customer": "Buyer D", "bu_code": "SHOP", "product": "G-S", "qty": 8, "confirm": "Yes"}]}

    @unittest.skipUnless(HAVE_XL and __import__("shutil").which("soffice"), "LibreOffice not installed")
    def test_libreoffice_and_the_app_agree(self):
        import subprocess
        import tempfile
        import openpyxl
        from erp import workbook
        for how in ("fifo", "average"):
            S = self.sheets(how)
            db = Database(":memory:")
            res = interchange.import_data(db, json.loads(json.dumps(S)), user="t")
            self.assertFalse(res.get("errors"), (how, res.get("errors")))
            c = [x for x in acc.audit(db) if x["check"].startswith("Stock register")][0]
            self.assertTrue(c["ok"], (how, c))
            with tempfile.TemporaryDirectory() as dd:
                src = Path(dd) / "t.xlsx"
                src.write_bytes(workbook.build_workbook(Database(":memory:"), template=True, data=S))
                subprocess.run(["soffice", "--headless", "--convert-to", "xlsx", "--outdir", str(Path(dd) / "out"), str(src)],
                               capture_output=True, timeout=600)
                wb = openpyxl.load_workbook(Path(dd) / "out" / "t.xlsx", data_only=True)
            # each sale line's cost of sales, as the app booked it
            sc = wb["Sales Contracts"]
            heads = [x.value for x in sc[interchange.FIRST_DATA_ROW - 1]]
            col = heads.index("cost of sales")
            xl = [sc.cell(interchange.FIRST_DATA_ROW + i, col + 1).value or 0 for i in range(len(S["Sales Contracts"]))]
            app = []
            for r in S["Sales Contracts"]:
                e = db.q("SELECT SUM(l.debit) v FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id JOIN stock_moves m ON m.entry_id=e.id "
                         "JOIN stock_items i ON i.id=m.item_id WHERE e.status='posted' AND m.note=? AND i.name=?",
                         (r["code"], {"G-T": "Tiles", "G-TB": "Tiles", "G-S": "Sinks"}[r["product"]]))
                app.append((e[0]["v"] or 0) / 100)
            self.assertEqual([round(x, 2) for x in xl], app, how)
            to = max(r["date"] for r in db.q("SELECT date FROM journal_entries"))
            mine = {r["account"]: (r["dr"] - r["cr"]) / 100 for r in db.q(
                "SELECT l.account, SUM(l.debit) dr, SUM(l.credit) cr FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                "WHERE e.date<=? AND e.status='posted' GROUP BY l.account", (to,))}
            tb = {str(r[0]): r[7] or 0 for r in wb["Trial Balance"].iter_rows(min_row=5, values_only=True) if r[0]}
            for k in set(mine) | set(tb):
                self.assertAlmostEqual(mine.get(k, 0), tb.get(k, 0), delta=0.011, msg=f"{how}: account {k}")
            self.assertEqual([x.value for x in wb["Model Audit"]["E"] if x.value][-1], "ALL OK", how)
