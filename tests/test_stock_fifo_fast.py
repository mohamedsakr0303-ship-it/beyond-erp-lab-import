"""FIFO cost of the first x units, found by bisection over running sums, is the same to the last bit as adding the
batches one by one — on long, random histories with odd quantities, empty batches and more out than ever came in."""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import stockcost  # noqa: E402


def one_by_one(s, x):
    c = 0.0
    for before, q, unit in s.batches:
        if x <= before:
            break
        c += min(q, x - before) * unit
    if x > s.qin + stockcost.EPS:
        c += (x - s.qin) * s.last_unit
    return c


class FastFifo(unittest.TestCase):
    def test_average_cost_uses_half_up_for_fractional_quantities(self):
        class FakeDB:
            def setting(self, key):
                return "average"

            def q(self, sql, params=()):
                return [
                    {"id": 1, "item_id": 1, "warehouse_id": 1, "date": "2026-01-01", "qty": 0.5,
                     "rate": 101, "value": 51, "booked": 1, "link_id": None, "fixed": 1,
                     "kind": "Receipt", "entry_id": None, "bu_id": None},
                    {"id": 2, "item_id": 1, "warehouse_id": 1, "date": "2026-01-02", "qty": -0.5,
                     "rate": 0, "value": None, "booked": 1, "link_id": None, "fixed": 0,
                     "kind": "Issue", "entry_id": None, "bu_id": None},
                ]

        result = stockcost.compute(FakeDB(), 1)
        self.assertEqual(result["values"][2], 51)
        self.assertEqual(result["levels"][1][:2], (0.0, 0))

    def test_average_count_gain_uses_half_up(self):
        class FakeDB:
            def setting(self, key):
                return "average"

            def q(self, sql, params=()):
                return [
                    {"id": 1, "item_id": 1, "warehouse_id": 1, "date": "2026-01-01", "qty": 0.5,
                     "rate": 101, "value": 51, "booked": 1, "link_id": None, "fixed": 1,
                     "kind": "Receipt", "entry_id": None, "bu_id": None},
                    {"id": 2, "item_id": 1, "warehouse_id": 1, "date": "2026-01-02", "qty": 0.5,
                     "rate": 0, "value": None, "booked": 1, "link_id": None, "fixed": 0,
                     "kind": "Adjustment", "entry_id": None, "bu_id": None},
                ]

        result = stockcost.compute(FakeDB(), 1)
        self.assertEqual(result["values"][2], 51)

    def test_the_same_as_one_by_one(self):
        rnd = random.Random(7)
        for _ in range(20):
            s = stockcost._Store()
            for _ in range(400):
                if rnd.random() < 0.55:
                    q = rnd.choice((0.0, 1, 2.5, 10, 0.333, 120))
                    s.take_in(q, rnd.randint(0, 99999), 1.0)
                else:
                    s.take_out(rnd.choice((1, 3.7, 9)), 0)
                for x in (0, s.qout, s.qout + 1.5, s.qin, s.qin + 4, rnd.uniform(0, s.qin + 5)) + tuple(b[0] for b in s.batches[-3:]):
                    self.assertEqual(s.first_cost(x), one_by_one(s, x))

    def test_a_long_history_stays_quick(self):
        import time
        s = stockcost._Store()
        t = time.time()
        for i in range(20000):
            s.take_in(10, 500 + i % 9, 1.0)
            s.take_out(9, stockcost.half_up(s.first_cost(s.qout + 9) - s.first_cost(s.qout)))
        self.assertLess(time.time() - t, 2.0)        # one by one this took minutes


if __name__ == "__main__":
    unittest.main()
