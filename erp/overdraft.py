"""A bank account with an overdraft facility (تسهيلات على الحساب الجاري): the bank lets the balance go below zero, up to an
agreed limit, and charges interest on what is drawn.

The account stays one account — payments and receipts go through it as for any bank — and three settings on it
(accounts.od_on, od_limit, od_rate) say what a negative balance is:
* it is money borrowed from the bank, not money paid out before it came in: the books' checks no longer ask where it
  came from (only going over the limit is flagged), and the balance sheet shows it as a bank overdraft under current
  liabilities instead of negative cash (cash flow keeps it in cash and cash equivalents, as IAS 7 allows for an
  overdraft repayable on demand that is part of day-to-day cash management);
* the unused part of the limit is money the company can spend: the money position and the cash forecast count it;
* the interest the bank will charge is estimated day by day from the balances and the rate; the bank's own figure is
  booked with one entry (interest expense / the bank), once per month and account.
An account without a limit is a facility whose limit is not recorded: nothing is counted as room and nothing is
flagged as over it.
"""
import calendar
import datetime as _dt

from . import accounting as acc
from .db import to_major, to_minor
from .accounting import ValidationError, num


def settings_from(d, sub, old=None):
    """The facility settings an account save carries: (on, limit in minor units, yearly rate %). A setting left out of
    the save keeps its value; an account that is not cash / bank never has one."""
    old = old or {}
    if sub != "cash":
        return 0, 0, 0.0
    on = d["od_on"] if "od_on" in d else old.get("od_on", 0)
    if on in (0, "0", False, "false", "", None, "No", "no", "لا"):
        return 0, 0, 0.0
    lim = (to_minor(num(d["od_limit"], "overdraft limit")) if d["od_limit"] not in (None, "") else 0) if "od_limit" in d else (old.get("od_limit") or 0)
    rate = (float(num(d["od_rate"], "overdraft interest rate")) if d["od_rate"] not in (None, "") else 0.0) if "od_rate" in d else (old.get("od_rate") or 0.0)
    if lim < 0:
        raise ValidationError("The overdraft limit cannot be negative")
    if not 0 <= rate <= 100:
        raise ValidationError("The overdraft interest rate is a yearly percentage between 0 and 100")
    return 1, lim, rate


def accounts(db):
    return db.q("SELECT code, name, name_ar, od_limit, od_rate FROM accounts WHERE od_on=1 AND subtype='cash' ORDER BY code")


def is_overdraft(db, code):
    return bool(db.one("SELECT 1 FROM accounts WHERE code=? AND od_on=1 AND subtype='cash'", (code,)))


def _days(db, code, date_to=None):
    """The account's balance at the end of each day it moved (minor units)."""
    w = [acc.LIVE, "l.account=?"]
    p = [code]
    if date_to:
        w.append("e.date<=?")
        p.append(date_to)
    out, run = [], 0
    for r in db.q(f"""SELECT e.date d, SUM(l.debit-l.credit) n FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id
                      WHERE {' AND '.join(w)} GROUP BY e.date ORDER BY e.date""", p):
        run += r["n"]
        out.append((r["d"], run))
    return out


def interest_estimate(db, code, date_from, date_to):
    """Interest on the overdrawn balance at the end of each day from date_from to date_to, at the account's yearly rate
    (actual / 365): what the bank is likely to charge for the period (minor units)."""
    a = db.one("SELECT od_rate FROM accounts WHERE code=?", (code,))
    rate = (a or {}).get("od_rate") or 0
    if not rate:
        return 0
    days = _days(db, code, date_to)
    d0, d1 = _dt.date.fromisoformat(date_from), _dt.date.fromisoformat(date_to)
    bal, i, total = 0, 0, 0.0
    while i < len(days) and days[i][0] < date_from:
        bal = days[i][1]
        i += 1
    day = d0
    while day <= d1:
        while i < len(days) and days[i][0] == day.isoformat():
            bal = days[i][1]
            i += 1
        if bal < 0:
            total += -bal * rate / 100 / 365
        day += _dt.timedelta(days=1)
    return int(round(total))


def _month(month):
    y, m = int(month[:4]), int(month[5:7])
    return f"{month[:7]}-01", f"{month[:7]}-{calendar.monthrange(y, m)[1]:02d}"


def facilities(db, date_to=None):
    """Each bank account with an overdraft facility: its balance, what is drawn, the room left and this month's interest
    estimate (amounts in the company's currency)."""
    date_to = acc.clean_date(date_to) if date_to else acc.today()
    m0, m1 = _month(date_to)
    out = []
    for a in accounts(db):
        days = _days(db, a["code"], date_to)
        bal = days[-1][1] if days else 0
        drawn = max(-bal, 0)
        peak, peak_on = 0, ""
        for d, b in days:
            if -b > peak:
                peak, peak_on = -b, d
        lim = a["od_limit"] or 0
        state = "ok"
        if lim and drawn > lim:
            state = "over"
        elif lim and drawn >= 0.9 * lim:
            state = "near"
        booked = db.one(f"""SELECT 1 FROM journal_entries e WHERE {acc.LIVE} AND e.kind='Bank Interest' AND e.ref=?""",
                        (f"OD:{a['code']}:{date_to[:7]}",))
        out.append({"code": a["code"], "name": a["name"], "name_ar": a["name_ar"] or "", "balance": to_major(bal),
                    "drawn": to_major(drawn), "limit": to_major(lim), "room": to_major(max(lim - drawn, 0)) if lim else None,
                    "rate": a["od_rate"] or 0, "state": state, "peak": to_major(peak), "peak_on": peak_on,
                    "month": date_to[:7], "interest_est": to_major(interest_estimate(db, a["code"], m0, min(m1, date_to))),
                    "interest_booked": bool(booked)})
    return out


def room(db):
    """The unused part of the recorded limits: money the company can still spend (company currency)."""
    return round(sum(f["room"] or 0 for f in facilities(db)), 2)


def book_interest(db, d, user=""):
    """The bank's interest for a month on an overdraft facility: interest expense / the bank account, dated the month's
    last day (or the date given), once per month and account."""
    code = acc.text(d.get("account"))
    if not is_overdraft(db, code):
        raise ValidationError("Choose a bank account with an overdraft facility")
    month = acc.text(d.get("month"))[:7]
    try:
        m0, m1 = _month(month)
    except (ValueError, IndexError):
        raise ValidationError("Say which month (YYYY-MM)") from None
    amount = num(d.get("amount"), "interest amount", allow_zero=False)
    date = acc.clean_date(d.get("date") or min(m1, acc.today()))
    ref = f"OD:{code}:{month}"
    if db.one(f"SELECT 1 FROM journal_entries e WHERE {acc.LIVE} AND e.kind='Bank Interest' AND e.ref=?", (ref,)):
        raise ValidationError(f"The interest for {month} on {code} is already booked")
    exp = acc.account_code(db, d.get("expense_account") or db.acct("interest"), types=("Expense",), field="interest account")
    name = db.one("SELECT name FROM accounts WHERE code=?", (code,))["name"]
    with db.tx():
        eid = acc._insert_entry(db, date=date, kind="Bank Interest", memo=acc.text(d.get("memo")) or f"Overdraft interest {month} — {name}",
                                ref=ref, lines=[{"account": exp, "debit": amount}, {"account": code, "credit": amount}], user=user,
                                meta={"overdraft": {"account": code, "month": month}})
    return eid
