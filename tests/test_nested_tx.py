"""A transaction inside another is a savepoint: when the inner step fails and its caller carries on, what that step had
already written is undone — never left half-done (a stock issue refused for want of stock left no movement behind)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import accounting as acc  # noqa: E402
from erp.db import Database  # noqa: E402


class NestedTransactions(unittest.TestCase):
    def test_a_failed_inner_step_is_undone_and_the_outer_one_goes_on(self):
        db = Database(":memory:")
        with db.tx():
            db.x("INSERT INTO settings(key,value) VALUES ('outer_before','1')")
            try:
                with db.tx():
                    db.x("INSERT INTO settings(key,value) VALUES ('inner','1')")
                    raise acc.ValidationError("refused")
            except acc.ValidationError:
                pass
            with db.tx():
                db.x("INSERT INTO settings(key,value) VALUES ('inner_ok','1')")
            db.x("INSERT INTO settings(key,value) VALUES ('outer_after','1')")
        keys = {r["key"] for r in db.q("SELECT key FROM settings WHERE key IN ('outer_before','inner','inner_ok','outer_after')")}
        self.assertEqual(keys, {"outer_before", "inner_ok", "outer_after"})
        self.assertFalse(db.conn.in_transaction)

    def test_an_outer_failure_still_undoes_everything(self):
        db = Database(":memory:")
        with self.assertRaises(RuntimeError):
            with db.tx():
                with db.tx():
                    db.x("INSERT INTO settings(key,value) VALUES ('inner','1')")
                raise RuntimeError("boom")
        self.assertIsNone(db.one("SELECT 1 FROM settings WHERE key='inner'"))

    def test_a_stock_issue_refused_inside_a_bigger_step_leaves_nothing(self):
        db = Database(":memory:")
        wh = db.one("SELECT id FROM warehouses LIMIT 1") or {"id": db.x("INSERT INTO warehouses(name) VALUES ('Main Store')").lastrowid}
        bu = db.x("INSERT INTO business_units(code,name) VALUES ('B1','B')").lastrowid
        acc.stock_move(db, {"type": "Receipt", "date": "2026-01-01", "warehouse_id": wh["id"], "item": "Gloves", "unit": "pair", "qty": 2, "rate": 5,
                            "vendor": "S", "paid": 10})
        with db.tx():
            try:
                acc.stock_move(db, {"type": "Issue", "date": "2026-01-02", "warehouse_id": wh["id"], "item": "Gloves", "qty": 5, "bu_id": bu})
            except acc.ValidationError:
                pass
        self.assertEqual(db.one("SELECT COUNT(*) n FROM stock_moves WHERE kind='Issue'")["n"], 0)


if __name__ == "__main__":
    unittest.main()
