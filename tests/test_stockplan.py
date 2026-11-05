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

    @unittest.expectedFailure
    def test_an_issue_entered_with_an_earlier_date_keeps_stock_and_books_agreeing(self):
        """Known limitation, reported: stock is costed in date order, but an issue posted with a date before later receipts
        is valued at the average of the moment it is posted, so the register and the inventory account drift apart. The
        books check flags it. A fix (revaluing later issues, or posting-order costing everywhere) is a costing decision."""
        db = books()
        receive(db, "Main Store", "Cement", 100, 10, month_day(2, 1))
        issue(db, "Main Store", "Cement", 30, "P1", month_day(1, 1))
        receive(db, "Main Store", "Cement", 100, 20, month_day(1, 5))
        issue(db, "Main Store", "Cement", 50, "P1", month_day(2, 10))          # backdated, before the second receipt
        self.check(db)

    def test_a_receipt_already_drawn_on_cannot_vanish(self):
        db = books()
        receive(db, "Main Store", "Cement", 100, 10, month_day(2, 1))
        r2 = receive(db, "Main Store", "Cement", 100, 20, month_day(2, 2))
        i1 = issue(db, "Main Store", "Cement", 100, "P1", month_day(2, 3))
        for undo in (lambda: acc.void_entry(db, r2, "wrong"), lambda: acc.reverse_entry(db, r2)):
            with self.assertRaisesRegex(acc.ValidationError, "Undo the later issues"):
                undo()
        self.check(db)
        acc.void_entry(db, i1, "undo the issue first")
        acc.void_entry(db, r2, "now it can go")
        self.check(db)
        self.assertEqual(db.one("SELECT qty FROM stock_levels")["qty"], 100)

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
