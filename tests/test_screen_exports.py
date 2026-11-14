"""A screen's Export gives what the screen shows: every Export button names a report that exists; the chart screen
exports the chart, the Partners screen its accounts table (with the transactions as a second choice); the partners'
accounts report has the screen's numbers and the screen's rights."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from erp import accounting as acc, auth, demo, reports, server  # noqa: E402
from erp.db import Database  # noqa: E402

APP_JS = (ROOT / "erp" / "static" / "app.js").read_text(encoding="utf-8")


class App:
    pass


def page(name):
    """The source of one screen (PAGES.<name> up to the next screen)."""
    m = re.search(rf"^PAGES\.{name} = .*?(?=^PAGES[.\[])", APP_JS, re.S | re.M)
    return m.group(0) if m else ""


class ScreenExports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = db = Database(":memory:")
        demo.load(db, "admin")
        auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        auth.create_user(db, {"username": "eq", "password": "pw123456", "perms": {"partners.equity": "view"}})
        auth.create_user(db, {"username": "sales", "password": "pw123456", "perms": {"sales": "edit"}})
        code = db.one("SELECT code FROM business_units ORDER BY id")["code"]
        auth.create_user(db, {"username": "site", "password": "pw123456", "projects": [code], "perms": {"partners": "edit"}})
        cls.app = App()
        cls.app.db, cls.app.backup, cls.app.backups = db, (lambda *a, **k: None), (lambda: [])
        cls.tok = {u: auth.login(db, u, "pw123456")[0] for u in ("boss", "eq", "sales", "site")}

    def call(self, who, path):
        r = server.handle(self.app, "GET", path, {"x-session": self.tok[who], "x-requested-with": "BeyondERP"}, b"")
        return r.status, (json.loads(r.body) if r.body and r.body[:1] in (b"{", b"[") else r.body)

    def test_every_export_button_names_a_report_that_exists(self):
        names = set(re.findall(r'exportMenu\("([a-z_]+)"', APP_JS))
        names |= set(re.findall(r'\], "([a-z_]+)", \{', APP_JS))           # exportMenus([[title, name, params], ...])
        self.assertIn("chart_of_accounts", names)
        for n in sorted(names):
            with self.subTest(n):                                          # through the address the button uses
                st, body = self.call("boss", f"/api/reports/{n}")
                self.assertNotIn("Unknown report", json.dumps(body) if isinstance(body, (dict, list)) else str(body[:300]))
                self.assertIn(st, (200, 400), (n, body if isinstance(body, dict) else ""))   # 400: a one-record report without its record

    def test_screens_export_their_own_table(self):
        self.assertIn('exportMenu("chart_of_accounts"', page("coa"))
        self.assertNotIn('exportMenu("trial_balance"', page("coa"))
        self.assertIn('"partner_accounts"', page("partners"))

    def test_partners_accounts_match_the_screen(self):
        t = reports.build(self.db, "partner_accounts", {"lang": "en"})
        partners = acc.partner_report(self.db, None, None)["partners"]
        self.assertEqual([r["cells"][0] for r in t["rows"][:-1]], [p["name"] for p in partners])
        capital = round(sum(p["accounts"]["capital"] for p in partners), 2)
        self.assertEqual(t["rows"][-1]["cells"][2], capital)
        for fmt in ("xlsx", "pdf", "csv", "docx"):
            st, body = self.call("eq", f"/api/reports/partner_accounts?format={fmt}&lang=ar")
            self.assertEqual(st, 200, fmt)
        self.assertEqual(self.call("sales", "/api/reports/partner_accounts")[0], 403)
        self.assertEqual(self.call("site", "/api/reports/partner_accounts")[0], 403)      # company-wide


if __name__ == "__main__":
    unittest.main()
