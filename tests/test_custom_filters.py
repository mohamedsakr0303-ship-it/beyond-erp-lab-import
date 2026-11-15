"""Custom filters (erp/customfilter.py) give exactly the records the conditions describe — counted against the entries
themselves — on the journal, the document screens, a project's page and the exports; anything they cannot do is
refused with a reason, and values are never read as SQL."""
import json
import sys
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import accounting as acc, auth, demo, server  # noqa: E402
from erp.db import Database  # noqa: E402


class App:
    pass


class CustomFilters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = db = Database(":memory:")
        demo.load(db, "admin")
        auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        cls.app = App()
        cls.app.db, cls.app.backup, cls.app.backups = db, (lambda *a, **k: None), (lambda: [])
        cls.tok = auth.login(db, "boss", "pw123456")[0]
        acc.save_group(db, {"code": "G1", "name": "Assets", "type": "Asset"})      # a group to filter by: the 1000s
        db.x("UPDATE accounts SET parent='G1' WHERE code LIKE '1%' AND type='Asset'")
        ids = [r["id"] for r in db.q("SELECT id FROM journal_entries")]
        cls.entries = acc.get_entries(db, ids)
        # a group to filter by: the first one with accounts that have entries
        cls.group = next(g["code"] for g in db.q("SELECT code FROM account_groups ORDER BY code")
                         if set(acc.group_accounts(db, g["code"])) & {ln["account"] for e in cls.entries for ln in e["lines"]}) \
            if db.one("SELECT 1 FROM account_groups") else None

    def call(self, path, rules=None, match=None, raw=None, **q):
        if rules is not None:
            q["rules"] = json.dumps(rules, ensure_ascii=False)
        if raw is not None:
            q["rules"] = raw
        if match:
            q["match"] = match
        r = server.handle(self.app, "GET", path + "?" + urllib.parse.urlencode(q),
                          {"x-session": self.tok, "x-requested-with": "BeyondERP"}, b"")
        return r.status, (json.loads(r.body) if r.body[:1] in (b"{", b"[") else r.body)

    def count(self, rules, match=None, path="/api/journal", **q):
        st, b = self.call(path, rules, match, limit=1, **q)
        self.assertEqual(st, 200, b)
        return b["total"]

    def test_amount_and_dates_match_the_entries(self):
        big = sorted(e["amount"] for e in self.entries)[len(self.entries) // 2]
        self.assertEqual(self.count([{"f": "amount", "op": "gt", "v": str(big)}]), sum(e["amount"] > big for e in self.entries))
        self.assertEqual(self.count([{"f": "amount", "op": "lte", "v": f"{big:,.2f}"}]), sum(e["amount"] <= big for e in self.entries))
        lo, hi = sorted(e["date"] for e in self.entries)[10], sorted(e["date"] for e in self.entries)[-10]
        self.assertEqual(self.count([{"f": "date", "op": "between", "v": hi, "v2": lo}]), sum(lo <= e["date"] <= hi for e in self.entries))
        self.assertEqual(self.count([{"f": "date", "op": "before", "v": lo}]), sum(e["date"] < lo for e in self.entries))

    def test_accounts_groups_and_any_line(self):
        code = self.entries[0]["lines"][0]["account"]
        has = lambda e, c: any(ln["account"] == c for ln in e["lines"])
        self.assertEqual(self.count([{"f": "account", "op": "eq", "v": code}]), sum(has(e, code) for e in self.entries))
        self.assertEqual(self.count([{"f": "account", "op": "ne", "v": code}]), sum(not has(e, code) for e in self.entries))
        self.assertTrue(self.group)
        if self.group:
            inside = set(acc.group_accounts(self.db, self.group))
            n_in = sum(any(ln["account"] in inside for ln in e["lines"]) for e in self.entries)
            self.assertEqual(self.count([{"f": "account", "op": "in_group", "v": self.group}]), n_in)
            self.assertEqual(self.count([{"f": "account", "op": "not_in_group", "v": self.group}]), len(self.entries) - n_in)

    def test_text_party_and_all_or_any(self):
        party = next(e["party"] for e in self.entries if e.get("party"))
        word = party.split()[0].lower()
        has = lambda e: word in (e.get("party") or "").lower() or any(word in (ln.get("party") or "").lower() for ln in e["lines"])
        self.assertEqual(self.count([{"f": "party", "op": "contains", "v": word.upper()}]), sum(has(e) for e in self.entries))
        big = sorted(e["amount"] for e in self.entries)[-20]
        both = sum(has(e) and e["amount"] >= big for e in self.entries)
        either = sum(has(e) or e["amount"] >= big for e in self.entries)
        rules = [{"f": "party", "op": "contains", "v": word}, {"f": "amount", "op": "gte", "v": str(big)}]
        self.assertEqual(self.count(rules), both)
        self.assertEqual(self.count(rules, "any"), either)
        self.assertEqual(self.count([{"f": "memo", "op": "empty"}]), sum(not (e["memo"] or "").strip() for e in self.entries))
        self.assertEqual(self.count([{"f": "attachment", "op": "no"}]), sum(not e["files"] for e in self.entries))

    def test_rules_work_with_the_other_filters_and_on_every_screen(self):
        rules = [{"f": "amount", "op": "gt", "v": "1000"}]
        kinds = {e["kind"] for e in self.entries}
        kind = "Payment Voucher" if "Payment Voucher" in kinds else sorted(kinds)[0]
        want = sum(e["kind"] == kind and e["amount"] > 1000 for e in self.entries)
        self.assertEqual(self.count(rules, kind=kind), want)
        if kind == "Payment Voucher":
            self.assertEqual(self.count(rules, path="/api/docs/payment"), want)
        bu = next(e["bu_id"] for e in self.entries if e["bu_id"])
        self.assertEqual(self.count(rules, path="/api/bu-journal", bu_id=bu),
                         sum(e["bu_id"] == bu and e["amount"] > 1000 for e in self.entries))
        st, body = self.call("/api/reports/vouchers", rules, format="csv")
        self.assertEqual(st, 200)
        st, plain = self.call("/api/reports/vouchers", None, format="csv")
        self.assertLess(len(body), len(plain))                    # the export holds only the matching vouchers

    def test_refused_with_a_reason(self):
        for rules, msg in (([{"f": "nope", "op": "eq", "v": "1"}], "unknown field"), ([{"f": "amount", "op": "contains", "v": "1"}], "cannot use"),
                           ([{"f": "memo", "op": "contains", "v": " "}], "needs a value"), ([{"f": "amount", "op": "gt", "v": "abc"}], "needs a number"),
                           ([{"f": "date", "op": "eq", "v": "yesterday"}], "needs a date"), ([{"f": "account", "op": "in_group", "v": "no-such"}], "no account group"),
                           ([{"f": "memo", "op": "contains", "v": "x"}] * 21, "at most"), ("not json", "not readable"), ({"f": "memo"}, "not readable")):
            with self.subTest(msg):
                st, b = self.call("/api/journal", raw=rules if isinstance(rules, str) else json.dumps(rules))
                self.assertEqual(st, 400, b)
                self.assertIn(msg, b["error"])

    def test_values_are_never_sql(self):
        n = self.db.one("SELECT COUNT(*) n FROM accounts")["n"]
        for v in ("'); DROP TABLE accounts;--", "%", "_", "\\"):
            self.assertEqual(self.count([{"f": "memo", "op": "contains", "v": v}]),
                             sum(v.lower() in (e["memo"] or "").lower() for e in self.entries), v)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM accounts")["n"], n)


if __name__ == "__main__":
    unittest.main()
