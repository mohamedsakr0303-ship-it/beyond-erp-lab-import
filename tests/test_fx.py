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


    def test_paying_a_named_invoice_values_it_at_that_invoices_rate(self):
        db = books()
        ar, rev = db.acct("ar"), db.acct("revenue")
        a = acc.record_journal(db, {"date": "2026-01-05", "party": "Globex", "currency": "USD", "lines": [{"account": ar, "debit": 1000}, {"account": rev, "credit": 1000}]}, "t")
        b = acc.record_journal(db, {"date": "2026-01-20", "party": "Globex", "currency": "USD", "lines": [{"account": ar, "debit": 1000}, {"account": rev, "credit": 1000}]}, "t")
        self.assertEqual([(x["id"], x["open"], x["rate"]) for x in fx.open_items(db, "Globex", ar, "USD")], [(a, 1000.0, 50.0), (b, 1000.0, 52.0)])
        eid = acc.record_collection(db, {"date": "2026-01-31", "bu_code": "P1", "party": "Globex", "mode": "invoice", "amount": 400, "received_into": "1015",
                                         "currency": "USD", "settles": b}, "t")
        lines = {ln["account"]: ln for ln in acc.get_entry(db, eid)["lines"]}
        self.assertEqual(lines[ar]["credit"], 20800.0)                              # at the 52 of the invoice it pays, not the 51 average
        self.assertEqual(lines[fx.diff_account(db)]["credit"], 400.0)              # 400 × (53 − 52): that invoice's gain, exactly
        self.assertEqual([(x["id"], x["open"]) for x in fx.open_items(db, "Globex", ar, "USD")], [(a, 1000.0), (b, 600.0)])
        acc.record_collection(db, {"date": "2026-01-31", "bu_code": "P1", "party": "Globex", "mode": "invoice", "amount": 1000, "received_into": "1015",
                                   "currency": "USD", "settles": a}, "t")
        self.assertEqual([(x["id"], x["open"]) for x in fx.open_items(db, "Globex", ar, "USD")], [(b, 600.0)])
        self.assertEqual(fx.carried(db, ar, "Globex", "USD", "2026-12-31"), (60000, 3120000))   # what is left is the rest of b, at 52
        self.assertTrue(balanced(db))

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
        self.assertEqual(bal(db, fx.unrealised_account(db), upto="2026-01-31"), -3600)      # an unrealised gain, apart from realised ones
        self.assertEqual(bal(db, fx.diff_account(db), upto="2026-01-31"), 0)
        self.assertEqual(bal(db, ar, upto="2026-02-01"), 50000)                              # back to cost on the 1st
        self.assertEqual(fx.carried(db, ar, "Globex", "USD", "2026-02-01"), (100000, 5000000))
        with self.assertRaisesRegex(acc.ValidationError, "already revalued on 2026-01-31"):
            fx.revalue(db, "2026-01-31", "t")
        acc.void_entry(db, r["entry"], "wrong rate", "t")                  # undone together with its reversal…
        self.assertEqual({x["status"] for x in db.q("SELECT status FROM journal_entries WHERE id IN (?,?)", (r["entry"], r["reversal"]))}, {"void"})
        r = fx.revalue(db, "2026-01-31", "t")                                # …so it can be done again
        self.assertTrue(r["entry"])
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


class EveryModule(unittest.TestCase):
    def test_stock_bought_in_dollars_is_valued_in_pounds_and_issued_at_that_cost(self):
        db = books()
        wh = db.one("SELECT id FROM warehouses LIMIT 1")["id"]
        acc.stock_move(db, {"type": "Receipt", "date": "2026-01-21", "warehouse_id": wh, "item": "Steel", "unit": "t", "qty": 2, "rate": 100,
                            "vendor": "Initech", "paid": 0, "currency": "USD"}, "t")
        m = db.one("SELECT value FROM stock_moves ORDER BY id DESC LIMIT 1")
        self.assertEqual(m["value"], 1040000)                                      # 200 USD × 52 = 10,400.00
        self.assertEqual(fx.carried(db, db.acct("ap"), "Initech", "USD", "2026-12-31")[0], -20000)   # 200 USD owed
        with acc.in_currency("USD"):                                               # a request in USD: the issue stays at cost
            eid = acc.stock_move(db, {"type": "Issue", "date": "2026-01-22", "warehouse_id": wh, "item": "Steel", "qty": 1, "bu_code": "P1"}, "t")
        lines = acc.get_entry(db, eid)["lines"]
        self.assertEqual({(ln["debit"] or ln["credit"], ln.get("currency")) for ln in lines}, {(5200.0, None)})
        self.assertTrue(balanced(db))

    def test_an_asset_bought_in_dollars_is_kept_and_depreciated_in_pounds(self):
        from erp import assets
        db = books()
        aid = assets.save(db, {"name": "Crane", "account": db.acct("fixed_asset") or db.one("SELECT code FROM accounts WHERE type='Asset' AND subtype LIKE '%ppe%' OR subtype='fixed_assets' LIMIT 1")["code"], "acquired": "2026-01-21", "cost": 1000, "salvage": 100, "life_months": 10,
                               "pay": "payable", "vendor": "Initech", "paid": 0, "currency": "USD"}, user="t")
        a = assets.get(db, aid)
        self.assertEqual((a["cost"], a["salvage"]), (52000.0, 5200.0) if isinstance(a["cost"], float) else (5200000, 520000))
        self.assertEqual(fx.carried(db, db.acct("ap"), "Initech", "USD", "2026-12-31")[0], -100000)
        self.assertTrue(balanced(db))

    def test_a_dollar_bank_account_is_reconciled_in_dollars(self):
        from erp import bankrec
        db = books()
        acc.record_journal(db, {"date": "2026-01-05", "lines": [{"account": "1015", "fx_debit": 500}, {"account": db.acct("revenue"), "credit": 25000}]}, "t")
        sid = bankrec.save(db, {"account": "1015", "opening": 0, "lines": [{"date": "2026-01-05", "description": "Deposit", "amount": 500},
                                                                           {"date": "2026-01-25", "description": "Fee", "amount": -10}]})
        self.assertEqual(bankrec.book_balance(db, "1015", "2026-01-31"), 50000)       # 500.00 in USD, as the statement
        ln = db.one("SELECT id FROM bank_lines WHERE statement_id=? AND amount<0", (sid,))["id"]
        eid = bankrec.create_entry(db, sid, ln, {"account": db.acct("other_overhead")}, "t")
        bank = next(x for x in acc.get_entry(db, eid)["lines"] if x["account"] == "1015")
        self.assertEqual((bank["credit"], bank["currency"], bank["fx_amount"]), (500.0, "USD", 10.0))   # at the 50 it cost
        self.assertEqual(bankrec.book_balance(db, "1015", "2026-01-31"), 49000)
        self.assertTrue(balanced(db))

    def test_supplier_discount_and_partner_money_in_dollars(self):
        db = books()
        acc.record_expense(db, {"date": "2026-01-06", "amount": 300, "vendor": "Initech", "paid": 0, "description": "Parts", "currency": "USD"}, "t")
        with self.assertRaisesRegex(acc.ValidationError, "only owed 300.00 USD"):
            acc.record_supplier_discount(db, {"date": "2026-01-21", "party": "Initech", "amount": 301, "currency": "USD"}, "t")
        acc.record_supplier_discount(db, {"date": "2026-01-21", "party": "Initech", "amount": 300, "currency": "USD"}, "t")
        self.assertEqual(fx.carried(db, db.acct("ap"), "Initech", "USD", "2026-12-31"), (0, 0))
        self.assertTrue(balanced(db))


class Documents(unittest.TestCase):
    def test_a_dollar_loan_is_followed_in_dollars_and_repaid_at_what_it_cost(self):
        from erp import loans
        db = books()
        lid = loans.save(db, {"direction": "borrowed", "party": "Big Bank", "principal": 10000, "start_date": "2026-01-05",
                              "term_months": 12, "interest_type": "none", "schedule_type": "none", "currency": "USD", "paid_from": "1015"}, user="t")
        ln = loans.get(db, lid)
        self.assertEqual(ln["currency"], "USD")
        self.assertEqual(loans.principal_balance(db, ln), 1000000)                  # 10,000.00 USD owed
        self.assertEqual(bal(db, ln["account"]), -500000)                            # booked at 50
        loans.post(db, lid, "pay", {"date": "2026-01-21", "principal": 5000, "paid_from": "1015"}, "t")
        self.assertEqual(loans.principal_balance(db, ln), 500000)
        self.assertEqual(bal(db, ln["account"]), -250000)                            # half of what it cost
        self.assertEqual(bal(db, "1015"), 250000)                                    # the USD went out at the 50 they came in at
        with self.assertRaisesRegex(acc.ValidationError, "Only 5,000.00 of principal"):
            loans.post(db, lid, "pay", {"date": "2026-01-22", "principal": 6000, "paid_from": "1015"}, "t")
        # repaid from the pound account at 52: the loan clears at cost, the 10,000 more is an exchange loss
        loans.post(db, lid, "pay", {"date": "2026-01-22", "principal": 5000}, "t")
        self.assertEqual(loans.principal_balance(db, ln), 0)
        self.assertEqual(bal(db, ln["account"]), 0)
        self.assertEqual(bal(db, fx.diff_account(db)), 10000)
        with self.assertRaisesRegex(acc.ValidationError, "currency cannot change"):
            loans.save(db, {**{k: ln[k] for k in ("direction", "party", "start_date", "term_months", "interest_type", "schedule_type")},
                            "principal": 100, "currency": ""}, lid)
        self.assertTrue(balanced(db))


    def test_a_purchase_order_in_dollars_receives_in_dollars(self):
        from erp import purchasing
        db = books()
        pid = purchasing.save(db, {"supplier": "Initech", "date": "2026-01-10", "currency": "USD",
                                   "lines": [{"kind": "expense", "description": "Software", "account": db.acct("other_overhead"), "qty": 2, "rate": 150}]}, "t")
        purchasing.approve(db, pid, "boss")
        po = purchasing.get(db, pid)
        self.assertEqual(po["currency"], "USD")
        purchasing.receive(db, pid, {"date": "2026-01-21", "lines": [{"line_id": po["lines"][0]["id"], "qty": 2}]}, "t")
        self.assertEqual(fx.carried(db, db.acct("ap"), "Initech", "USD", "2026-12-31"), (-30000, -1560000))   # 300 USD at 52
        self.assertTrue(balanced(db))


    def test_a_sale_in_dollars_collects_and_invoices_in_dollars_and_costs_in_pounds(self):
        from erp import sales
        db = books()
        unit = sales.save_product(db, {"name": "Villa V-1", "kind": "unit", "price": 30000, "price_currency": "USD", "bu_code": "P1", "cost": 1000000, "inventory_account": "1210"})
        sid = sales.save(db, {"customer": "Globex", "bu_code": "P1", "date": "2026-01-05", "revenue_on": "delivery", "currency": "USD",
                              "lines": [{"product_id": unit}], "plan": {"type": "full"}})
        with acc.in_currency("USD"):
            sales.confirm(db, sid, {"date": "2026-01-05", "collect": 10000, "method": "Bank Transfer", "received_into": "1015"})
        adv = db.acct("client_advances")
        self.assertEqual(fx.carried(db, adv, "Globex", "USD", "2026-12-31"), (-1000000, -50000000))   # 10,000 USD at 50
        with acc.in_currency("USD"):
            sales.collect(db, sid, {"date": "2026-01-21", "amount": 20000, "method": "Bank Transfer", "received_into": "1015"})
            sales.deliver(db, sid, {"date": "2026-01-31"})
        d = sales.detail(db, sid, "2026-01-31")
        self.assertEqual((d["paid"], d["invoiced"]), (30000, True))
        x = acc.get_extract(db, sales.get(db, sid)["extract_id"])
        self.assertEqual((x["currency"], x["fx_rate"]), ("USD", 53.0))
        self.assertEqual(fx.carried(db, adv, "Globex", "USD", "2026-12-31")[0], 0)
        self.assertEqual(fx.carried(db, db.acct("ar"), "Globex", "USD", "2026-12-31")[0], 0)   # invoiced, then cleared by the advances
        cos = next(r for r in db.q("SELECT l.* FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id WHERE e.kind='Cost of Sales'"))
        self.assertEqual((cos["debit"], cos["currency"]), (100000000, ""))                      # what the villa cost, in pounds
        self.assertTrue(balanced(db))


    def test_a_subcontract_in_dollars_certifies_recovers_and_pays_in_dollars(self):
        from erp import contracts
        db = books()
        sid = contracts.save(db, {"kind": "sub", "bu_code": "P1", "party": "Sub Co", "retention_pct": 10, "advance_recovery_pct": 20, "currency": "USD",
                                  "lines": [{"description": "Plaster", "qty": 1000, "rate": 100}, {"description": "Paint", "qty": 1000, "rate": 50}]})
        lines = contracts.get(db, sid)["lines"]
        contracts.pay(db, sid, {"kind": "advance", "amount": 20000, "date": "2026-01-05", "paid_from": "1015"}, "t")
        x1 = contracts.save_certificate(db, sid, {"date": "2026-01-21", "lines": [{"line_id": lines[0]["id"], "qty_to_date": 300}]})
        contracts.approve_certificate(db, x1)
        contracts.post_certificate(db, x1)
        st = contracts.get(db, sid)["statement"]
        self.assertEqual((st["certified"], st["retention_held"], st["advance_outstanding"], st["payable"]), (30000, 3000, 14000, 21000))
        contracts.pay(db, sid, {"kind": "payment", "amount": 21000, "date": "2026-01-31"}, "t")
        st = contracts.get(db, sid)["statement"]
        self.assertEqual((st["paid"], st["payable"]), (21000, 0))
        self.assertEqual(bal(db, db.acct("ap"), "Sub Co"), 0)
        self.assertTrue(balanced(db))


    def test_a_dollar_subscription_bills_in_dollars_and_earns_at_the_invoice_rate(self):
        from erp import services
        db = books()
        sid = services.save(db, {"customer": "Globex", "title": "Support", "price": 300, "period_months": 3, "start": "2026-01-21", "currency": "USD"}, "t")
        services.run(db, "2026-01-21", "t")
        x = db.one("SELECT * FROM extracts ORDER BY id DESC LIMIT 1")
        self.assertEqual((x["currency"], x["fx_rate"]), ("USD", 52.0))
        self.assertEqual(fx.carried(db, db.acct("ar"), "Globex", "USD", "2026-12-31")[0], 30000)
        services.recognize(db, "2026-03-31", "t")
        self.assertEqual(bal(db, db.acct("service_deferred")), 0)          # deferred at 52, released at 52: nothing left over
        with self.assertRaisesRegex(acc.ValidationError, "main subscription's currency"):
            services.save(db, {"customer": "Globex", "title": "Extra", "price": 10, "parent_id": sid, "currency": "EUR"}, "t")
        self.assertTrue(balanced(db))

    def test_a_dollar_rental_deposit_is_held_in_dollars(self):
        from erp import rentals
        db = books()
        rid = rentals.save(db, {"client": "Globex", "site": "Tower", "start_date": "2026-01-05", "duration_months": 3, "currency": "USD", "lines": [{"kind": "charge", "charge_type": "install", "charge_when": "delivery", "description": "Installation", "qty": 1, "rate": 100}]}, user="t")
        r = rentals.get(db, rid)
        self.assertEqual(r["currency"], "USD")
        rentals.deposit(db, r, {"type": "receive", "amount": 1000, "date": "2026-01-05", "paid_from": "1015"}, "t")
        self.assertEqual(rentals.deposit_balance(db, r), 100000)                 # 1,000.00 USD
        with self.assertRaisesRegex(acc.ValidationError, "Only 1,000.00 of deposit"):
            rentals.deposit(db, r, {"type": "refund", "amount": 1001, "date": "2026-01-21", "paid_from": "1015"}, "t")
        rentals.deposit(db, r, {"type": "refund", "amount": 1000, "date": "2026-01-21", "paid_from": "1015"}, "t")
        self.assertEqual((rentals.deposit_balance(db, r), bal(db, "1015")), (0, 0))
        self.assertTrue(balanced(db))


    def test_a_dollar_custody_is_followed_in_dollars(self):
        from erp import custody
        db = books()
        cid = custody.save(db, {"holder": "Samir", "account": "1015", "issued_on": "2026-01-05", "bu_code": "P1", "booking": "none"}, user="t")
        custody.post(db, cid, "issue", {"date": "2026-01-05", "amount": 500, "paid_from": db.acct("bank")}, "t")      # 500 USD from the pound bank
        custody.post(db, cid, "settle", {"date": "2026-01-21", "report_no": "R-1", "lines": [
            {"date": "2026-01-20", "description": "Taxi", "account": db.acct("other_overhead"), "amount": 100}]}, "t")
        st = custody.status(db, custody.get(db, cid))
        self.assertEqual(st["balance"], 400.0)                                     # 400 USD left with Samir, not pounds
        self.assertEqual(bal(db, "1015"), 20000)                                   # 400 USD at the 50 they were given at
        self.assertEqual(custody.statement(db, cid)["rows"][-1]["balance"], 400.0)
        self.assertTrue(balanced(db))

    def test_maintenance_paid_in_dollars_keeps_the_machine_cost_in_pounds(self):
        from erp import equipment
        db = books()
        e = equipment.save(db, {"name": "Mixer", "hourly_rate": 100, "meter_start": 50})
        lid = equipment.log_maintenance(db, {"equipment_id": e, "cost": 100, "date": "2026-01-21", "paid_from": "1015", "currency": "USD"}, "t")
        self.assertEqual(db.one("SELECT cost FROM maintenance_logs WHERE id=?", (lid,))["cost"], 520000)     # 100 USD × 52
        self.assertTrue(balanced(db))

    def test_a_patient_and_an_insurer_who_deal_in_dollars_owe_in_dollars(self):
        from erp import care, industries
        db = Database(":memory:")
        industries.apply(db, "healthcare")
        db.set_setting("fx_currencies", "USD")
        fx.set_rate(db, "2026-01-01", "USD", 50)
        db.x("INSERT INTO business_units(code,name) VALUES ('CL1','Clinic 1')")
        bu = db.one("SELECT id FROM business_units WHERE code='CL1'")["id"]
        gp = care.save_service(db, {"name": "GP", "category": "consultation", "price": 400, "insurer_price": 300, "provider_pct": 40})
        pid = care.save_patient(db, {"name": "Tourist Tom", "payer": "insurer", "insurer": "Global TPA", "coverage_pct": 80, "currency": "USD"})
        db.x("UPDATE parties SET currency='USD' WHERE name='Global TPA'")
        vid = care.save_visit(db, {"date": "2026-02-01", "patient_id": pid, "provider": "Dr. A", "bu_id": bu, "lines": [{"service_id": gp, "qty": 1}]}, "boss")
        care.set_status(db, vid, "arrived", {"time": "10:00"})
        care.set_status(db, vid, "done", {"time": "10:30"})
        care.bill_visit(db, vid, {"paid": 0}, "boss")
        ins = care.INS
        self.assertEqual(fx.carried(db, ins, "Global TPA", "USD", "2026-12-31"), (480, 24000))          # 240 EGP = 4.80 USD
        self.assertEqual(fx.carried(db, care.AR, "Tourist Tom", "USD", "2026-12-31"), (120, 6000))
        cl = care.make_claim(db, "Global TPA", "2026-01-01", "2026-12-31", "boss")
        care.settle_claim(db, cl["id"] if isinstance(cl, dict) else cl, {"date": "2026-02-10", "paid": 240, "rejected": 0}, "boss")
        self.assertEqual(fx.carried(db, ins, "Global TPA", "USD", "2026-12-31"), (0, 0))
        self.assertTrue(balanced(db))

    def test_an_employee_paid_in_dollars_is_owed_the_net_in_dollars(self):
        from erp import payroll as P
        db = books()
        e = P.save_employee(db, {"name": "Expat Eng", "basic": 10400, "insured": "No", "taxed": "No", "bu_code": "P1"})
        db.x("INSERT INTO parties(name,type,currency) VALUES ('Expat Eng','employee','USD')")
        r = P.create_run(db, {"period": "2026-01", "date": "2026-01-21"})
        db.x("UPDATE payroll_runs SET date='2026-01-21' WHERE id=?", (r,))
        P.post_run(db, r)
        acr = db.acct("accrued_payroll")
        self.assertEqual(fx.carried(db, acr, "Expat Eng", "USD", "2026-12-31"), (-20000, -1040000))     # 10,400 EGP = 200 USD
        acc.record_payment(db, {"date": "2026-01-31", "against": acr, "party": "Expat Eng", "amount": 200, "paid_from": "1015", "currency": "USD"}, "t")
        self.assertEqual(fx.carried(db, acr, "Expat Eng", "USD", "2026-12-31"), (0, 0))
        self.assertTrue(balanced(db))


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
        named = acc.record_journal(db, {"date": "2026-01-06", "party": "Globex", "currency": "USD", "ref": "JV-NAMED",
                                        "lines": [{"account": ar, "debit": 300}, {"account": rev, "credit": 300}]}, "t")
        acc.record_collection(db, {"date": "2026-01-22", "bu_code": "P1", "party": "Globex", "mode": "invoice", "amount": 100,
                                   "received_into": "1015", "currency": "USD", "settles": named}, "t")
        # a USD invoice partly billed out of work booked as done (unbilled revenue), and stock bought in USD
        acc.record_journal(db, {"date": "2026-01-10", "bu_code": "P1", "lines": [{"account": db.acct("unbilled_revenue"), "debit": 10400},
                                                                                {"account": rev, "credit": 10400}]}, "t")
        xid = acc.save_extract(db, {"bu_code": "P1", "number": "INV-USD", "date": "2026-01-21", "customer": "Globex", "currency": "USD",
                                    "lines": [{"description": "Works", "qty": 1, "price": 500}]})
        acc.post_extract(db, xid)
        wh = db.one("SELECT id FROM warehouses LIMIT 1")["id"]
        acc.stock_move(db, {"type": "Receipt", "date": "2026-01-21", "warehouse_id": wh, "item": "Steel", "qty": 2, "rate": 100,
                            "vendor": "Initech", "paid": 0, "currency": "USD"}, "t")
        out = interchange.export_data(db)
        self.assertEqual(next(r for r in out["Sales Invoices"] if r["number"] == "INV-USD")["from_unbilled"], 200.0)   # 10,400 at 52
        self.assertEqual(next(r for r in out["Stock Moves"] if r["item"] == "Steel")["currency"], "USD")
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
                self.assertEqual(db2.one("SELECT value FROM stock_moves WHERE kind='Receipt'")["value"], 1040000)
                n2 = db2.one("SELECT id FROM journal_entries WHERE ref='JV-NAMED'")["id"]
                self.assertEqual(fx.open_item(db2, n2, ar, "Globex", "USD")[0], 20000)       # the payment that named it still does


class Ideas(unittest.TestCase):
    """Balances shown per currency, the month-end reminder, and products priced in a currency."""
    def setUp(self):
        self.db = db = books()
        ar, rev = db.acct("ar"), db.acct("revenue")
        with acc.in_currency("USD"):
            acc.record_journal(db, {"date": "2026-01-05", "party": "Globex", "memo": "USD invoice",
                                    "lines": [{"account": ar, "debit": 1000}, {"account": rev, "credit": 1000}]})
        acc.record_journal(db, {"date": "2026-01-06", "party": "Initech", "memo": "EGP invoice",
                                "lines": [{"account": ar, "debit": 700}, {"account": rev, "credit": 700}]})

    def test_aging_ledger_and_balances_show_what_is_owed_in_each_currency(self):
        from erp import reports, statements
        db = self.db
        ag = {r["party"]: r for r in statements.aging(db, "2026-01-31")["rows"]}
        self.assertEqual((ag["Globex"]["fx"], ag["Initech"]["fx"]), ({"USD": 1000.0}, {}))
        t = reports.aging_table(db, "2026-01-31", lang="en")
        self.assertEqual(t["columns"][-1]["label"], "In foreign currency")
        row = next(r for r in t["rows"] if r["cells"][0] == "Globex")
        self.assertEqual(row["cells"][-1], "USD 1,000.00")
        self.assertTrue(all(len(r["cells"]) == len(t["columns"]) for r in t["rows"]))
        gl = reports.general_ledger_table(db, account=db.acct("ar"), date_from="2026-01-01", date_to="2026-01-31")
        self.assertEqual(gl["columns"][-1]["label"], "In foreign currency")
        self.assertIn("USD 1,000.00 @ 50", [r["cells"][-1] for r in gl["rows"]])
        self.assertTrue(all(len(r["cells"]) == len(gl["columns"]) for r in gl["rows"]))
        pb = {r["party"]: r for r in acc.party_balances(db, subtypes=("receivable",))}
        self.assertEqual(pb["Globex"]["fx"], {"USD": -1000.0})     # (credit side positive: payables' sign)

    def test_the_dashboard_tile_values_open_balances_at_todays_rate(self):
        from erp import dashboards
        c = dashboards.Ctx(self.db, {"id": 1}, {}, lambda *a: True)
        x = dashboards.w_fx_balances(c, {})
        r = next(r for r in x["rows"] if r["side"] == "owed_us")
        self.assertEqual((r["currency"], r["fx"], r["book"]), ("USD", 1000.0, 50000.0))
        self.assertEqual(r["now"], 51000.0)                        # the last rate, 51
        self.assertEqual(r["diff"], 1000.0)

    def test_last_month_end_is_reminded_until_revalued_and_needs_fresh_rates_to_run_alone(self):
        from erp import dashboards
        db = self.db
        due = fx.revaluation_due(db, "2026-02-15")
        self.assertEqual((due["date"], due["count"], due["difference"], due["rates_ok"]), ("2026-01-31", 1, 3000.0, True))
        texts = [a["en"] for a in dashboards.alerts(db, lambda *a: True)]
        self.assertEqual(fx.revaluation_due(db, "2026-03-03")["rates_ok"], False)   # no rate in February's last week
        fx.revalue(db, "2026-01-31")
        self.assertIsNone(fx.revaluation_due(db, "2026-02-15"))
        self.assertTrue(any(x.startswith("Foreign balances not revalued at ") for x in texts), texts)   # (as of today's last month-end)
        db.set_setting("fx_currencies", "")
        self.assertIsNone(fx.revaluation_due(db, "2026-02-15"))

    def test_a_product_priced_in_dollars_is_converted_into_the_sales_currency(self):
        from erp import sales
        db = self.db
        p = sales.save_product(db, {"name": "Laptop", "kind": "item", "price": 100, "price_currency": "USD"})
        self.assertEqual(sales.get_product(db, p)["price_currency"], "USD")
        self.assertEqual(sales.preview(db, {"date": "2026-01-20", "lines": [{"product_id": p}]})["net"], 5200)       # 100 × 52, in pounds
        self.assertEqual(sales.preview(db, {"date": "2026-01-20", "currency": "USD", "lines": [{"product_id": p}]})["net"], 100)
        self.assertEqual(sales.preview(db, {"date": "2026-01-20", "lines": [{"product_id": p, "price": 4000}]})["net"], 4000)  # typed wins
        fx.set_rate(db, "2026-01-01", "EUR", 55)
        self.assertEqual(sales.preview(db, {"date": "2026-01-20", "currency": "EUR", "lines": [{"product_id": p}]})["net"], 94.55)  # 5200 / 55
        sales.save_product(db, {"name": "Laptop", "price": 100, "price_currency": "EGP"}, p)
        self.assertEqual(sales.get_product(db, p)["price_currency"], "")                 # the company's own currency is blank
        sales.save_product(db, {"name": "Laptop", "price": 100, "price_currency": "USD"}, p)
        db2 = Database(":memory:")
        db2.set_setting("fx_currencies", "USD,EUR")
        interchange.import_data(db2, interchange.export_data(db), mode="replace")
        self.assertEqual(db2.one("SELECT price_currency FROM products WHERE name='Laptop'")["price_currency"], "USD")

    def test_the_month_end_setting_goes_through_the_server(self):
        db = self.db
        auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        app = App()
        app.db, app.backup, app.backups = db, (lambda *a, **k: None), (lambda: [])
        tok = auth.login(db, "boss", "pw123456")[0]
        h = {"x-session": tok, "x-requested-with": "BeyondERP", "content-type": "application/json"}
        r = server.handle(app, "PUT", "/api/settings", h, json.dumps({"fx_reval_auto": True}).encode())
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(db.setting("fx_reval_auto"), "1")
        r = server.handle(app, "GET", "/api/fx", h, b"")
        d = json.loads(r.body)
        self.assertTrue(d["reval_auto"])
        r = server.handle(app, "GET", "/api/bootstrap", h, b"")
        self.assertEqual(r.status, 200)
        b = json.loads(r.body)["fx"]
        self.assertTrue(b["reval_auto"])
        self.assertIn("reval_due", b)


if __name__ == "__main__":
    unittest.main()
