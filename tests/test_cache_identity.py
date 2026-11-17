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

    def test_two_sets_of_books_with_the_same_change_count_keep_their_own_figures(self):
        one, two = self.books(100), self.books(200)
        self.assertEqual(one.data_version(), two.data_version())          # the counters alone cannot tell them apart
        self.assertEqual((revenue(one), revenue(two)), (10000, 20000))
        self.assertNotEqual(one._cache_token, two._cache_token)            # each set of books has a key of its own

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
        if not reused:                                      # (depends on the memory allocator: proven where it happens)
            self.skipTest("no address was reused in this run")


if __name__ == "__main__":
    unittest.main()
