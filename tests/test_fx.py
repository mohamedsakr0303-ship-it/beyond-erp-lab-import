"""Foreign currencies (erp/fx.py): rates (by hand, imported, fetched — a fetched one never replaces the others), entries
in a foreign currency valued at the day's rate going in and at the carried rate going out (the gap is the exchange
difference), accounts kept in a currency, invoices and their e-invoice document, the month-end revaluation reversed the
next day, the screens' routes, and the Excel / JSON files carrying all of it."""
import json
import sys
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import accounting as acc, auth, einvoice, fx, interchange, server  # noqa: E402
from erp.db import Database  # noqa: E402


def books():
    db = Database(":memory:")
    for k, v in (("company_tax_id", "999888777"), ("company_name", "Sample Company"), ("eta_activity_code", "4100"),
                 ("eta_governate", "Cairo"), ("eta_region", "Nasr City"), ("eta_street", "Demo St"), ("eta_building", "12"),
                 ("eta_default_item_code", "EG-999888777-1"), ("fx_currencies", "USD,EUR")):
        db.set_setting(k, v)
    db.x("INSERT INTO business_units(code,name,customer) VALUES ('P1','Villa','Globex')")
    db.x("INSERT INTO parties(name,type,category,receiver_type,tax_id,governate,region,street,building,currency) VALUES "
         "('Globex','customer','customer','B','100200300','Cairo','Maadi','Road 9','4','USD')")
    acc.save_account(db, {"code": "1015", "name": "Bank — USD", "type": "Asset", "subtype": "cash", "currency": "USD"})
    for d, r in (("2026-01-01", 50), ("2026-01-20", 52), ("2026-01-31", 53), ("2026-02-10", 51)):
        fx.set_rate(db, d, "USD", r)
    return db


def bal(db, code, party=None, upto="2099-12-31"):
    w, p = ["e.status='posted'", "l.account=?", "e.date<=?"], [code, upto]
    if party is not None:
        w.append("COALESCE(NULLIF(l.party,''), e.party)=?")
        p.append(party)
    return db.one(f"SELECT COALESCE(SUM(l.debit-l.credit),0) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
                  f"WHERE {' AND '.join(w)}", p)["n"] / 100


def balanced(db):
    return db.one("SELECT COALESCE(SUM(debit),0)-COALESCE(SUM(credit),0) n FROM journal_lines l JOIN journal_entries e "
                  "ON e.id=l.entry_id WHERE e.status='posted'")["n"] == 0


class Rates(unittest.TestCase):
    def test_the_last_rate_on_or_before_the_day_and_a_fetched_one_never_wins(self):
        db = books()
        self.assertEqual(fx.rate_on(db, "USD", "2026-01-25"), 52)
        self.assertEqual(fx.rate_on(db, "EGP", "2026-01-25"), 1.0)
        with self.assertRaisesRegex(acc.ValidationError, "No exchange rate for USD on or before 2025-12-31"):
            fx.rate_on(db, "USD", "2025-12-31")
        fx.set_rate(db, "2026-01-20", "USD", 99, "auto")
        self.assertEqual(fx.rate_on(db, "USD", "2026-01-20"), 52)                 # typed by hand: kept
        fx.set_rate(db, "2026-03-01", "USD", 48, "auto")
        fx.set_rate(db, "2026-03-01", "USD", 49, "auto")                          # a later fetch the same day: updated
        self.assertEqual(fx.rate_on(db, "USD", "2026-03-05"), 49)
        fx.set_rate(db, "2026-03-01", "USD", 47.5, "manual")                      # and a hand rate replaces a fetched one
        self.assertEqual(fx.rate_on(db, "USD", "2026-03-05"), 47.5)
        for bad, msg in (((("2026-01-01", "US", 1)), "not a currency code"), (("2026-01-01", "EGP", 1), "foreign currency"),
                         (("2026-01-01", "USD", 0), "more than zero"), (("2026-01-01", "USD", "abc"), "must be a number")):
            with self.subTest(msg), self.assertRaisesRegex(acc.ValidationError, msg):
                fx.set_rate(db, *bad)

    def test_fetched_rates_are_turned_into_company_currency_per_unit(self):
        db = books()
        got = fx.store_fetched(db, {"result": "success", "base_code": "EGP", "rates": {"USD": 0.02, "EUR": 0.016, "GBP": 0.015}}, "2026-04-01")
        self.assertEqual(got["rates"], {"USD": 50.0, "EUR": 62.5})                # only the company's currencies
        self.assertEqual(fx.rate_on(db, "EUR", "2026-04-02"), 62.5)
        self.assertEqual(db.setting("fx_fetched_on"), "2026-04-01")
        with self.assertRaisesRegex(acc.ValidationError, "answered for USD"):
            fx.store_fetched(db, {"result": "success", "base_code": "USD", "rates": {}})
        with self.assertRaisesRegex(acc.ValidationError, "did not send"):
            fx.store_fetched(db, {"result": "error"})


class Posting(unittest.TestCase):
    def test_in_at_the_days_rate_out_at_the_carried_rate_the_gap_is_the_exchange_difference(self):
        db = books()
        ar, rev, diff = db.acct("ar"), db.acct("revenue"), fx.diff_account(db)
        acc.record_journal(db, {"date": "2026-01-05", "party": "Globex", "currency": "USD",
                                "lines": [{"account": ar, "debit": 1000}, {"account": rev, "credit": 1000}]}, "t")
        self.assertEqual(bal(db, ar, "Globex"), 50000)
        self.assertEqual(fx.carried(db, ar, "Globex", "USD", "2026-12-31"), (100000, 5000000))
        # paid at 52 into the USD bank: the bank gets 52,000, the customer is cleared at the 50,000 it was booked at
        eid = acc.record_collection(db, {"date": "2026-01-21", "bu_code": "P1", "party": "Globex", "mode": "invoice", "amount": 1000,
                                         "received_into": "1015", "currency": "USD"}, "t")
        lines = {ln["account"]: ln for ln in acc.get_entry(db, eid)["lines"]}
        self.assertEqual((lines["1015"]["debit"], lines["1015"]["currency"], lines["1015"]["fx_amount"]), (52000.0, "USD", 1000.0))
        self.assertEqual(lines[ar]["credit"], 50000.0)
        self.assertEqual(lines[diff]["credit"], 2000.0)                            # the exchange gain
        self.assertEqual(bal(db, ar, "Globex"), 0)
        self.assertEqual(fx.carried(db, ar, "Globex", "USD", "2026-12-31"), (0, 0))
        # spending half the USD from the bank: at what it cost (52), whatever the day's rate
        eid = acc.record_expense(db, {"date": "2026-02-11", "amount": 500, "description": "Licence", "paid_from": "1015",
                                      "currency": "USD"}, "t")
        bank = next(ln for ln in acc.get_entry(db, eid)["lines"] if ln["account"] == "1015")
        self.assertEqual(bank["credit"], 26000.0)
        self.assertEqual(bal(db, "1015"), 26000)
        self.assertTrue(balanced(db))

    def test_a_company_currency_bank_pays_in_its_own_currency_and_overpaying_is_counted_in_the_foreign_one(self):
        db = books()
        ap = db.acct("ap")
        acc.record_expense(db, {"date": "2026-01-06", "amount": 300, "vendor": "Initech", "paid": 0, "description": "Parts", "currency": "USD"}, "t")
        with self.assertRaisesRegex(acc.ValidationError, "owed 300.00 USD"):
            acc.record_payment(db, {"date": "2026-01-21", "party": "Initech", "amount": 400, "currency": "USD"}, "t")
        eid = acc.record_payment(db, {"date": "2026-01-21", "party": "Initech", "amount": 300, "currency": "USD", "fx_rate": 53}, "t")
        lines = acc.get_entry(db, eid)["lines"]
        bank = next(ln for ln in lines if ln["account"] == db.acct("bank"))
        self.assertEqual((bank["credit"], bank.get("currency")), (15900.0, None))     # the EGP bank stays in EGP
        self.assertEqual(next(ln for ln in lines if ln["account"] == fx.diff_account(db))["debit"], 900.0)   # the loss
        self.assertEqual(bal(db, ap, "Initech"), 0)
        self.assertTrue(balanced(db))

    def test_a_currency_account_takes_only_its_currency_and_keeps_it(self):
        db = books()
        rev = db.acct("revenue")
        with self.assertRaisesRegex(acc.ValidationError, "kept in USD"):
            acc.record_journal(db, {"date": "2026-01-05", "lines": [{"account": "1015", "debit": 100}, {"account": rev, "credit": 100}]}, "t")
        with self.assertRaisesRegex(acc.ValidationError, "kept in USD, not EUR"):
            acc.record_journal(db, {"date": "2026-01-05", "currency": "EUR", "fx_rate": 60,
                                    "lines": [{"account": "1015", "debit": 100}, {"account": rev, "credit": 100}]}, "t")
        acc.record_journal(db, {"date": "2026-01-05", "lines": [{"account": "1015", "fx_debit": 100}, {"account": rev, "credit": 5000}]}, "t")
        self.assertEqual(bal(db, "1015"), 5000)
        with self.assertRaisesRegex(acc.ValidationError, "currency cannot change"):
            acc.save_account(db, {"code": "1015", "name": "Bank — USD", "type": "Asset", "subtype": "cash", "currency": ""}, old_code="1015")
        with self.assertRaisesRegex(acc.ValidationError, "system account"):
            acc.save_account(db, {"code": db.acct("bank"), "name": "Bank", "type": "Asset", "subtype": "cash", "currency": "USD"}, old_code=db.acct("bank"))
        with self.assertRaisesRegex(acc.ValidationError, "No exchange rate for EUR"):
            acc.record_journal(db, {"date": "2026-01-05", "currency": "EUR", "lines": [{"account": db.acct("ar"), "debit": 1}, {"account": rev, "credit": 1}]}, "t")

    def test_reversing_and_editing_keep_the_foreign_amounts(self):
        db = books()
        ar, rev = db.acct("ar"), db.acct("revenue")
        eid = acc.record_journal(db, {"date": "2026-01-05", "party": "Globex", "currency": "USD",
                                      "lines": [{"account": ar, "debit": 1000}, {"account": rev, "credit": 1000}]}, "t")
        acc.edit_entry(db, eid, {"date": "2026-01-21", "party": "Globex", "currency": "USD",
                                 "lines": [{"account": ar, "debit": 800}, {"account": rev, "credit": 800}]}, "t")
        e = acc.get_entry(db, eid)
        self.assertEqual({(ln["account"], ln["debit"] or ln["credit"], ln["fx_amount"]) for ln in e["lines"]},
                         {(ar, 41600.0, 800.0), (rev, 41600.0, 800.0)})          # at the new day's rate (52)
        rid = acc.reverse_entry(db, eid, "2026-02-01", "t")
        r = acc.get_entry(db, rid)
        self.assertEqual({(ln["account"], ln["credit"] or ln["debit"], ln["currency"], ln["fx_amount"]) for ln in r["lines"]},
                         {(ar, 41600.0, "USD", 800.0), (rev, 41600.0, "USD", 800.0)})
        self.assertEqual(fx.carried(db, ar, "Globex", "USD", "2026-12-31"), (0, 0))


class Revaluation(unittest.TestCase):
    def test_restated_at_month_end_reversed_the_next_day_once_only(self):
        db = books()
        ar, rev = db.acct("ar"), db.acct("revenue")
        acc.record_journal(db, {"date": "2026-01-05", "party": "Globex", "currency": "USD",
                                "lines": [{"account": ar, "debit": 1000}, {"account": rev, "credit": 1000}]}, "t")
        acc.record_journal(db, {"date": "2026-01-06", "lines": [{"account": "1015", "fx_debit": 200}, {"account": rev, "credit": 10000}]}, "t")
        p = fx.revalue(db, "2026-01-31", preview=True)
        self.assertIsNone(p["entry"])
        self.assertEqual(sorted((r["account"], r["fx"], r["home"], r["rate"], r["difference"]) for r in p["rows"]),
                         sorted([("1015", 200.0, 10000.0, 53.0, 600.0), (ar, 1000.0, 50000.0, 53.0, 3000.0)]))
        before = db.one("SELECT COUNT(*) n FROM journal_entries")["n"]
        self.assertEqual(db.one("SELECT COUNT(*) n FROM journal_entries")["n"], before)        # the preview posts nothing
        r = fx.revalue(db, "2026-01-31", "t")
        e, rv = acc.get_entry(db, r["entry"]), acc.get_entry(db, r["reversal"])
        self.assertEqual((e["kind"], e["date"], rv["date"], rv["reversal_of"]), ("FX Revaluation", "2026-01-31", "2026-02-01", r["entry"]))
        self.assertEqual(bal(db, ar, upto="2026-01-31"), 53000)
        self.assertEqual(bal(db, "1015", upto="2026-01-31"), 10600)
        self.assertEqual(bal(db, fx.diff_account(db), upto="2026-01-31"), -3600)            # a gain
        self.assertEqual(bal(db, ar, upto="2026-02-01"), 50000)                              # back to cost on the 1st
        self.assertEqual(fx.carried(db, ar, "Globex", "USD", "2026-02-01"), (100000, 5000000))
        with self.assertRaisesRegex(acc.ValidationError, "already revalued on 2026-01-31"):
            fx.revalue(db, "2026-01-31", "t")
        # paid in February at 51: the gain / loss is against what it cost, not the restated value
        eid = acc.record_collection(db, {"date": "2026-02-11", "bu_code": "P1", "party": "Globex", "mode": "invoice", "amount": 1000,
                                         "received_into": "1015", "currency": "USD"}, "t")
        self.assertEqual(next(ln for ln in acc.get_entry(db, eid)["lines"] if ln["account"] == fx.diff_account(db))["credit"], 1000.0)
        self.assertTrue(balanced(db))


class Invoices(unittest.TestCase):
    def test_an_invoice_in_dollars_posts_at_its_rate_and_its_e_invoice_says_so(self):
        db = books()
        xid = acc.save_extract(db, {"bu_code": "P1", "number": "INV-1", "date": "2026-01-21", "customer": "Globex", "vat_code": "VAT14",
                                    "currency": "USD", "lines": [{"description": "Design works", "qty": 2, "price": 500, "unit": "EA"}]})
        eid = acc.post_extract(db, xid)
        x = acc.get_extract(db, xid)
        self.assertEqual((x["currency"], x["fx_rate"]), ("USD", 52.0))                    # the day's rate, kept on the invoice
        ar = next(ln for ln in acc.get_entry(db, eid)["lines"] if ln["account"] == db.acct("ar"))
        self.assertEqual((ar["debit"], ar["currency"], ar["fx_amount"]), (59280.0, "USD", 1140.0))   # (1,000 + 14% VAT) × 52
        doc, problems = einvoice.build_document(db, xid)
        self.assertEqual(problems, [])
        uv = doc["invoiceLines"][0]["unitValue"]
        self.assertEqual(uv, {"currencySold": "USD", "amountSold": 500.0, "currencyExchangeRate": 52.0, "amountEGP": 26000.0})
        self.assertEqual(doc["netAmount"], 52000.0)
        self.assertEqual(doc["totalAmount"], 59280.0)
        # a credit note is in its invoice's currency, whatever the form says
        cn = acc.save_extract(db, {"bu_code": "P1", "number": "CN-1", "date": "2026-01-31", "customer": "Globex", "vat_code": "VAT14",
                                   "currency": "EUR", "doc_type": "C", "ref_number": "INV-1", "eta_reason": "Discount",
                                   "lines": [{"description": "Design works", "qty": 1, "price": 100, "unit": "EA"}]})
        self.assertEqual(acc.get_extract(db, cn)["currency"], "USD")
        acc.post_extract(db, cn)
        self.assertEqual(fx.carried(db, db.acct("ar"), "Globex", "USD", "2026-12-31")[0], 102600)      # 1,140 − 114 USD
        self.assertTrue(balanced(db))
        from erp import extracts
        row = next(r for r in extracts.board(db)["rows"] if r["id"] == xid)
        self.assertEqual((row["fx"]["currency"], row["fx"]["receivable"], row["receivable"]), ("USD", 1140.0, 59280.0))


class App:
    pass


class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = db = books()
        auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        cls.app = App()
        cls.app.db, cls.app.backup, cls.app.backups = db, (lambda *a, **k: None), (lambda: [])
        cls.tok = auth.login(db, "boss", "pw123456")[0]

    def call(self, method, path, body=None, **q):
        r = server.handle(self.app, method, path + ("?" + urllib.parse.urlencode(q) if q else ""),
                          {"x-session": self.tok, "x-requested-with": "BeyondERP", "content-type": "application/json"},
                          json.dumps(body).encode() if body is not None else b"")
        return r.status, (json.loads(r.body) if r.body[:1] in (b"{", b"[") else r.body)

    def test_settings_rates_fetch_and_revaluation_through_the_server(self):
        st, b = self.call("PUT", "/api/settings", {"fx_currencies": "usd, eur ,EGP,usd", "fx_auto": False})
        self.assertEqual(st, 200, b)
        self.assertEqual((self.db.setting("fx_currencies"), self.db.setting("fx_auto")), ("USD,EUR", "0"))
        st, b = self.call("PUT", "/api/settings", {"fx_currencies": "DOLLARS"})
        self.assertEqual(st, 400)
        st, boot = self.call("GET", "/api/bootstrap")
        self.assertEqual((boot["fx"]["base"], boot["fx"]["currencies"], boot["fx"]["auto"]), ("EGP", ["USD", "EUR"], False))
        st, b = self.call("POST", "/api/fx/rates", {"rows": [{"date": "2026-05-01", "currency": "eur", "rate": "60.5"},
                                                             {"date": "2026-05-02", "currency": "USD", "rate": "50,1"}]})
        self.assertEqual((st, b.get("saved")), (200, 2), b)
        st, b = self.call("POST", "/api/fx/rates", {"rows": [{"date": "2026-05-03", "currency": "EUR", "rate": "61"},
                                                             {"date": "2026-05-04", "currency": "EUR", "rate": "x"}]})
        self.assertEqual(st, 400)
        self.assertIn("Row 2", b["error"])
        self.assertIsNone(self.db.one("SELECT 1 FROM fx_rates WHERE date='2026-05-03'"))     # all or none
        st, b = self.call("GET", "/api/fx/rate", currency="EUR", date="2026-05-09")
        self.assertEqual(b["rate"], 60.5)
        st, b = self.call("POST", "/api/fx/fetch", {"payload": {"result": "success", "base_code": "EGP", "rates": {"USD": 0.025, "EUR": 0.02}}})
        self.assertEqual(st, 200, b)
        self.assertEqual(b["rates"], {"USD": 40.0, "EUR": 50.0})
        st, b = self.call("GET", "/api/fx", currency="EUR")
        self.assertTrue(all(r["currency"] == "EUR" for r in b["rates"]))
        self.assertIn("auto", {r["source"] for r in b["rates"]})
        st, b = self.call("DELETE", "/api/fx/rates", date="2026-05-01", currency="EUR")
        self.assertIsNone(self.db.one("SELECT 1 FROM fx_rates WHERE date='2026-05-01' AND currency='EUR'"))
        st, b = self.call("POST", "/api/journal", {"date": "2026-01-05", "party": "Globex", "currency": "USD", "fx_rate": "50",
                                                   "lines": [{"account": self.db.acct("ar"), "debit": "100"}, {"account": self.db.acct("revenue"), "credit": "100"}]})
        self.assertEqual(st, 200, b)
        st, p = self.call("GET", "/api/fx/revalue", date="2026-01-31")
        self.assertEqual([r["difference"] for r in p["rows"]], [300.0])
        st, r = self.call("POST", "/api/fx/revalue", {"date": "2026-01-31"})
        self.assertEqual(st, 200, r)
        self.assertTrue(r["entry"] and r["reversal"])


class Files(unittest.TestCase):
    def test_the_excel_and_json_files_carry_currencies_rates_and_foreign_lines(self):
        db = books()
        ar, rev = db.acct("ar"), db.acct("revenue")
        acc.record_journal(db, {"date": "2026-01-05", "party": "Globex", "currency": "USD",
                                "lines": [{"account": ar, "debit": 1000}, {"account": rev, "credit": 1000}]}, "t")
        acc.record_collection(db, {"date": "2026-01-21", "bu_code": "P1", "party": "Globex", "mode": "invoice", "amount": 400,
                                   "received_into": "1015", "currency": "USD"}, "t")
        out = interchange.export_data(db)
        self.assertEqual(next(a for a in out["Chart of Accounts"] if a["code"] == "1015")["currency"], "USD")
        self.assertEqual(next(p for p in out["Parties"] if p["name"] == "Globex")["currency"], "USD")
        self.assertEqual(len(out["Exchange Rates"]), 4)
        fxl = [r for r in out["Journal Vouchers"] if r.get("currency")]
        self.assertTrue(fxl and all(r["fx_amount"] and r["fx_rate"] for r in fxl))
        tb = lambda d: {r["account"]: r["n"] for r in d.q("SELECT account, SUM(debit-credit) n FROM journal_lines GROUP BY account")}  # noqa: E731
        fxs = lambda d: sorted((r["account"], r["currency"], r["fx_amount"]) for r in d.q("SELECT * FROM journal_lines WHERE currency<>''"))  # noqa: E731
        for how in ("excel", "json"):
            with self.subTest(how):
                db2 = Database(":memory:")
                data = out if how == "excel" else interchange.read_json(interchange.to_json(db).encode())
                res = interchange.import_data(db2, data, mode="replace")
                self.assertFalse(res.get("errors"), res.get("errors"))
                self.assertEqual(tb(db2), tb(db))
                self.assertEqual(fxs(db2), fxs(db))
                self.assertEqual(fx.carried(db2, ar, "Globex", "USD", "2026-12-31"), fx.carried(db, ar, "Globex", "USD", "2026-12-31"))
                self.assertEqual(db2.one("SELECT currency FROM accounts WHERE code='1015'")["currency"], "USD")
                self.assertEqual(db2.one("SELECT COUNT(*) n FROM fx_rates")["n"], 4)


if __name__ == "__main__":
    unittest.main()
