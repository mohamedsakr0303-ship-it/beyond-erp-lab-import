"""Figures kept between screens (db_cached) belong to their own books: once a set of books is closed, a new one that
CPython places at the same address — another company opened, a fresh set — never receives the old one's figures."""
import gc
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import accounting as a  # noqa: E402
from erp.db import Database, db_cached  # noqa: E402


@db_cached
def revenue(db):
    return db.one("SELECT COALESCE(SUM(credit), 0) n FROM journal_lines WHERE account='4010'")["n"]


class CacheIdentity(unittest.TestCase):
    def books(self, amount):
        db = Database(":memory:")
        db.keeping = lambda: True                   # (as the server keeps figures between screens)
        if amount:
            a.record_journal(db, {"date": "2026-01-05", "lines": [{"account": db.acct("cash"), "debit": amount},
                                                                   {"account": "4010", "credit": amount}]})
        return db

    def test_a_new_set_of_books_at_a_freed_address_gets_its_own_figures(self):
        reused = 0
        for _ in range(40):
            old = self.books(100)
            self.assertEqual(revenue(old), 10000)
            addr, ver = id(old), old.data_version()
            del old
            gc.collect()
            new = self.books(200)                            # the same number of changes, different figures …
            if id(new) == addr:
                reused += 1
                self.assertEqual(revenue(new), 20000)       # … never the closed books' ones, whatever the address
            del new
            gc.collect()
        self.assertGreater(reused, 0, "no address was reused: nothing proven")


if __name__ == "__main__":
    unittest.main()
