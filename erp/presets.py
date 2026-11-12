"""Saved fill-ins: a form's values saved under a name, to fill it in again with one tap ("Office rent", "Diesel for
the site truck", "Monthly internet"). Company-wide, per form, most recently used first; anyone who may add on that form
can save, use and delete them. Dates and attached files are never kept — the rest is the form's own fields, sent back
as they were saved (the form still checks everything when it is posted).

Stored in the setting "form_presets": {form key: [{id, name, values, by, used_at}]}; the workbook carries them in the
"Saved Fill-ins" sheet (one row each), not in Settings, so a large set never meets Excel's cell limit."""
import json
import re

from . import accounting as acc
from .accounting import ValidationError, text

MAX_PER_FORM = 50
MAX_VALUES = 30000         # characters of saved values per fill-in: the same text the workbook cell holds (at most 32,767)
KEY_RE = re.compile(r"^[a-z_]+:[a-z_]{1,40}$")
def _now():
    from .db import now
    return now().isoformat()        # the app's clock: the same on every device a change is replayed on


SKIP = {"date", "_files", "files", "attachments", "allow_duplicate", "issued_on", "due"}


def load(db):
    try:
        d = json.loads(db.setting("form_presets") or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _store(db, d):
    db.set_setting("form_presets", json.dumps({k: v for k, v in d.items() if v}, ensure_ascii=False))


def for_form(db, key):
    return sorted(load(db).get(key, []), key=lambda p: p.get("used_at", ""), reverse=True)


def save(db, key, name, values, user=""):
    if not KEY_RE.match(key or ""):
        raise ValidationError("This form is not recognised")
    name = text(name, "name", required=True, maxlen=80)
    if not isinstance(values, dict):
        raise ValidationError("Nothing to save")
    line_ok = lambda v: isinstance(v, list) and len(v) <= 100 and all(
        isinstance(x, dict) and all(isinstance(y, (str, int, float, bool)) or y is None for y in x.values()) for x in v)
    vals = {k: v for k, v in values.items() if k not in SKIP and isinstance(k, str)
            and ((isinstance(v, (str, int, float, bool)) and str(v).strip() != "") or (k == "lines" and line_ok(v) and v))}
    if not vals:
        raise ValidationError("Fill in something first")
    if len(json.dumps(vals, ensure_ascii=False)) > MAX_VALUES:
        raise ValidationError("Too much to save as one fill-in")
    d = load(db)
    lst = d.get(key, [])
    same = next((p for p in lst if p["name"].strip().lower() == name.lower()), None)
    now = _now()
    if same:
        same.update(values=vals, by=user, used_at=now)
        p = same
    else:
        if len(lst) >= MAX_PER_FORM:
            raise ValidationError(f"At most {MAX_PER_FORM} fill-ins per form: delete one you no longer use")
        n = max([int(x["id"]) for x in lst if str(x.get("id", "")).isdigit()] + [0]) + 1
        p = {"id": str(n), "name": name, "values": vals, "by": user, "used_at": now}
        lst.append(p)
    d[key] = lst
    _store(db, d)
    return p


def touch(db, key, pid):
    d = load(db)
    for p in d.get(key, []):
        if p["id"] == str(pid):
            p["used_at"] = _now()
            _store(db, d)
            return p
    raise ValidationError("That fill-in was deleted")


def delete(db, key, pid):
    d = load(db)
    lst = d.get(key, [])
    if not any(p["id"] == str(pid) for p in lst):
        raise ValidationError("That fill-in was already deleted")
    d[key] = [p for p in lst if p["id"] != str(pid)]
    _store(db, d)


# ----------------------------------------------------------------- the workbook
def sheet_rows(db):
    return [{"form": k, "name": p["name"], "values": json.dumps(p["values"], ensure_ascii=False), "by": p.get("by", ""),
             "used_at": p.get("used_at", "")} for k, lst in sorted(load(db).items()) for p in lst]


def import_sheet(db, rows):
    """Each row replaces the fill-in of the same form and name; others stay."""
    d, n = load(db), 0
    for r in rows or []:
        key, name = text(r.get("form")), text(r.get("name"), maxlen=80)
        if not KEY_RE.match(key) or not name:
            continue
        try:
            vals = json.loads(r.get("values") or "{}")
        except ValueError:
            raise ValidationError(f"Saved fill-in “{name}”: the values are not readable")
        if not isinstance(vals, dict):
            continue
        lst = d.setdefault(key, [])
        same = next((p for p in lst if p["name"].strip().lower() == name.lower()), None)
        if same:
            same.update(values=vals, used_at=text(r.get("used_at")) or same.get("used_at", ""))
        elif len(lst) < MAX_PER_FORM:
            pid = str(max([int(x["id"]) for x in lst if str(x.get("id", "")).isdigit()] + [0]) + 1)
            lst.append({"id": pid, "name": name, "values": vals, "by": text(r.get("by")), "used_at": text(r.get("used_at"))})
        n += 1
    _store(db, d)
    return n
