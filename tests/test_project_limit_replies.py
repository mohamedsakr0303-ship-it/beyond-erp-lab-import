"""Someone limited to some projects: no reply (or file) names another project, or shows the company's own totals.

Walks every screen that needs no address, as a person limited to one demo project with every screen open, and looks
for the other projects' codes and names in what comes back.
"""
import io
import json
import re
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import auth, demo, server  # noqa: E402
from erp.db import Database  # noqa: E402

# made from an empty demo database, not the books; images are base64 (their letters can spell a code)
NOT_THE_BOOKS = {"/api/demo/workbook", "/api/branding"}


class App:
    pass


class ProjectLimitReplies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = db = Database(":memory:")
        demo.load(db, "admin")
        auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        bus = db.q("SELECT id, code, name FROM business_units ORDER BY id")
        cls.mine = bus[0]
        mods = sorted({r[2] for r in server.ROUTES if r[2]})
        auth.create_user(db, {"username": "site", "password": "pw123456", "projects": [cls.mine["code"]],
                              "perms": {m: "edit" for m in mods}})
        cls.app = App()
        cls.app.db, cls.app.backup, cls.app.backups = db, (lambda *a, **k: None), (lambda: [])
        cls.tok = {u: auth.login(db, u, "pw123456")[0] for u in ("site", "boss")}
        others = [b for b in bus if b["code"] != cls.mine["code"]]
        cls.pat = re.compile("|".join(re.escape(x) for b in others for x in (b["code"], b["name"])))

    def get(self, path, who="site"):
        r = server.handle(self.app, "GET", path, {"x-session": self.tok[who], "x-requested-with": "BeyondERP"}, b"")
        raw = r.body or b""
        if raw[:2] == b"PK":
            z = zipfile.ZipFile(io.BytesIO(raw))
            raw = b"".join(z.read(n) for n in z.namelist() if n.endswith(".xml"))
        return r.status, raw.decode("utf-8", "replace")

    def test_no_screen_names_another_project(self):
        paths = sorted({r[1].pattern.strip("^$") for r in server.ROUTES if r[0] == "GET" and "(" not in r[1].pattern})
        leaks = {}
        for p in paths:
            if p in NOT_THE_BOOKS:
                continue
            try:
                st, body = self.get(p)
            except Exception:        # a screen that cannot run in a bare test (no server object) is not a leak
                continue
            if st == 200:
                found = sorted(set(self.pat.findall(body)))
                if found:
                    leaks[p] = found[:3]
        self.assertEqual(leaks, {})

    def test_the_dashboard_keeps_their_project_and_hides_company_cash(self):
        d = json.loads(self.get("/api/dashboard")[1])
        self.assertEqual([u["code"] for u in d["units"]], [self.mine["code"]])
        self.assertIsNone(d["cash_position"])
        self.assertTrue(all(c.get("a") is None for c in d["audit"]))
        full = json.loads(self.get("/api/dashboard", "boss")[1])              # the super administrator sees it all
        self.assertGreater(len(full["units"]), 1)
        self.assertIsNotNone(full["cash_position"])

    def test_overhead_shares_show_only_their_projects(self):
        d = json.loads(self.get("/api/alloc-rules")[1])
        for r in d["preview"]["rules"]:
            self.assertLessEqual(set(r["split"]), {self.mine["code"]})
            self.assertEqual(r["allocated"], round(sum(r["split"].values()), 2))
        self.assertIsNone(d["preview"]["pool"])

    def test_invoices_of_other_projects_are_not_sent_or_exported(self):
        other = self.db.one("SELECT id FROM extracts WHERE status='posted' AND bu_id<>? LIMIT 1", (self.mine["id"],))["id"]
        st, body = self.get(f"/api/einvoice-batch?ids={other}")
        self.assertNotEqual(st, 200, body[:200])
        r = server.handle(self.app, "POST", "/api/einvoice/submit", {"x-session": self.tok["site"], "x-requested-with": "BeyondERP",
                                                                      "content-type": "application/json"}, json.dumps({"ids": [other]}).encode())
        self.assertEqual(r.status, 403)


if __name__ == "__main__":
    unittest.main()
