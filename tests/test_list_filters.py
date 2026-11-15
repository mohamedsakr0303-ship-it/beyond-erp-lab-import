"""Custom filters on every list screen (erp/customfilter.py LISTS / SQL_LISTS): for each list and each of its fields, a
condition made from the list's own data gives exactly the rows the test picks out by itself from the unfiltered list;
totals above a list follow the filtered rows; exports carry the filter; wrong conditions are refused with the reason."""
import json
import sys
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import auth, customfilter, demo, server  # noqa: E402
from erp.db import Database  # noqa: E402


class App:
    pass


def rows_of(path, body):
    if isinstance(body, list):
        return body
    return body["levels"] if path == "/api/stock" else body["rows"]


def value(row, key):
    return " ".join(str(row.get(k) or "") for k in key) if isinstance(key, tuple) else row.get(key)


def ident(row):
    return json.dumps({k: row.get(k) for k in ("id", "code", "number", "party", "name", "item_id", "warehouse_id", "sale_id", "seq")}, sort_keys=True, default=str)


class ListFilters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = db = Database(":memory:")
        demo.load(db, "admin")
        auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        cls.app = App()
        cls.app.db, cls.app.backup, cls.app.backups = db, (lambda *a, **k: None), (lambda: [])
        cls.tok = auth.login(db, "boss", "pw123456")[0]

    def get(self, path, **q):
        r = server.handle(self.app, "GET", path + "?" + urllib.parse.urlencode(q), {"x-session": self.tok, "x-requested-with": "BeyondERP"}, b"")
        return r.status, (json.loads(r.body) if r.body[:1] in (b"{", b"[") else r.body)

    def cases(self, rows, fields):
        """A condition per field, made from the list's own values."""
        for f, kind, key, *_ in fields:
            vals = [value(r, key) for r in rows]
            if kind == "text":
                words = [str(v).strip() for v in vals if str(v or "").strip()]
                if words:
                    yield f, {"f": f, "op": "contains", "v": words[0][:3]}, lambda r, key=key, w=words[0][:3].lower(): w in str(value(r, key) or "").lower()
                yield f, {"f": f, "op": "empty"}, lambda r, key=key: not str(value(r, key) or "").strip()
            elif kind == "money":
                nums = sorted(float(v or 0) for v in vals)
                mid = nums[len(nums) // 2] if nums else 0
                yield f, {"f": f, "op": "gte", "v": str(mid)}, lambda r, key=key, mid=mid: float(value(r, key) or 0) >= mid - 0.005
            elif kind == "date":
                ds = sorted(str(v)[:10] for v in vals if v)
                if ds:
                    pivot = ds[len(ds) // 2]
                    yield f, {"f": f, "op": "after", "v": pivot}, lambda r, key=key, p=pivot: str(value(r, key) or "")[:10] > p
            elif kind == "bool":
                yield f, {"f": f, "op": "yes"}, lambda r, key=key: bool(value(r, key))

    def test_every_list_and_every_field(self):
        checked = 0
        for path, key in customfilter.LIST_OF_PATH.items():
            st, body = self.get(path)
            self.assertEqual(st, 200, path)
            rows = rows_of(path, body)
            fields = customfilter.LISTS[key]
            for f, rule, pick in self.cases(rows, fields):
                with self.subTest(path=path, field=f, op=rule["op"]):
                    st, got = self.get(path, rules=json.dumps([rule], ensure_ascii=False))
                    self.assertEqual(st, 200, got)
                    want = sorted(ident(r) for r in rows if pick(r))
                    self.assertEqual(sorted(ident(r) for r in rows_of(path, got)), want)
                    checked += 1
        self.assertGreater(checked, 150)

    def test_contacts_are_filtered_before_the_page_is_cut(self):
        st, body = self.get("/api/parties-master", limit=500)
        rows = body["rows"]
        word = rows[0]["name"][:3]
        want = [r["name"] for r in rows if word.lower() in (r["name"] or "").lower()]
        st, got = self.get("/api/parties-master", limit=1, rules=json.dumps([{"f": "name", "op": "contains", "v": word}]))
        self.assertEqual(got["total"], len(want))                  # the count covers every record, the page shows one
        self.assertEqual(len(got["rows"]), 1)
        self.assertIn(got["rows"][0]["name"], want)
        st, got = self.get("/api/parties-master", rules=json.dumps([{"f": "nope", "op": "eq", "v": "1"}]))
        self.assertEqual(st, 400)

    def test_totals_follow_the_filtered_rows(self):
        st, body = self.get("/api/sales")
        code = body["rows"][0]["code"]
        st, got = self.get("/api/sales", rules=json.dumps([{"f": "code", "op": "eq", "v": code}]))
        live = [r for r in got["rows"] if r["status"] in ("confirmed", "delivered")]
        self.assertEqual(got["totals"]["count"], len(live))
        self.assertEqual(got["totals"]["total"], round(sum(r["total"] for r in live), 2))
        st, body = self.get("/api/assets")
        code = body["rows"][0]["code"]
        st, got = self.get("/api/assets", rules=json.dumps([{"f": "code", "op": "ne", "v": code}]))
        self.assertEqual(got["totals"]["cost"], round(sum(r["cost"] for r in got["rows"] if not r["disposed"]), 2))
        self.assertLess(len(got["rows"]), len(body["rows"]))

    def test_exports_carry_the_filter(self):
        for report, key in customfilter.LIST_OF_REPORT.items():
            with self.subTest(report):
                st, plain = self.get(f"/api/reports/{report}")
                self.assertEqual(st, 200, plain)
                items = [r for r in plain["rows"] if r.get("style") == "item"]
                if len(items) < 2:
                    continue
                field = customfilter.LISTS[key][0][0]          # code / name / party: never empty on these lists
                path = next(p for p, k in customfilter.LIST_OF_PATH.items() if k == key)
                rows = rows_of(path, self.get(path)[1])
                first = str(value(rows[0], customfilter.LISTS[key][0][2]))
                st, filt = self.get(f"/api/reports/{report}", rules=json.dumps([{"f": field, "op": "eq", "v": first}], ensure_ascii=False))
                self.assertEqual(st, 200, filt)
                self.assertLess(len([r for r in filt["rows"] if r.get("style") == "item"]), len(items))

    def test_the_filter_stays_with_its_own_list(self):
        rules = json.dumps([{"f": "code", "op": "eq", "v": "no such code"}])
        st, body = self.get("/api/assets", rules=rules)
        self.assertEqual(body["rows"], [])
        st, boot = self.get("/api/bootstrap", rules=rules)        # other calls with the same address part are untouched
        self.assertEqual(st, 200)
        self.assertTrue(boot["accounts"])
        self.assertIn("assets", boot["list_rules"])
        st, body = self.get("/api/assets")
        self.assertTrue(body["rows"])                              # and nothing stays switched on afterwards

    def test_refused_with_a_reason(self):
        for rule, msg in (({"f": "nope", "op": "eq", "v": "1"}, "unknown field"), ({"f": "cost", "op": "contains", "v": "1"}, "cannot use"),
                          ({"f": "cost", "op": "gt", "v": "abc"}, "needs a number"), ({"f": "acquired", "op": "after", "v": "soon"}, "needs a date"),
                          ({"f": "name", "op": "contains", "v": ""}, "needs a value")):
            with self.subTest(msg):
                st, body = self.get("/api/assets", rules=json.dumps([rule]))
                self.assertEqual(st, 400, body)
                self.assertIn(msg, body["error"])


if __name__ == "__main__":
    unittest.main()
