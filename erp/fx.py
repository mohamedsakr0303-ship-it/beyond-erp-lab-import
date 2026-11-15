"""Foreign currencies.

The books stay in the company's currency: every journal line's debit / credit is in it, so every balance, report and
total works as before. A line in a foreign currency also keeps that currency, its amount in it and the rate used
(journal_lines.currency / fx_amount / fx_rate).

Entering foreign amounts: a line gives "currency" and "fx_debit" or "fx_credit" (the foreign amount), with an optional
"rate"; or the entry gives a currency (and rate) for all its lines. Lines that already carry currency details next to
ordinary debit / credit (a copy, a reversal, an edit of a posted entry) are taken as they are — never converted twice.

Valuing: money coming into a foreign balance (a bank account in USD, a customer's USD invoices) is valued at the day's
rate; money leaving it, at the rate it is carried at (what that balance cost in the company's currency, on average).
The difference is the exchange gain or loss, posted to the exchange-differences account in the same entry. At a month
end, open foreign balances can be revalued at that day's rate (reversed on the 1st): see revalue().
"""
import datetime as _dt
import re

from . import accounting as acc
from .db import to_major, to_minor

# balances held or owed in money (IAS 21 monetary items): valued at the carried rate when they go down, revalued at month end
MONETARY = {"cash", "receivable", "payable", "accrued", "short_term_debt", "long_term_debt", "other_current_liability",
            "other_noncurrent_liability", "other_current_asset", "other_noncurrent_asset", "dividends_payable"}
# taxes are declared and settled in the company's currency: their lines are converted but never kept in a foreign one
TAX_ROLES = {"vat_input", "vat_output", "wht_receivable", "wht_payable"}
CODE = re.compile(r"^[A-Z]{3}$")
# the currencies offered first (any three-letter code is accepted)
COMMON = ["USD", "EUR", "GBP", "SAR", "AED", "KWD", "QAR", "BHD", "OMR", "JOD", "CNY", "TRY", "CHF", "JPY", "CAD", "AUD", "EGP"]


def base(db):
    return (db.setting("currency") or "EGP").strip().upper() or "EGP"


def enabled(db):
    """The foreign currencies this company works in (Settings → Currencies)."""
    b = base(db)
    return [c for c in dict.fromkeys(x.strip().upper() for x in (db.setting("fx_currencies") or "").split(",")) if CODE.match(c) and c != b]


def code(c):
    c = str(c or "").strip().upper()
    if c and not CODE.match(c):
        raise acc.ValidationError(f"{c} is not a currency code (three letters, e.g. USD)")
    return c


# ------------------------------------------------------------------ rates
def rate_on(db, cur, date):
    """Company currency per 1 unit of `cur`: the last rate on or before `date`."""
    cur = code(cur)
    if not cur or cur == base(db):
        return 1.0
    r = db.one("SELECT rate FROM fx_rates WHERE currency=? AND date<=? ORDER BY date DESC LIMIT 1", (cur, acc.clean_date(date)))
    if not r:
        raise acc.ValidationError(f"No exchange rate for {cur} on or before {acc.clean_date(date)}. Add one under Exchange rates "
                                  f"(or let the app fetch today's).")
    return float(r["rate"])


def set_rate(db, date, cur, rate, source="manual"):
    """One rate. A rate entered by hand or imported is never replaced by a fetched one for the same day."""
    cur, date = code(cur), acc.clean_date(date)
    if not cur or cur == base(db):
        raise acc.ValidationError("Choose a foreign currency (not the company's own)")
    try:
        rate = float(str(rate).replace(",", ""))
    except (TypeError, ValueError):
        raise acc.ValidationError(f"The rate for {cur} must be a number") from None
    if not 0 < rate < 1e7:
        raise acc.ValidationError(f"The rate for {cur} must be more than zero")
    with db.tx():
        _put_rate(db, date, cur, rate, source)


def _put_rate(db, date, cur, rate, source):
    if source == "auto":
        db.x("INSERT INTO fx_rates(date, currency, rate, source) VALUES (?,?,?,?) "
             "ON CONFLICT(date, currency) DO UPDATE SET rate=excluded.rate WHERE fx_rates.source='auto'", (date, cur, rate, source))
    else:
        db.x("INSERT OR REPLACE INTO fx_rates(date, currency, rate, source) VALUES (?,?,?,?)", (date, cur, rate, source))


def rates(db, cur=None, limit=400):
    w, p = ("WHERE currency=?", (code(cur),)) if cur else ("", ())
    return db.q(f"SELECT date, currency, rate, source FROM fx_rates {w} ORDER BY date DESC, currency LIMIT ?", (*p, int(limit)))


# ------------------------------------------------------------------ posting
def diff_account(db):
    """The exchange-differences account: the one with that role, else the chart's 7040 "Foreign Exchange Loss / (Gain)",
    else a new one."""
    r = db.one("SELECT code FROM accounts WHERE role='fx_diff'")
    if r:
        return r["code"]
    r = db.one("SELECT code FROM accounts WHERE code='7040' AND type='Expense' AND (role IS NULL OR role='')")
    if r:
        db.conn.execute("UPDATE accounts SET role='fx_diff' WHERE code='7040'")
        return "7040"
    c = 7040
    while db.one("SELECT 1 FROM accounts WHERE code=?", (str(c),)):
        c += 1
    db.conn.execute("INSERT INTO accounts(code, name, name_ar, type, subtype, normal, role) VALUES (?,?,?,?,?,?,?)",
                    (str(c), "Foreign Exchange Loss / (Gain)", "خسائر (أرباح) فروق العملة", "Expense", "other_nonoperating", "Debit", "fx_diff"))
    return str(c)


def carried(db, account, party, cur, date, exclude_eid=None):
    """(foreign balance, company-currency balance) of one account / party in one currency up to `date`, debit positive.
    party None: the whole account (bank and cash hold one balance, whoever the money came from)."""
    ex = "AND e.id<>?" if exclude_eid else ""
    if party is None:
        r = db.one(f"""SELECT COALESCE(SUM(CASE WHEN l.debit>0 THEN l.fx_amount ELSE -l.fx_amount END),0) fx,
                              COALESCE(SUM(l.debit-l.credit),0) home
                       FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                       WHERE {acc.LIVE} AND l.account=? AND l.currency=? AND e.date<=? {ex}""",
                   (account, cur, date, *((exclude_eid,) if exclude_eid else ())))
        return int(r["fx"]), int(r["home"])
    r = db.one(f"""SELECT COALESCE(SUM(CASE WHEN l.debit>0 THEN l.fx_amount ELSE -l.fx_amount END),0) fx,
                          COALESCE(SUM(l.debit-l.credit),0) home
                   FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                   WHERE {acc.LIVE} AND l.account=? AND l.currency=? AND e.date<=? {ex}
                     AND COALESCE(NULLIF(l.party,''), e.party, '')=?""",
               (account, cur, date, *((exclude_eid,) if exclude_eid else ()), party or ""))
    return int(r["fx"]), int(r["home"])


def convert(db, date, lines, party="", currency=None, rate=None, exclude_eid=None):
    """Lines as entered → lines in the company's currency with their foreign details, plus the exchange difference.
    Lines with no foreign amount pass through unchanged."""
    b = base(db)
    cur_e = code(currency) if currency else ""
    if cur_e == b:
        cur_e = ""
    rate_e = None
    if cur_e and rate not in (None, "", 0, "0"):
        rate_e = _rate_value(rate, cur_e)
    accts = {r["code"]: r for r in db.q("SELECT code, subtype, currency, role FROM accounts")}
    out, any_fx, diff_minor, rounding = [], False, 0, 0
    running = {}                                       # foreign balances as this entry moves them (several lines, one balance)
    for ln in lines:
        ln = dict(ln)
        if ln.pop("_home", None):                      # already in the company's currency (a stock cost, a balance held…)
            out.append(ln)
            continue
        a = accts.get(acc.text(ln.get("account")))
        a_cur = (a or {}).get("currency") or ""
        fxd, fxc = ln.pop("fx_debit", None), ln.pop("fx_credit", None)
        cur = code(ln.get("currency")) if ln.get("currency") else ""
        if fxd in (None, "", 0, "0") and fxc in (None, "", 0, "0") and cur_e and not cur and not ln.get("fx_amount"):
            fxd, fxc, cur = ln.pop("debit", None), ln.pop("credit", None), cur_e      # the entry is in a foreign currency
        if cur == b:
            cur = ""
        if fxd in (None, "", 0, "0") and fxc in (None, "", 0, "0"):
            # in the company's currency (or already converted): only a currency account's own currency may go there
            if a_cur and (ln.get("currency") or "") != a_cur and (ln.get("debit") or ln.get("credit")):
                raise acc.ValidationError(f"Account {a['code']} is kept in {a_cur}: enter its line in {a_cur}")
            if ln.get("currency"):
                any_fx = True
            out.append(ln)
            continue
        cur = cur or a_cur or cur_e                    # a foreign amount on a USD account is in USD
        if not cur:
            raise acc.ValidationError("A line has a foreign amount but no currency")
        if a_cur and a_cur != cur:
            raise acc.ValidationError(f"Account {a['code']} is kept in {a_cur}, not {cur}")
        amount = to_minor(acc.num(fxd if fxd not in (None, "", 0, "0") else fxc))
        if amount <= 0:
            raise acc.ValidationError("A foreign amount must be more than zero")
        is_debit = fxd not in (None, "", 0, "0")
        r = _rate_value(ln.pop("rate"), cur) if ln.get("rate") not in (None, "", 0, "0") else \
            (rate_e if (rate_e and cur == cur_e) else rate_on(db, cur, date))
        ln.pop("rate", None)
        at_rate = round(amount * r)
        home = at_rate
        if a and not a_cur and (a["subtype"] in ("cash", "tax_payable") or (a.get("role") or "") in TAX_ROLES):
            # a bank / cash account in the company's currency, or a tax: the money moved is that currency, at the day's rate
            out.append({**ln, "debit": to_major(home) if is_debit else 0, "credit": 0 if is_debit else to_major(home)})
            any_fx = True
            continue
        if a and (a_cur or a["subtype"] in MONETARY):
            who = None if a["subtype"] == "cash" else (acc.text(ln.get("party")) or party or "")
            key = (a["code"], who, cur)
            if key not in running:
                running[key] = carried(db, a["code"], who, cur, acc.clean_date(date), exclude_eid)
            bal_fx, bal_home = running[key]
            move = amount if is_debit else -amount
            if bal_fx and (bal_fx > 0) != (move > 0):        # it reduces a balance: valued at the rate it is carried at
                part = min(amount, abs(bal_fx))
                home = round(abs(bal_home) * part / abs(bal_fx)) + round((amount - part) * r)
            running[key] = (bal_fx + move, bal_home + (home if is_debit else -home))
        diff_minor += (home - at_rate) * (1 if is_debit else -1)
        any_fx = True
        out.append({**ln, "debit": to_major(home) if is_debit else 0, "credit": 0 if is_debit else to_major(home),
                    "currency": cur, "fx_amount": to_major(amount), "fx_rate": r})
    if not any_fx:
        return out
    # what keeps the entry from balancing: the exchange difference of balances valued at their carried rate, plus each
    # line's rounding to the piastre — never anything else (a real mistake still refuses to post)
    dr = sum(to_minor(acc.num(x.get("debit"))) for x in out)
    cr = sum(to_minor(acc.num(x.get("credit"))) for x in out)
    gap = dr - cr
    fx_lines = sum(1 for x in out if x.get("currency"))
    if gap and abs(gap - diff_minor) <= fx_lines:
        da = diff_account(db)
        out.append({"account": da, "debit": to_major(-gap) if gap < 0 else 0, "credit": to_major(gap) if gap > 0 else 0,
                    "memo": "Exchange difference", "party": party or ""})
    return out


def _rate_value(r, cur):
    try:
        v = float(str(r).replace(",", ""))
    except (TypeError, ValueError):
        raise acc.ValidationError(f"The rate for {cur} must be a number") from None
    if not 0 < v < 1e7:
        raise acc.ValidationError(f"The rate for {cur} must be more than zero")
    return v


# ------------------------------------------------------------------ month-end revaluation
def open_balances(db, date):
    """Every foreign balance still open on `date` on money accounts (cash / bank, receivables, payables, and accounts
    kept in a currency): [{account, name, party, currency, fx, home, rate, revalued, difference}] (company currency)."""
    rows = db.q(f"""SELECT l.account, CASE WHEN a.subtype='cash' THEN '' ELSE COALESCE(NULLIF(l.party,''), e.party, '') END AS who, l.currency,
                           SUM(CASE WHEN l.debit>0 THEN l.fx_amount ELSE -l.fx_amount END) fx, SUM(l.debit-l.credit) home
                    FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id JOIN accounts a ON a.code=l.account
                    WHERE {acc.LIVE} AND l.currency<>'' AND e.date<=? AND (a.currency<>'' OR a.subtype IN ({','.join('?' * len(MONETARY))}))
                    GROUP BY l.account, who, l.currency HAVING fx<>0 ORDER BY l.account, who, l.currency""",
               (acc.clean_date(date), *sorted(MONETARY)))
    names = {r["code"]: r["name"] for r in db.q("SELECT code, name FROM accounts")}
    out = []
    for r in rows:
        rate = rate_on(db, r["currency"], date)
        target = round(r["fx"] * rate)
        out.append({"account": r["account"], "name": names.get(r["account"], ""), "party": r["who"], "currency": r["currency"],
                    "fx": to_major(r["fx"]), "home": to_major(r["home"]), "rate": rate, "revalued": to_major(target),
                    "difference": to_major(target - r["home"])})
    return out


def revalue(db, date, user="", preview=False):
    """Revalue every open foreign balance at `date`'s rate: one entry dated `date` (each balance up or down, against the
    exchange-differences account), reversed on the next day so the next month starts from what the balances cost.
    preview=True only lists what it would post."""
    date = acc.clean_date(date)
    ref = f"FXR:{date}"
    done = db.one("SELECT id FROM journal_entries WHERE kind='FX Revaluation' AND ref=? AND status='posted'", (ref,))
    if done and not preview:
        raise acc.ValidationError(f"Foreign balances were already revalued on {date} (entry #{done['id']}).")
    rows = [r for r in open_balances(db, date) if abs(r["difference"]) >= 0.01]
    if preview or not rows:
        return {"date": date, "rows": rows, "entry": None, "reversal": None}
    da = diff_account(db)
    lines = []
    for r in rows:
        d = r["difference"]
        lines.append({"account": r["account"], "party": r["party"], "debit": d if d > 0 else 0, "credit": -d if d < 0 else 0,
                      "currency": r["currency"], "fx_amount": 0, "fx_rate": r["rate"],
                      "memo": f"{r['currency']} {r['fx']:,.2f} at {r['rate']:g}"})
        lines.append({"account": da, "debit": -d if d < 0 else 0, "credit": d if d > 0 else 0, "party": r["party"],
                      "memo": f"Revaluation {r['currency']} — {r['account']}"})
    nxt = (_dt.date.fromisoformat(date) + _dt.timedelta(days=1)).isoformat()
    with db.tx():                                   # the revaluation and its reversal go in together, or not at all
        done = db.one("SELECT id FROM journal_entries WHERE kind='FX Revaluation' AND ref=? AND status='posted'", (ref,))
        if done:
            raise acc.ValidationError(f"Foreign balances were already revalued on {date} (entry #{done['id']}).")
        eid = acc._insert_entry(db, date=date, kind="FX Revaluation", memo=f"Revaluation of foreign balances at {date}",
                                ref=ref, lines=lines, meta={"fx_revaluation": date}, user=user or "system")
        rid = acc.reverse_entry(db, eid, nxt, user=user or "system")
    return {"date": date, "rows": rows, "entry": eid, "reversal": rid}


# ------------------------------------------------------------------ rates fetched each day
FETCH_HOST = "open.er-api.com"          # free, no key; quotes per 1 unit of the company currency


def parse_fetched(db, payload):
    """A rates service's answer {"result": "success", "base_code": "EGP", "rates": {"USD": 0.0198, …}} → {cur: company
    currency per 1 unit} for this company's foreign currencies."""
    if not isinstance(payload, dict) or payload.get("result") not in (None, "success"):
        raise acc.ValidationError("The rates service did not send rates")
    b = base(db)
    if str(payload.get("base_code") or b).upper() != b:
        raise acc.ValidationError(f"The rates service answered for {payload.get('base_code')}, not {b}")
    rates_ = payload.get("rates") or {}
    out = {}
    for c in enabled(db):
        v = rates_.get(c)
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out[c] = round(1 / v, 6)
    return out


def store_fetched(db, payload, date=None):
    got = parse_fetched(db, payload)
    day = acc.clean_date(date or acc.today())
    for c, r in got.items():
        set_rate(db, day, c, r, "auto")
    db.set_setting("fx_fetched_on", day)
    return {"date": day, "rates": got}


def fetch_now(db):
    """Desktop app: today's rates from the service (the browser edition fetches them in the page and sends them in)."""
    import json as _json
    import urllib.request
    url = f"https://{FETCH_HOST}/v6/latest/{base(db)}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "BeyondERP"}), timeout=15) as r:
            payload = _json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise acc.ValidationError(f"Could not fetch today's rates ({e}). Enter them by hand, or try again later.") from None
    return store_fetched(db, payload)


# ------------------------------------------------------------------ amounts priced in the company's currency, owed in another
def party_currency(db, party):
    cur = (db.one("SELECT currency FROM parties WHERE name=?", (party or "",)) or {}).get("currency") or "" \
        or (db.one("SELECT currency FROM care_patients WHERE name=? AND currency<>''", (party or "",)) or {}).get("currency") or ""
    return "" if cur == base(db) or cur not in enabled(db) else cur


def owed_line(db, line, party, date):
    """A receivable / payable line priced in the company's currency for a contact who deals in a foreign one (their
    usual currency): it keeps its value and is owed in that currency, at the day's rate."""
    cur = party_currency(db, party)
    amt = acc.num(line.get("debit") or line.get("credit") or 0)
    if not cur or not amt:
        return line
    r = rate_on(db, cur, date)
    return {**line, "currency": cur, "fx_amount": to_major(round(to_minor(amt) / r)), "fx_rate": r}


def settle_line(db, account, party, home_minor, date, side="credit"):
    """Clearing part of what a contact owes on an account kept in their currency: the same share of the foreign
    balance it clears at home, so nothing is left behind in either currency."""
    cur = party_currency(db, party)
    line = {"account": account, side: to_major(home_minor), "party": party}
    if not cur or not home_minor:
        return line
    fxb, homeb = carried(db, account, party, cur, acc.clean_date(date))
    if not homeb:
        return owed_line(db, line, party, date)
    part = round(abs(fxb) * home_minor / abs(homeb))
    return {**line, "currency": cur, "fx_amount": to_major(part), "fx_rate": round(home_minor / part, 6) if part else 0}
