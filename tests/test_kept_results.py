"""Results kept between screens (settings, the partners' distribution check) are never stale: they are worked out again
after any write, a dry run that was undone, a write from another connection to the same file, or a restore; the
journal's batch loading gives exactly what loading entries one by one gave; and the browser build's connection tells
writes from reads."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import accounting as acc, demo, partner_pack  # noqa: E402
from erp.db import Database, db_cached  # noqa: E402
from erp.jsdb import _writes  # noqa: E402


class Kept(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "k.sqlite3"
        self.db = Database(self.path)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_settings_follow_every_change(self):
        self.db.set_setting("company_name", "One")
        self.assertEqual(self.db.setting("company_name"), "One")
        self.db.set_setting("company_name", "Two")
        self.assertEqual(self.db.setting("company_name"), "Two")
        other = sqlite3.connect(self.path)                      # another connection to the same file
        other.execute("UPDATE settings SET value='Three' WHERE key='company_name'")
        other.commit()
        other.close()
        self.assertEqual(self.db.setting("company_name"), "Three")
        self.assertEqual(self.db.setting("no such key", "dflt"), "dflt")

    def test_an_undone_dry_run_leaves_nothing_kept(self):
        self.db.set_setting("company_name", "Real")
        self.assertEqual(self.db.setting("company_name"), "Real")
        conn = self.db.conn
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE settings SET value='Dry' WHERE key='company_name'")
        self.assertEqual(self.db.setting("company_name"), "Dry")  # inside: what the transaction sees
        conn.execute("ROLLBACK")
        self.assertEqual(self.db.setting("company_name"), "Real")

    def test_a_restore_forgets_kept_results(self):
        calls = []

        @db_cached
        def figure(db):
            calls.append(1)
            return {"n": len(calls)}
        self.assertEqual(figure(self.db), {"n": 1})
        self.assertEqual(figure(self.db), {"n": 1})               # kept
        figure(self.db)["n"] = 99                                   # a caller changing its copy changes nothing kept
        self.assertEqual(figure(self.db), {"n": 1})
        self.db.bump()
        self.assertEqual(figure(self.db), {"n": 2})
        self.db.set_setting("x", "1")
        self.assertEqual(figure(self.db), {"n": 3})

    def test_the_partners_check_follows_the_books(self):
        demo.load(self.db, "admin")
        before = partner_pack.compute(self.db)
        self.assertEqual(partner_pack.compute(self.db), before)
        cash = self.db.acct("cash")
        rev = self.db.one("SELECT code FROM accounts WHERE type='Revenue' ORDER BY code")["code"]
        acc.post_entry(self.db, date=acc.today(), kind="Journal Voucher", memo="kept test",
                       lines=[{"account": cash, "debit": 5000}, {"account": rev, "credit": 5000}])
        after = partner_pack.compute(self.db)
        self.assertNotEqual(after, before)

    def test_batch_loading_matches_one_by_one(self):
        demo.load(self.db, "admin")
        ids = [r["id"] for r in self.db.q("SELECT id FROM journal_entries ORDER BY date DESC, id DESC")] * 2
        self.assertGreater(len(ids), 500)                           # also in parts, within SQLite's parameter limit
        self.assertEqual(acc.get_entries(self.db, ids), [acc.get_entry(self.db, i) for i in ids])
        self.assertEqual(acc.get_entries(self.db, []), [])

    def test_the_browser_connection_tells_writes_from_reads(self):
        for sql in ("SELECT 1", "  select x", "PRAGMA foreign_keys", "WITH a AS (SELECT 1) SELECT * FROM a", "EXPLAIN SELECT 1"):
            self.assertFalse(_writes(sql), sql)
        for sql in ("INSERT INTO t VALUES(1)", "UPDATE t SET a=1", "DELETE FROM t", "BEGIN", "COMMIT", "CREATE TABLE x(a)",
                    "WITH a AS (SELECT 1) INSERT INTO t SELECT * FROM a"):
            self.assertTrue(_writes(sql), sql)


if __name__ == "__main__":
    unittest.main()
