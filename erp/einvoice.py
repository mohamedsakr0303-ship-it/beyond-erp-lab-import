"""Egyptian Tax Authority (ETA) e-invoicing.

Two ways to use it:
  1. Generate for import — ETA v1.0 document JSON (single or batch) and an Excel batch
     (Header / Lines / Taxes) for the ETA portal uploader or an integrator.
  2. Direct link — OAuth2 client-credentials login, signing through a local signing
     service (your USB token / HSM), submission, status refresh and cancellation.

Signing: ETA requires a CAdES-BES signature made with the taxpayer's e-seal/token for
documentTypeVersion "1.0". Tokens are hardware, so this app calls a *signer service* you run
next to the token (see README → E-invoicing). Contract:
    POST <signer_url>  {"serialized": "<canonical string>", "sha256": "<hex>", "document": {...}}
    ->  {"signature": "<base64 CAdES-BES>"}
Version "0.9" documents need no signature and are accepted by the pre-production system only.
"""
import datetime as _dt
import hashlib
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import accounting as acc, xlsafe

ENDPOINTS = {
    "preprod": ("https://id.preprod.eta.gov.eg", "https://api.preprod.invoicing.eta.gov.eg"),
    "prod": ("https://id.eta.gov.eg", "https://api.invoicing.eta.gov.eg"),
}
# app units -> ETA unit codes (UN/ECE Rec 20 as used by ETA)
UNIT_CODES = {"pcs": "EA", "عدد": "EA", "ea": "EA", "task": "EA", "lump sum": "EA", "مقطوعية": "EA",
              "m2": "MTK", "م٢": "MTK", "m²": "MTK", "sqm": "MTK", "m.t": "MTR", "ml": "MTR", "م.ط": "MTR",
              "m": "MTR", "kg": "KGM", "ton": "TNE", "l": "LTR", "day": "DAY", "hour": "HUR", "month": "MON"}


def _r(v, n=5):
    return round(float(v or 0), n)


def settings(db):
    s = {r["key"]: r["value"] for r in db.q("SELECT key, value FROM settings WHERE key LIKE 'eta_%' OR key LIKE 'company_%'")}
    return s


def public_settings(db):
    s = settings(db)
    s = {k: v for k, v in s.items() if k.startswith("eta_")}
    from .db import ETA_SECRETS
    for k in ETA_SECRETS:
        s[k] = "••••••••" if s.get(k) else ""
    return s


def _party(db, name):
    return db.one("SELECT * FROM parties WHERE name=?", (name,)) or {}


def build_document(db, extract_id, version=None):
    """Return (document, problems). problems is a list of human-readable blocking issues."""
    x = acc.get_extract(db, extract_id)
    if not x:
        raise acc.ValidationError("Invoice not found")
    s = settings(db)
    version = version or s.get("eta_doc_version") or "1.0"
    problems = []
    tpl = template_for(db, x)
    for k, sk in (("activity_code", "eta_activity_code"), ("branch_id", "eta_branch_id"), ("item_code", "eta_default_item_code"),
                  ("unit", "eta_default_unit")):
        if tpl and tpl.get(k):
            s[sk] = tpl[k]                                  # the template's own details win for this document
    doc_type = (x.get("doc_type") or "I").upper()
    refs = []
    if doc_type in ("C", "D"):
        orig = db.one("SELECT number, eta_uuid FROM extracts WHERE id=?", (x.get("ref_id"),)) or {}
        if not orig.get("eta_uuid"):
            problems.append(f"Send the original invoice {orig.get('number', '')} to the tax authority before this note")
        else:
            refs = [orig["eta_uuid"]]
    rin = (s.get("company_tax_id") or "").strip()
    if not re.fullmatch(r"\d{9}", rin):
        problems.append("Company tax registration number (9 digits) is missing — Settings → Company")
    if not s.get("eta_activity_code"):
        problems.append("Taxpayer activity code is missing — Settings → E-invoicing")
    for k in ("eta_governate", "eta_region", "eta_street", "eta_building"):
        if not s.get(k):
            problems.append(f"Issuer address field '{k[4:]}' is missing — Settings → E-invoicing")
    cust_name = x["customer"] or x.get("bu_customer") or x["bu_name"]
    p = _party(db, cust_name)
    rtype = p.get("receiver_type") or "B"
    receiver_id = (p.get("tax_id") if rtype == "B" else p.get("national_id")) or ""
    if not p:
        problems.append(f"Customer '{cust_name}' is not in Customers & Suppliers — add it with tax ID and address")
    elif rtype == "B" and not re.fullmatch(r"\d{9}", receiver_id or ""):
        problems.append(f"Customer '{cust_name}' needs a 9-digit tax registration number")
    elif rtype == "P" and x["gross"] >= 50000 and not receiver_id:
        problems.append("Invoices of 50,000 EGP or more to individuals need the customer's national ID")
    if x["status"] != "posted":
        problems.append("Post the invoice before issuing it as an e-invoice")
    vat = db.one("SELECT * FROM tax_codes WHERE code=?", (x["vat_code"],)) if x["vat_code"] else None
    wht = db.one("SELECT * FROM tax_codes WHERE code=?", (x["wht_code"],)) if x["wht_code"] else None
    codes = {}
    for r in db.q("SELECT item_no, description, eta_code FROM rates WHERE eta_code<>''"):
        codes[r["item_no"]] = codes[r["description"]] = r["eta_code"]
    lines, tax_totals = [], {}
    fee_lines = [{"description": f"أتعاب {f['category'] or ''} {f['pct'] * 100:g}%".strip(), "qty": 1, "pct": 1, "price": f["amount"],
                  "unit": "", "item_no": "FEE", "seq": i} for i, f in enumerate(x.get("fee_rows") or [])]
    for ln in x["lines"] + fee_lines:
        qty = ln["qty"] * (ln.get("pct") if ln.get("pct") is not None else 1)     # completion % is part of the quantity billed
        net = _r(qty * ln["price"])
        items = []
        if vat:
            amt = _r(net * vat["rate"])
            items.append({"taxType": vat["eta_type"] or "T1", "amount": amt, "subType": vat["eta_subtype"] or "V009",
                          "rate": _r(vat["rate"] * 100, 2)})
        if wht:
            amt = _r(net * wht["rate"])
            items.append({"taxType": wht["eta_type"] or "T4", "amount": amt, "subType": wht["eta_subtype"] or "W001",
                          "rate": _r(wht["rate"] * 100, 2)})
        for it in items:
            tax_totals[it["taxType"]] = _r(tax_totals.get(it["taxType"], 0) + it["amount"])
        total = _r(net + sum(it["amount"] for it in items if it["taxType"] != "T4")
                   - sum(it["amount"] for it in items if it["taxType"] == "T4"))
        code = codes.get(ln["item_no"]) or codes.get(ln["description"]) or s.get("eta_default_item_code") or ""
        if not code:
            problems.append(f"Line '{ln['description'][:40]}' has no ETA item code (price list → ETA code, "
                            "or a default in Settings → E-invoicing)")
        lines.append({
            "description": ln["description"][:500], "itemType": "GS1" if code.isdigit() else "EGS",
            "itemCode": code, "unitType": UNIT_CODES.get((ln["unit"] or "").strip().lower(),
                                                        s.get("eta_default_unit") or "EA"),
            "quantity": _r(qty, 5), "internalCode": str(ln["item_no"] or ln["seq"] + 1),
            "salesTotal": net, "total": total, "valueDifference": 0.0, "totalTaxableFees": 0.0, "netTotal": net,
            "itemsDiscount": 0.0, "unitValue": {"currencySold": "EGP", "amountEGP": _r(ln["price"])},
            "discount": {"rate": 0.0, "amount": 0.0}, "taxableItems": items})
    net_amount = _r(sum(line["netTotal"] for line in lines))
    doc = {
        "issuer": {"address": {"branchID": s.get("eta_branch_id") or "0", "country": "EG",
                               "governate": s.get("eta_governate", ""), "regionCity": s.get("eta_region", ""),
                               "street": s.get("eta_street", ""), "buildingNumber": s.get("eta_building", ""),
                               "postalCode": s.get("eta_postal_code", ""), "floor": "", "room": "", "landmark": "",
                               "additionalInformation": ""},
                   "type": "B", "id": rin, "name": s.get("company_name", "")},
        "receiver": {"address": {"country": p.get("country") or "EG", "governate": p.get("governate", ""),
                                 "regionCity": p.get("region", ""), "street": p.get("street", ""),
                                 "buildingNumber": p.get("building", ""), "postalCode": p.get("postal_code", ""),
                                 "floor": "", "room": "", "landmark": "", "additionalInformation": ""},
                     "type": rtype, "id": receiver_id, "name": cust_name},
        "documentType": doc_type, "documentTypeVersion": version,
        "dateTimeIssued": issued_at(x["date"]),
        "taxpayerActivityCode": s.get("eta_activity_code", ""), "internalID": x["number"],
        "purchaseOrderReference": (tpl or {}).get("po_ref") or "", "purchaseOrderDescription": (tpl or {}).get("po_desc") or "",
        "salesOrderReference": x.get("boq_title") or "", "salesOrderDescription": (tpl or {}).get("so_desc") or "", "proformaInvoiceNumber": "",
        "invoiceLines": lines, "totalDiscountAmount": 0.0, "totalSalesAmount": net_amount, "netAmount": net_amount,
        "taxTotals": [{"taxType": k, "amount": v} for k, v in tax_totals.items()],
        "totalAmount": _r(sum(line["total"] for line in lines)), "extraDiscountAmount": 0.0,
        "totalItemsDiscountAmount": 0.0,
    }
    if refs:
        doc["references"] = refs                            # a credit / debit note names the invoice it corrects
    # the document must say what the books say: rounding of fractional quantities can make them part
    book = {"net": x["total"], "T1": x.get("vat") or 0, "T4": x.get("wht") or 0}
    sent = {"net": net_amount, "T1": tax_totals.get((vat or {}).get("eta_type") or "T1", 0), "T4": tax_totals.get((wht or {}).get("eta_type") or "T4", 0)}
    for k, label in (("net", "net"), ("T1", "VAT"), ("T4", "withholding tax")):
        if abs(round(book[k] - sent[k], 5)) > 0.01:
            problems.append(f"The e-invoice {label} ({sent[k]:,.5f}) differs from the invoice ({book[k]:,.2f}) by rounding of "
                            "fractional quantities. Round the quantities or prices so the lines come out in whole piasters.")
    if x["date"] > acc.today():
        problems.append("The issue date cannot be in the future")
    return doc, sorted(set(problems))


def issued_at(day):
    """The issue time the tax authority expects, in UTC. Today: now (never ahead of the clock). An earlier date: 09:00 UTC,
    which is 11:00 or 12:00 in Egypt, so the document stays on its own local date (and is the same each time it is built)."""
    from .db import now as _now
    n = _now()
    if day >= n.date().isoformat():
        return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{day}T09:00:00Z"


def serialize(obj, name=None):
    """ETA canonical serialization (the string that is hashed and signed)."""
    if isinstance(obj, dict):
        out = []
        for k, v in obj.items():
            if isinstance(v, list):
                out.append(f'"{k.upper()}"' + "".join(f'"{k.upper()}"' + serialize(e) for e in v))
            else:
                out.append(f'"{k.upper()}"' + serialize(v))
        return "".join(out)
    if isinstance(obj, list):
        return "".join(serialize(e) for e in obj)
    return '"' + (json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj.replace('"', '\\"')) + '"'


def sign(db, doc):
    s = settings(db)
    if doc.get("documentTypeVersion") == "0.9":
        return doc
    url = s.get("eta_signer_url")
    if not url:
        raise acc.ValidationError("Set the signing service URL (Settings → E-invoicing) or use version 0.9 "
                                  "on pre-production for testing")
    ser = serialize({k: v for k, v in doc.items() if k != "signatures"})
    body = json.dumps({"serialized": ser, "sha256": hashlib.sha256(ser.encode("utf-8")).hexdigest(),
                       "document": doc}).encode("utf-8")
    head = {"Content-Type": "application/json"}
    if s.get("eta_signer_key"):
        head["X-Signer-Key"] = s["eta_signer_key"]          # the BeyondERP Signer's pairing key
    res = _http("POST", url, body, head)
    sig = res.get("signature") if isinstance(res, dict) else None
    if not sig:
        raise acc.ValidationError("The signing service did not return a signature")
    return {**doc, "signatures": [{"signatureType": "I", "value": sig}]}


def _http(method, url, body=None, headers=None, timeout=40):
    import sys
    if sys.platform == "emscripten":
        raise acc.ValidationError("The direct e-invoicing connection requires the desktop or self-hosted edition, as browsers block these connections. Use Generate for import (JSON / Excel) instead.")
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            detail = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            detail = raw.decode("utf-8", "replace")[:400]
        raise acc.ValidationError(f"{url.split('/')[2]} answered {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise acc.ValidationError(f"Could not reach {url.split('/')[2]}: {e}") from None
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        return {"raw": raw.decode("utf-8", "replace")}


def _token(db):
    s = settings(db)
    if not s.get("eta_client_id") or not s.get("eta_client_secret"):
        raise acc.ValidationError("Enter the ETA client ID and secret (Settings → E-invoicing)")
    id_base, _ = ENDPOINTS.get(s.get("eta_env") or "preprod", ENDPOINTS["preprod"])
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": s["eta_client_id"],
                                   "client_secret": s["eta_client_secret"], "scope": "InvoicingAPI"}).encode()
    res = _http("POST", f"{id_base}/connect/token", body, {"Content-Type": "application/x-www-form-urlencoded"})
    if not res.get("access_token"):
        raise acc.ValidationError("E-invoicing sign-in failed. Check the client ID, secret and environment.")
    return res["access_token"]


def _api_base(db):
    return ENDPOINTS.get(settings(db).get("eta_env") or "preprod", ENDPOINTS["preprod"])[1]


def test_connection(db):
    _token(db)
    return {"ok": True, "environment": settings(db).get("eta_env")}


def at_eta(x):
    """Is the invoice with the tax authority (it has a UUID and was not rejected, found invalid or cancelled)? A
    cancellation the receiver may still decline ("Cancel requested") is still with them."""
    st = str((x or {}).get("eta_status") or "").strip().lower()
    return bool((x or {}).get("eta_uuid")) and not (st.startswith(("reject", "invalid")) or st in ("cancelled", "canceled"))


def submit(db, extract_ids, user=""):
    docs, results = [], []
    for xid in extract_ids:
        x = db.one("SELECT number, eta_uuid, eta_status FROM extracts WHERE id=?", (int(xid),))
        if at_eta(x):      # sending again would make a second document at the tax authority
            raise acc.ValidationError(f"Invoice {x['number']} is already with the tax authority ({x['eta_status']}).")
        doc, problems = build_document(db, xid)
        if problems:
            raise acc.ValidationError(f"Invoice #{xid}: " + "; ".join(problems))
        docs.append((xid, sign(db, doc)))
    token = _token(db)
    res = _http("POST", f"{_api_base(db)}/api/v1/documentsubmissions",
                json.dumps({"documents": [d for _, d in docs]}, ensure_ascii=False).encode("utf-8"),
                {"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    by_internal = {d["internalID"]: xid for xid, d in docs}
    for a in res.get("acceptedDocuments", []):
        xid = by_internal.get(a.get("internalId"))
        if xid:
            db.x("UPDATE extracts SET eta_uuid=?, eta_status='Submitted', eta_submission=?, eta_error='' WHERE id=?",
                 (a.get("uuid", ""), res.get("submissionId", ""), xid))
            results.append({"id": xid, "status": "Submitted", "uuid": a.get("uuid")})
    for r in res.get("rejectedDocuments", []):
        xid = by_internal.get(r.get("internalId"))
        err = json.dumps(r.get("error", {}), ensure_ascii=False)[:1500]
        if xid:
            db.x("UPDATE extracts SET eta_status='Rejected', eta_error=? WHERE id=?", (err, xid))
            results.append({"id": xid, "status": "Rejected", "error": err})
    db.log(user, "eta.submit", json.dumps(results, ensure_ascii=False)[:500])
    return {"submission_id": res.get("submissionId"), "results": results}


def refresh_status(db, extract_id):
    x = db.one("SELECT eta_uuid FROM extracts WHERE id=?", (extract_id,))
    if not x or not x["eta_uuid"]:
        raise acc.ValidationError("This invoice has not been submitted")
    res = _http("GET", f"{_api_base(db)}/api/v1/documents/{x['eta_uuid']}/details",
                headers={"Authorization": f"Bearer {_token(db)}"})
    status = res.get("status") or res.get("documentStatus") or "Unknown"
    errs = json.dumps(res.get("validationResults", {}), ensure_ascii=False)[:1500] if status == "Invalid" else ""
    db.x("UPDATE extracts SET eta_status=?, eta_error=? WHERE id=?", (status, errs, extract_id))
    return {"status": status, "error": errs}


def cancel(db, extract_id, reason):
    x = db.one("SELECT eta_uuid FROM extracts WHERE id=?", (extract_id,))
    if not x or not x["eta_uuid"]:
        raise acc.ValidationError("This invoice has not been submitted")
    _http("PUT", f"{_api_base(db)}/api/v1/documents/state/{x['eta_uuid']}/state",
          json.dumps({"status": "cancelled", "reason": acc.text(reason, "reason", required=True)}).encode(),
          {"Content-Type": "application/json", "Authorization": f"Bearer {_token(db)}"})
    db.x("UPDATE extracts SET eta_status='Cancel requested' WHERE id=?", (extract_id,))
    return {"status": "Cancel requested"}



# ---------------------------------------------------------------- templates
TEMPLATE_FIELDS = ("name", "doc_type", "customers", "reason", "activity_code", "branch_id", "item_code", "unit", "po_ref", "po_desc",
                   "so_desc", "notes", "active")


def templates(db, active_only=False):
    return db.q("SELECT * FROM einv_templates" + (" WHERE active=1" if active_only else "") + " ORDER BY doc_type, name")


def save_template(db, d, code=None):
    new_code = acc.text(d.get("code") or code, "code", required=True, maxlen=40)
    name = acc.text(d.get("name"), "name", required=True, maxlen=120)
    dt = str(d.get("doc_type") or "I").upper()[:1]
    if dt not in ("I", "C", "D"):
        raise acc.ValidationError("The document is an invoice, a credit note or a debit note")
    act = acc.text(d.get("activity_code"), maxlen=10)
    if act and not re.fullmatch(r"\d{4}", act):
        raise acc.ValidationError("The activity code has 4 digits")
    custs = "\n".join(sorted({c.strip() for c in re.split(r"[\n;]", str(d.get("customers") or "")) if c.strip()}))
    vals = (name, dt, custs, acc.text(d.get("reason"), maxlen=500), act, acc.text(d.get("branch_id"), maxlen=20),
            acc.text(d.get("item_code"), maxlen=100), acc.text(d.get("unit"), maxlen=10), acc.text(d.get("po_ref"), maxlen=100),
            acc.text(d.get("po_desc"), maxlen=500), acc.text(d.get("so_desc"), maxlen=500), acc.text(d.get("notes"), maxlen=1000),
            0 if d.get("active") in (0, False, "0", "No", "no") else 1)
    with db.tx():
        if code and code != new_code and db.one("SELECT 1 FROM einv_templates WHERE code=?", (new_code,)):
            raise acc.ValidationError(f"A template with the code {new_code} already exists")
        if code and db.one("SELECT 1 FROM einv_templates WHERE code=?", (code,)):
            db.conn.execute("UPDATE einv_templates SET code=?, name=?, doc_type=?, customers=?, reason=?, activity_code=?, branch_id=?, item_code=?,"
                            " unit=?, po_ref=?, po_desc=?, so_desc=?, notes=?, active=? WHERE code=?", (new_code, *vals, code))
            db.conn.execute("UPDATE extracts SET einv_template=? WHERE einv_template=?", (new_code, code))
        else:
            if db.one("SELECT 1 FROM einv_templates WHERE code=?", (new_code,)):
                raise acc.ValidationError(f"A template with the code {new_code} already exists")
            db.conn.execute("INSERT INTO einv_templates(code, name, doc_type, customers, reason, activity_code, branch_id, item_code, unit,"
                            " po_ref, po_desc, so_desc, notes, active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (new_code, *vals))
    return db.one("SELECT * FROM einv_templates WHERE code=?", (new_code,))


def delete_template(db, code):
    n = db.one("SELECT COUNT(*) n FROM extracts WHERE einv_template=?", (code,))["n"]
    if n:
        raise acc.ValidationError(f"{n} invoice(s) follow this template. Switch it off instead.")
    db.x("DELETE FROM einv_templates WHERE code=?", (code,))


def template_for(db, x):
    """The template an invoice follows: the one chosen on it, else the first active one of its kind for its customer,
    else the first active one of its kind for any customer."""
    if x.get("einv_template"):
        t = db.one("SELECT * FROM einv_templates WHERE code=?", (x["einv_template"],))
        if t:
            return t
    cust = (x.get("customer") or x.get("bu_customer") or "").strip()
    dt = (x.get("doc_type") or "I").upper()
    rows = [t for t in templates(db, True) if t["doc_type"] == dt]
    for t in rows:
        if cust and cust in (t["customers"] or "").split("\n"):
            return t
    return next((t for t in rows if not (t["customers"] or "").strip()), None)


# ---------------------------------------------------------------- what the BeyondERP Signer did, recorded in the books
def prepare(db, extract_ids):
    """Documents ready for the Signer: each built from the books, with what still stops it from being sent."""
    out = []
    for xid in extract_ids:
        x = db.one("SELECT id, number, eta_status, eta_uuid FROM extracts WHERE id=?", (int(xid),))
        if not x:
            raise acc.ValidationError(f"Invoice #{xid} not found")
        doc, problems = build_document(db, int(xid))
        st = (x["eta_status"] or "").lower()
        if at_eta(x):
            problems = problems + [f"Already with the tax authority ({x['eta_status']})"]
        out.append({"id": x["id"], "number": x["number"], "document": doc, "problems": problems, "serialized": serialize(doc)})
    return out


def _by_number(db, internal_id):
    return db.one("SELECT id FROM extracts WHERE number=?", (acc.text(internal_id),))


def _signed_ok(doc):
    sig = ((doc or {}).get("signatures") or [{}])[0].get("value") if isinstance(doc, dict) else None
    return bool(sig) or (doc or {}).get("documentTypeVersion") == "0.9"


def record_signed(db, documents, user=""):
    """Signed documents kept with their invoices (signed now, sent later — or to check later)."""
    done = []
    for d in documents or []:
        x = _by_number(db, d.get("internalID"))
        if not x or not _signed_ok(d):
            continue
        db.x("UPDATE extracts SET eta_signed=?, eta_signed_at=?, eta_status=CASE WHEN COALESCE(eta_uuid,'')='' THEN 'Signed' ELSE eta_status END "
             "WHERE id=?", (json.dumps(d, ensure_ascii=False), acc.now_iso(), x["id"]))
        done.append(x["id"])
    db.log(user, "eta.sign", ",".join(map(str, done)))
    return {"signed": done}


def record_submission(db, submission, documents=None, user=""):
    """The tax authority's answer to a submission (through the Signer): accepted documents get their UUID, rejected
    ones their reason; the signed documents are kept with their invoices."""
    if documents:
        record_signed(db, documents, user)
    res, now = [], acc.now_iso()
    sub = submission or {}
    for a in sub.get("acceptedDocuments") or []:
        x = _by_number(db, a.get("internalId"))
        cur = db.one("SELECT eta_uuid, eta_status FROM extracts WHERE id=?", (x["id"],)) if x else None
        if x and at_eta(cur) and cur["eta_uuid"] != a.get("uuid"):
            # accepted twice: the first one stays the invoice's; the second must be cancelled on the portal
            db.x("UPDATE extracts SET eta_error=? WHERE id=?",
                 (f"Also accepted as {acc.text(a.get('uuid'), maxlen=60)} — cancel that duplicate on the portal", x["id"]))
            res.append({"id": x["id"], "status": "Duplicate", "uuid": a.get("uuid"), "kept": cur["eta_uuid"]})
            continue
        if x:
            db.x("UPDATE extracts SET eta_uuid=?, eta_long_id=?, eta_status='Submitted', eta_submission=?, eta_error='', eta_submitted_at=? WHERE id=?",
                 (acc.text(a.get("uuid"), maxlen=60), acc.text(a.get("longId"), maxlen=120), acc.text(sub.get("submissionId"), maxlen=60), now, x["id"]))
            res.append({"id": x["id"], "status": "Submitted", "uuid": a.get("uuid")})
    for r in sub.get("rejectedDocuments") or []:
        x = _by_number(db, r.get("internalId"))
        if x and at_eta(db.one("SELECT eta_uuid, eta_status FROM extracts WHERE id=?", (x["id"],))):
            continue          # a rejected repeat never undoes the accepted document
        if x:
            err = json.dumps(r.get("error", {}), ensure_ascii=False)[:1500]
            db.x("UPDATE extracts SET eta_status='Rejected', eta_error=? WHERE id=?", (err, x["id"]))
            res.append({"id": x["id"], "status": "Rejected", "error": err})
    db.log(user, "eta.submit", json.dumps(res, ensure_ascii=False)[:500])
    return {"submission_id": sub.get("submissionId"), "results": res}


def _errors(details):
    vr = (details or {}).get("validationResults") or {}
    msgs = []
    for step in vr.get("validationSteps") or []:
        if str(step.get("status", "")).lower() == "invalid":
            err = step.get("error") or {}
            for e in (err.get("innerError") or [err]):
                m = e.get("error") or e.get("errorCode") or e.get("message")
                if m:
                    msgs.append(f"{step.get('name', '')}: {m}".strip(": "))
    return "; ".join(msgs)[:1500]


def record_status(db, extract_id, details):
    """The status the tax authority gives a document now (Submitted, Valid, Invalid, Rejected, Cancelled)."""
    x = db.one("SELECT id, eta_uuid FROM extracts WHERE id=?", (int(extract_id),))
    if not x or not x["eta_uuid"]:
        raise acc.ValidationError("This invoice has not been submitted")
    status = acc.text((details or {}).get("status") or (details or {}).get("documentStatus") or "Unknown", maxlen=40)
    err = _errors(details) if status.lower() == "invalid" else ""
    long_id = acc.text((details or {}).get("longId"), maxlen=120)
    db.x("UPDATE extracts SET eta_status=?, eta_error=?, eta_checked_at=?, eta_long_id=CASE WHEN ?<>'' THEN ? ELSE eta_long_id END WHERE id=?",
         (status, err, acc.now_iso(), long_id, long_id, x["id"]))
    return {"status": status, "error": err}


def record_state(db, extract_id, status, reason, user=""):
    """A cancellation asked of the tax authority (it is final once the receiver does not object in time)."""
    x = db.one("SELECT id, eta_uuid FROM extracts WHERE id=?", (int(extract_id),))
    if not x or not x["eta_uuid"]:
        raise acc.ValidationError("This invoice has not been submitted")
    if status != "cancelled":
        raise acc.ValidationError("Only an issued invoice can be cancelled")
    db.x("UPDATE extracts SET eta_status='Cancel requested', eta_error=? WHERE id=?", ("Cancelled: " + acc.text(reason, "reason", required=True, maxlen=500), x["id"]))
    db.log(user, "eta.cancel", str(x["id"]))
    return {"status": "Cancel requested"}


# ---------------------------------------------------------------- received documents
def _minor(v):
    try:
        return acc.to_minor(float(v or 0))
    except (TypeError, ValueError):
        return 0


def save_received(db, rows):
    """Documents other companies issued to this one (the tax authority's list), added or brought up to date."""
    n = 0
    with db.tx():
        for r in rows or []:
            uuid = acc.text(r.get("uuid"), maxlen=60)
            if not uuid:
                continue
            vals = (acc.text(r.get("internalId") or r.get("internalID"), maxlen=60), acc.text(r.get("typeName") or r.get("documentType") or "I", maxlen=4).upper()[:1],
                    acc.text(r.get("issuerId"), maxlen=30), acc.text(r.get("issuerName"), maxlen=200),
                    acc.text(r.get("dateTimeIssued"), maxlen=30), acc.text(r.get("dateTimeReceived"), maxlen=30),
                    _minor(r.get("totalSales") if r.get("netAmount") is None else r.get("netAmount")),
                    0, _minor(r.get("total")), acc.text(r.get("status"), maxlen=40), acc.now_iso())   # VAT comes with the full document
            if db.one("SELECT 1 FROM einv_received WHERE uuid=?", (uuid,)):
                db.conn.execute("UPDATE einv_received SET internal_id=?, doc_type=?, issuer_id=?, issuer_name=?, issued=?, received=?, net=?, "
                                "vat=CASE WHEN ?>0 THEN ? ELSE vat END, total=?, "
                                "status=CASE WHEN status='Rejected' THEN status ELSE ? END, fetched_at=? WHERE uuid=?",
                                (*vals[:7], vals[7], vals[7], *vals[8:], uuid))
            else:
                db.conn.execute("INSERT INTO einv_received(internal_id, doc_type, issuer_id, issuer_name, issued, received, net, vat, total, status, "
                                "fetched_at, uuid) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (*vals, uuid))
            n += 1
    return {"saved": n}


def received_list(db, q=None):
    rows = db.q("SELECT r.*, e.kind AS entry_kind FROM einv_received r LEFT JOIN journal_entries e ON e.id=r.entry_id ORDER BY r.issued DESC, r.uuid")
    out = []
    for r in rows:
        if q and q.lower() not in f"{r['issuer_name']} {r['issuer_id']} {r['internal_id']}".lower():
            continue
        r.pop("raw", None)
        for k in ("net", "vat", "total"):
            r[k] = acc.to_major(r[k])
        out.append(r)
    return out


def received_doc(db, uuid):
    r = db.one("SELECT * FROM einv_received WHERE uuid=?", (uuid,))
    if not r:
        raise acc.ValidationError("Not found")
    for k in ("net", "vat", "total"):
        r[k] = acc.to_major(r[k])
    try:
        raw = json.loads(r["raw"] or "{}")
        r["document"] = json.loads(raw["document"]) if isinstance(raw.get("document"), str) else (raw.get("document") or raw or None)
    except (ValueError, TypeError):
        r["document"] = None
    r.pop("raw", None)
    return r


def save_received_raw(db, uuid, raw):
    """The full document (lines and taxes) of a received e-invoice; its totals are taken from it."""
    if not db.one("SELECT 1 FROM einv_received WHERE uuid=?", (uuid,)):
        raise acc.ValidationError("Not found")
    doc = raw.get("document") if isinstance(raw, dict) else None
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except ValueError:
            doc = None
    sets = {"raw": json.dumps(raw, ensure_ascii=False)[:2_000_000]}
    if isinstance(doc, dict):
        vat = sum(float(t.get("amount") or 0) for t in doc.get("taxTotals") or [] if t.get("taxType") == "T1")
        sets.update(net=_minor(doc.get("netAmount")), vat=_minor(vat), total=_minor(doc.get("totalAmount")),
                    doc_type=str(doc.get("documentType") or "I").upper()[:1], internal_id=acc.text(doc.get("internalID"), maxlen=60))
    db.x("UPDATE einv_received SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE uuid=?", (*sets.values(), uuid))
    return received_doc(db, uuid)


def received_state(db, uuid, status, reason, user=""):
    if status != "rejected":
        raise acc.ValidationError("Only a received e-invoice can be rejected")
    r = db.one("SELECT entry_id FROM einv_received WHERE uuid=?", (uuid,))
    if not r:
        raise acc.ValidationError("Not found")
    if r["entry_id"]:
        raise acc.ValidationError("It is recorded in the books. Void that entry before rejecting the document.")
    db.x("UPDATE einv_received SET status='Rejected', reason=? WHERE uuid=?", (acc.text(reason, "reason", required=True, maxlen=500), uuid))
    db.log(user, "eta.reject", uuid)
    return received_doc(db, uuid)


def record_received(db, uuid, d, user=""):
    """Book a received e-invoice: a direct cost on a project, or an overhead expense — owed to the supplier, with the
    input VAT the document carries."""
    r = received_doc(db, uuid)
    if r["entry_id"] and db.one("SELECT 1 FROM journal_entries WHERE id=? AND status='posted'", (r["entry_id"],)):
        raise acc.ValidationError("This document is already in the books")
    if (r["status"] or "").lower() in ("rejected", "cancelled", "invalid"):
        raise acc.ValidationError(f"The document is {r['status'].lower()}; it is not recorded")
    if r["doc_type"] not in ("I", ""):
        raise acc.ValidationError("Record credit and debit notes from suppliers as a supplier discount or a cost by hand")
    net = r["net"]
    if not r.get("document") or net <= 0:
        raise acc.ValidationError("Fetch the full document first (its amounts and VAT are not known yet)")
    party = acc.text(d.get("party")) or r["issuer_name"]
    if party and not db.one("SELECT 1 FROM parties WHERE name=?", (party,)):
        db.x("INSERT INTO parties(name, type, category, tax_id) VALUES (?,?,?,?)", (party, "supplier", "supplier", r["issuer_id"]))
    vat_code = d.get("vat_code") or (db.one("SELECT code FROM tax_codes WHERE kind='vat' AND rate>0 ORDER BY code LIMIT 1") or {}).get("code") if r["vat"] else d.get("vat_code") or ""
    memo = acc.text(d.get("description")) or f"E-invoice {r['internal_id']} from {r['issuer_name']}"
    common = {"date": (r["issued"] or acc.today())[:10], "vendor": party, "description": memo, "vat_code": vat_code, "paid": 0, "deal_ref": r["internal_id"]}
    if d.get("bu_id") or d.get("bu_code"):
        eid = acc.record_project_cost(db, {**common, "bu_id": d.get("bu_id"), "bu_code": d.get("bu_code"), "account": d.get("account"),
                                           "qty": 1, "rate": net}, user)
    else:
        eid = acc.record_expense(db, {**common, "account": d.get("account"), "amount": net}, user)
    db.x("UPDATE einv_received SET entry_id=? WHERE uuid=?", (eid, uuid))
    return {"entry_id": eid, **received_doc(db, uuid)}


def direct_received(db, page=1, size=50):
    """The direct connection (desktop / self-hosted server): the tax authority's list of documents received."""
    q = urllib.parse.urlencode({"pageNo": int(page), "pageSize": min(int(size), 100), "direction": "Received"})
    res = _http("GET", f"{_api_base(db)}/api/v1/documents/recent?{q}", headers={"Authorization": f"Bearer {_token(db)}"})
    save_received(db, res.get("result") or [])
    return {"saved": len(res.get("result") or []), "metadata": res.get("metadata") or {}}


# ---------------------------------------------------------------- batch export
def batch(db, extract_ids, signed=False):
    docs, problems = [], {}
    for xid in extract_ids:
        doc, pr = build_document(db, xid)
        if pr:
            problems[doc["internalID"]] = pr
        docs.append(sign(db, doc) if signed and not pr else doc)
    return docs, problems


def batch_json(db, extract_ids, signed=False):
    docs, problems = batch(db, extract_ids, signed)
    return json.dumps({"documents": docs, "_problems": problems} if problems else {"documents": docs},
                      ensure_ascii=False, indent=2).encode("utf-8")


def batch_xlsx(db, extract_ids):
    """Excel batch: Header, Lines and Taxes sheets, one row per document / line / tax item."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        raise acc.ValidationError("Excel export needs openpyxl:  python -m pip install openpyxl") from None
    docs, problems = batch(db, extract_ids)
    wb = Workbook()
    head = wb.active
    head.title = "Header"
    hcols = ["internalID", "documentType", "documentTypeVersion", "dateTimeIssued", "taxpayerActivityCode",
             "issuer.id", "issuer.name", "issuer.branchID", "receiver.type", "receiver.id", "receiver.name",
             "receiver.country", "receiver.governate", "receiver.regionCity", "receiver.street",
             "receiver.buildingNumber", "totalSalesAmount", "totalDiscountAmount", "netAmount", "T1 (VAT)",
             "T4 (WHT)", "totalAmount", "problems"]
    lines = wb.create_sheet("Lines")
    lcols = ["internalID", "line", "description", "itemType", "itemCode", "unitType", "quantity",
             "unitValue.amountEGP", "salesTotal", "discount.amount", "netTotal", "total", "internalCode"]
    taxes = wb.create_sheet("Taxes")
    tcols = ["internalID", "line", "taxType", "subType", "rate", "amount"]
    for ws, cols in ((head, hcols), (lines, lcols), (taxes, tcols)):
        ws.append(cols)
        for c in ws[1]:
            c.font = Font(name="Arial", bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F3A5F")
    for d in docs:
        tt = {t["taxType"]: t["amount"] for t in d["taxTotals"]}
        r, i = d["receiver"], d["issuer"]
        head.append([d["internalID"], d["documentType"], d["documentTypeVersion"], d["dateTimeIssued"],
                     d["taxpayerActivityCode"], i["id"], i["name"], i["address"]["branchID"], r["type"], r["id"],
                     r["name"], r["address"]["country"], r["address"]["governate"], r["address"]["regionCity"],
                     r["address"]["street"], r["address"]["buildingNumber"], d["totalSalesAmount"],
                     d["totalDiscountAmount"], d["netAmount"], tt.get("T1", 0), tt.get("T4", 0), d["totalAmount"],
                     "; ".join(problems.get(d["internalID"], []))])
        for n, ln in enumerate(d["invoiceLines"], start=1):
            lines.append([d["internalID"], n, ln["description"], ln["itemType"], ln["itemCode"], ln["unitType"],
                          ln["quantity"], ln["unitValue"]["amountEGP"], ln["salesTotal"], ln["discount"]["amount"],
                          ln["netTotal"], ln["total"], ln["internalCode"]])
            for t in ln["taxableItems"]:
                taxes.append([d["internalID"], n, t["taxType"], t["subType"], t["rate"], t["amount"]])
    for ws in (head, lines, taxes):
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or "")) for c in col) + 2, 60)
    buf = io.BytesIO()
    xlsafe.no_formulas(wb)
    wb.save(buf)
    return buf.getvalue()
