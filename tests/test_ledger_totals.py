"""The general ledger's opening, running balance and closing count every line, whatever part of the list is returned,
and a contact's ledger opens at what that contact owed — not the whole account's balance."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import accounting as acc, reports  # noqa: E402
from erp.db import Database  # noqa: E402


class LedgerTotals(unittest.TestCase):
    def setUp(self):
        self.db = db = Database(":memory:")
        self.ar, rev = db.acct("ar"), db.acct("revenue")
        for i, (who, amt) in enumerate((("Acme", 100), ("Globex", 1000), ("Acme", 50), ("Globex", 7), ("Acme", 20)), 1):
            acc.record_journal(db, {"date": f"2026-0{i}-10", "party": who, "lines": [{"account": self.ar, "debit": amt}, {"account": rev, "credit": amt}]})

    def test_a_contacts_ledger_opens_at_what_that_contact_owed(self):
        gl = acc.general_ledger(self.db, self.ar, "2026-03-01", "2026-12-31", party="Acme", limit=None)
        self.assertEqual(gl["opening"], 100)                     # Acme before March, not Acme + Globex (1100)
        self.assertEqual([r["balance"] for r in gl["rows"]], [150, 170])
        self.assertEqual(gl["closing"], 170)
        t = reports.general_ledger_table(self.db, account=self.ar, date_from="2026-03-01", date_to="2026-12-31", party="Acme")
        self.assertEqual(t["rows"][0]["cells"][-1], 100)

    def test_part_of_the_list_still_has_the_whole_closing_and_carried_balances(self):
        full = acc.general_ledger(self.db, self.ar, None, "2026-12-31", limit=None)
        self.assertEqual(full["closing"], 1177)
        page1 = acc.general_ledger(self.db, self.ar, None, "2026-12-31", limit=2)
        self.assertEqual(page1["closing"], 1177)
        page2 = acc.general_ledger(self.db, self.ar, None, "2026-12-31", limit=2, offset=2)
        self.assertEqual([r["balance"] for r in page2["rows"]], [r["balance"] for r in full["rows"][2:4]])
        self.assertEqual(page2["closing"], 1177)


if __name__ == "__main__":
    unittest.main()
