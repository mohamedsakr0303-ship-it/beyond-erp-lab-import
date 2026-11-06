"""E-invoicing in the books: credit and debit notes (their accounting, VAT and limits), the tax-authority document they
make (type, references, templates), what the BeyondERP Signer did recorded on the invoices (signed, submitted,
statuses, cancellation), received e-invoices (fetched, read, booked, rejected) — and a whole run with the real Signer."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "signer"))
from erp import accounting as acc, auth, einvoice, server  # noqa: E402
from erp.db import Database  # noqa: E402


def books():
    db = Database(":memory:")
    for k, v in (("company_tax_id", "999888777"), ("company_name", "Sample Company"), ("eta_activity_code", "4100"),
                 ("eta_governate", "Cairo"), ("eta_region", "Nasr City"), ("eta_street", "Demo St"), ("eta_building", "12"),
                 ("eta_default_item_code", "EG-999888777-1")):
        db.set_setting(k, v)
    db.x("INSERT INTO business_units(code,name,customer) VALUES ('P1','Villa','Delta Tech')")
    db.x("INSERT INTO parties(name,type,category,receiver_type,tax_id,governate,region,street,building) VALUES "
         "('Delta Tech','customer','customer','B','100200300','Cairo','Maadi','Road 9','4')")
    return db


def invoice(db, number="INV-1", amount=10000, **kw):
    xid = acc.save_extract(db, {"bu_code": "P1", "number": number, "date": "2026-11-01", "customer": "Delta Tech", "vat_code": "VAT14",
                                "lines": [{"description": "Fit-out works", "qty": 1, "price": amount, "unit": "EA"}], **kw})
    return xid


def ar(db):
    return acc.party_balance(db, db.acct("ar"), "Delta Tech")


class Notes(unittest.TestCase):
    def test_a_credit_note_mirrors_the_invoice_and_cannot_take_back_more(self):
        db = books()
        inv = invoice(db)
        with self.assertRaisesRegex(acc.ValidationError, "draft"):
            invoice(db, "CN-1", 4000, doc_type="C", ref_id=inv, eta_reason="Price agreed lower")
        acc.post_extract(db, inv)
        self.assertEqual(round(ar(db), 2), -11400.0 if ar(db) < 0 else 11400.0)
        sign = 1 if ar(db) > 0 else -1
        with self.assertRaisesRegex(acc.ValidationError, "reason"):
            invoice(db, "CN-1", 4000, doc_type="C", ref_id=inv)
        with self.assertRaisesRegex(acc.ValidationError, "must name the invoice"):
            invoice(db, "CN-1", 4000, doc_type="C", eta_reason="x")
        cn = invoice(db, "CN-1", 4000, doc_type="C", ref_number="INV-1", eta_reason="Price agreed lower")
        eid = acc.post_extract(db, cn)
        e = acc.get_entry(db, eid)
        self.assertEqual(e["kind"], "Credit Note")
        lines = {(ln["account"], ln["debit"], ln["credit"]) for ln in e["lines"]}
        self.assertIn((db.acct("revenue"), 4000.0, 0.0), lines)
        self.assertIn((db.acct("vat_output"), 560.0, 0.0), lines)
        self.assertIn((db.acct("ar"), 0.0, 4560.0), lines)
        self.assertEqual(round(sign * ar(db), 2), 6840.0)                              # the customer owes 11,400 − 4,560
        inc = acc.income_statement(db)
        self.assertEqual(inc["net_sales"], 6000.0)
        tax = acc.tax_summary(db)
        sales = [b for b in tax["vat_by_code"] if b["side"] == "sales"][0]
        self.assertEqual((sales["net"], sales["vat"], sales["documents"]), (6000.0, 840.0, 2))     # the note counts against sales
        self.assertEqual(tax["output_vat"], 840.0)
        self.assertTrue(all(c["ok"] for c in acc.audit(db)))
        self.assertEqual(acc.credit_left(db, inv), 6840.0)
        too_much = invoice(db, "CN-2", 7000, doc_type="C", ref_id=inv, eta_reason="More")
        with self.assertRaisesRegex(acc.ValidationError, "more than the invoice still holds"):
            acc.post_extract(db, too_much)
        with self.assertRaisesRegex(acc.ValidationError, "customer"):
            invoice(db, "CN-3", 100, doc_type="C", ref_id=inv, eta_reason="x", customer="Someone Else")
        with self.assertRaisesRegex(acc.ValidationError, "not another note"):
            invoice(db, "CN-4", 100, doc_type="C", ref_id=cn, eta_reason="x")

    def test_a_debit_note_adds_to_the_invoice(self):
        db = books()
        inv = invoice(db)
        acc.post_extract(db, inv)
        dn = invoice(db, "DN-1", 1000, doc_type="D", ref_id=inv, eta_reason="Extra works agreed")
        e = acc.get_entry(db, acc.post_extract(db, dn))
        self.assertEqual(e["kind"], "Debit Note")
        self.assertIn("INV-1", e["memo"])
        self.assertEqual(round(abs(ar(db)), 2), 12540.0)
        self.assertEqual(acc.income_statement(db)["net_sales"], 11000.0)


class Documents(unittest.TestCase):
    def test_a_note_names_the_invoice_it_corrects(self):
        db = books()
        inv = invoice(db)
        acc.post_extract(db, inv)
        doc, problems = einvoice.build_document(db, inv)
        self.assertEqual((doc["documentType"], problems), ("I", []))
        self.assertNotIn("references", doc)
        cn = invoice(db, "CN-1", 4000, doc_type="C", ref_id=inv, eta_reason="Lower price")
        acc.post_extract(db, cn)
        doc, problems = einvoice.build_document(db, cn)
        self.assertEqual(doc["documentType"], "C")
        self.assertTrue(any("original invoice INV-1" in p for p in problems))
        db.x("UPDATE extracts SET eta_uuid='UUID-INV-1', eta_status='Valid' WHERE id=?", (inv,))
        doc, problems = einvoice.build_document(db, cn)
        self.assertEqual((doc["references"], problems), (["UUID-INV-1"], []))
        self.assertEqual(doc["totalAmount"], 4560.0)

    def test_templates_by_kind_and_customer(self):
        db = books()
        einvoice.save_template(db, {"code": "STD", "name": "Standard invoice", "doc_type": "I", "activity_code": "4100"})
        einvoice.save_template(db, {"code": "DELTA", "name": "Delta Tech (their PO)", "doc_type": "I", "customers": "Delta Tech\nAtlas",
                                    "activity_code": "4330", "branch_id": "2", "po_ref": "PO-7781", "so_desc": "Contract 2026/14"})
        with self.assertRaisesRegex(acc.ValidationError, "4 digits"):
            einvoice.save_template(db, {"code": "X", "name": "x", "activity_code": "12"})
        with self.assertRaisesRegex(acc.ValidationError, "already exists"):
            einvoice.save_template(db, {"code": "STD", "name": "again"})
        inv = invoice(db)
        acc.post_extract(db, inv)
        doc, _ = einvoice.build_document(db, inv)
        self.assertEqual((doc["taxpayerActivityCode"], doc["issuer"]["address"]["branchID"], doc["purchaseOrderReference"],
                          doc["salesOrderDescription"]), ("4330", "2", "PO-7781", "Contract 2026/14"))       # the customer's own
        db.x("UPDATE extracts SET einv_template='STD' WHERE id=?", (inv,))
        doc, _ = einvoice.build_document(db, inv)
        self.assertEqual((doc["taxpayerActivityCode"], doc["purchaseOrderReference"]), ("4100", ""))       # chosen on the invoice
        with self.assertRaisesRegex(acc.ValidationError, "follow this template"):
            einvoice.delete_template(db, "STD")
        einvoice.save_template(db, {"code": "STANDARD", "name": "Standard invoice", "doc_type": "I"}, "STD")    # renamed: invoices follow
        self.assertEqual(db.one("SELECT einv_template FROM extracts WHERE id=?", (inv,))["einv_template"], "STANDARD")
        einvoice.delete_template(db, "DELTA")


class Records(unittest.TestCase):
    def test_signed_submitted_valid_invalid_cancelled(self):
        db = books()
        a, b = invoice(db, "INV-1"), invoice(db, "INV-2", 500)
        for x in (a, b):
            acc.post_extract(db, x)
        prep = einvoice.prepare(db, [a, b])
        self.assertEqual([p["problems"] for p in prep], [[], []])
        self.assertEqual(prep[0]["serialized"], einvoice.serialize(prep[0]["document"]))
        signed = [{**p["document"], "signatures": [{"signatureType": "I", "value": "SIG" + p["number"]}]} for p in prep]
        einvoice.record_signed(db, [signed[0]])
        self.assertEqual(db.one("SELECT eta_status FROM extracts WHERE id=?", (a,))["eta_status"], "Signed")
        r = einvoice.record_submission(db, {"submissionId": "S1", "acceptedDocuments": [{"uuid": "U1", "longId": "L1", "internalId": "INV-1"}],
                                            "rejectedDocuments": [{"internalId": "INV-2", "error": {"message": "Bad item code"}}]}, signed)
        self.assertEqual([x["status"] for x in r["results"]], ["Submitted", "Rejected"])
        x = db.one("SELECT * FROM extracts WHERE id=?", (a,))
        self.assertEqual((x["eta_uuid"], x["eta_long_id"], x["eta_submission"]), ("U1", "L1", "S1"))
        self.assertEqual(json.loads(x["eta_signed"])["signatures"][0]["value"], "SIGINV-1")
        self.assertIn("Bad item code", db.one("SELECT eta_error FROM extracts WHERE id=?", (b,))["eta_error"])
        self.assertTrue(any("Already with the tax authority" in p for p in einvoice.prepare(db, [a])[0]["problems"]))
        self.assertEqual(einvoice.prepare(db, [b])[0]["problems"], [])                        # a rejected one can go again
        st = einvoice.record_status(db, a, {"status": "Invalid", "validationResults": {"validationSteps": [
            {"name": "Item code", "status": "Invalid", "error": {"innerError": [{"error": "Item code not registered"}]}}]}})
        self.assertIn("Item code not registered", st["error"])
        einvoice.record_status(db, a, {"status": "Valid", "longId": "L2"})
        self.assertEqual(db.one("SELECT eta_status, eta_long_id FROM extracts WHERE id=?", (a,))["eta_status"], "Valid")
        with self.assertRaises(acc.ValidationError):
            einvoice.record_state(db, a, "cancelled", "")
        self.assertEqual(einvoice.record_state(db, a, "cancelled", "Issued to the wrong branch")["status"], "Cancel requested")


class Received(unittest.TestCase):
    ROW = {"uuid": "R-1", "internalId": "S-77", "typeName": "i", "issuerId": "200300400", "issuerName": "Supplier Co",
           "dateTimeIssued": "2026-11-02T09:00:00Z", "dateTimeReceived": "2026-11-02T09:05:00Z", "netAmount": 0, "total": 570.0, "status": "Valid"}
    DOC = {"internalID": "S-77", "documentType": "I", "netAmount": 500.0, "totalAmount": 570.0,
           "taxTotals": [{"taxType": "T1", "amount": 70.0}], "invoiceLines": [{"description": "Cement", "netTotal": 500.0}]}

    def test_fetch_read_book_reject(self):
        db = books()
        einvoice.save_received(db, [self.ROW])
        einvoice.save_received(db, [self.ROW])                                              # fetched again: no duplicate
        self.assertEqual(len(einvoice.received_list(db)), 1)
        with self.assertRaisesRegex(acc.ValidationError, "full document"):
            einvoice.record_received(db, "R-1", {})
        r = einvoice.save_received_raw(db, "R-1", {"document": json.dumps(self.DOC)})
        self.assertEqual((r["net"], r["vat"], r["total"]), (500.0, 70.0, 570.0))
        self.assertEqual(r["document"]["invoiceLines"][0]["description"], "Cement")
        out = einvoice.record_received(db, "R-1", {"account": db.acct("other_overhead")})
        e = acc.get_entry(db, out["entry_id"])
        self.assertEqual(e["kind"], "Operating Expense")
        self.assertEqual(round(acc.party_balance(db, db.acct("ap"), "Supplier Co"), 2), 570.0)     # owed, with VAT
        self.assertIn((db.acct("vat_input"), 70.0), {(ln["account"], ln["debit"]) for ln in e["lines"]})
        self.assertEqual(db.one("SELECT tax_id FROM parties WHERE name='Supplier Co'")["tax_id"], "200300400")
        with self.assertRaisesRegex(acc.ValidationError, "already in the books"):
            einvoice.record_received(db, "R-1", {})
        with self.assertRaisesRegex(acc.ValidationError, "Void that entry"):
            einvoice.received_state(db, "R-1", "rejected", "Not ordered")
        einvoice.save_received(db, [{**self.ROW, "uuid": "R-2", "internalId": "S-78"}])
        einvoice.received_state(db, "R-2", "rejected", "Not ordered")
        einvoice.save_received(db, [{**self.ROW, "uuid": "R-2", "status": "Valid"}])          # a later fetch keeps the rejection
        self.assertEqual(einvoice.received_doc(db, "R-2")["status"], "Rejected")
        with self.assertRaisesRegex(acc.ValidationError, "rejected"):
            einvoice.record_received(db, "R-2", {})
        self.assertTrue(all(c["ok"] for c in acc.audit(db)))


class Exchange(unittest.TestCase):
    """Backups, the Excel workbook and industry templates carry the notes, the templates and the received documents."""

    def test_export_import_round_trip(self):
        from erp import interchange
        db = books()
        einvoice.save_template(db, {"code": "RET", "name": "Returns", "doc_type": "C", "reason": "Goods returned", "customers": "Delta Tech"})
        inv = invoice(db)
        acc.post_extract(db, inv)
        db.x("UPDATE extracts SET eta_uuid='U-1', eta_status='Valid', eta_long_id='L-1' WHERE id=?", (inv,))
        cn = invoice(db, "CN-1", 4000, doc_type="C", ref_number="INV-1", eta_reason="Goods returned", einv_template="RET")
        acc.post_extract(db, cn)
        einvoice.save_received(db, [{"uuid": "R-1", "internalId": "S-77", "typeName": "I", "issuerId": "123456789", "issuerName": "Supplier Co",
                                     "dateTimeIssued": "2026-11-02T10:00:00Z", "netAmount": 1000, "total": 1140, "status": "Valid"}])
        data = interchange.read_json(interchange.to_json(db).encode())
        self.assertEqual([r["doc_type"] for r in data["Sales Invoices"]], ["", "Credit Note"])
        dst = Database(":memory:")
        interchange.import_data(dst, data, mode="replace", user="t")
        self.assertEqual(round(ar(dst), 2), round(ar(db), 2))
        x = dst.one("SELECT * FROM extracts WHERE number='CN-1'")
        self.assertEqual((x["doc_type"], x["eta_reason"], x["einv_template"], x["status"]), ("C", "Goods returned", "RET", "posted"))
        self.assertEqual(x["ref_id"], dst.one("SELECT id FROM extracts WHERE number='INV-1'")["id"])
        self.assertEqual(dst.one("SELECT kind FROM journal_entries WHERE id=?", (x["entry_id"],))["kind"], "Credit Note")
        self.assertEqual(dst.one("SELECT eta_uuid, eta_status FROM extracts WHERE number='INV-1'"), {"eta_uuid": "U-1", "eta_status": "Valid"})
        t = dst.one("SELECT * FROM einv_templates WHERE code='RET'")
        self.assertEqual((t["doc_type"], t["customers"], t["reason"]), ("C", "Delta Tech", "Goods returned"))
        r = dst.one("SELECT * FROM einv_received WHERE uuid='R-1'")
        self.assertEqual((r["issuer_name"], r["total"], r["status"]), ("Supplier Co", 114000, "Valid"))

    def test_industry_templates_add_generic_e_invoice_templates(self):
        from erp import industries
        db = Database(":memory:")
        r = industries.apply(db, "general")
        self.assertEqual(sorted(r["einv_templates_added"]), ["CN-DISC", "CN-RET", "DN-ADJ", "INV"])
        self.assertFalse(any(t["activity_code"] or t["item_code"] for t in db.q("SELECT * FROM einv_templates")))   # the company's own codes
        self.assertEqual(industries.apply(db, "contracting")["einv_templates_added"], [])


class Api(unittest.TestCase):
    def test_routes_and_permissions(self):
        db = books()
        auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        clerk = auth.create_user(db, {"username": "clerk", "password": "pw123456"})
        db.x("UPDATE users SET perms=? WHERE id=?", (json.dumps({"tax.einvoice": "view", "sales.invoices": "view"}), clerk))
        tok, _ = auth.login(db, "boss", "pw123456")
        tok_c, _ = auth.login(db, "clerk", "pw123456")
        app = type("App", (), {})()
        app.db, app.backup = db, (lambda *a, **k: None)

        def call(m, p, b=None, t=tok):
            r = server.handle(app, m, p, {"x-session": t, "x-requested-with": "BeyondERP", "content-type": "application/json"}, json.dumps(b or {}).encode())
            return r.status, json.loads(r.body or b"{}") if r.ctype.startswith("application/json") else r.body
        st, r = call("POST", "/api/extracts", {"bu_code": "P1", "number": "INV-9", "date": "2026-11-01", "customer": "Delta Tech", "vat_code": "VAT14",
                                               "lines": [{"description": "Works", "qty": 1, "price": 1000}], "post": True})
        self.assertEqual(st, 200, r)
        xid = r["id"]
        st, r = call("POST", "/api/einvoice/prepare", {"ids": [xid]})
        self.assertEqual((st, r["documents"][0]["problems"]), (200, []))
        self.assertEqual(call("POST", "/api/einvoice/prepare", {"ids": [xid]}, tok_c)[0], 403)          # view only
        doc = {**r["documents"][0]["document"], "signatures": [{"signatureType": "I", "value": "SIG"}]}
        st, r = call("POST", "/api/einvoice/record-submission", {"submission": {"submissionId": "S", "acceptedDocuments": [{"uuid": "U9", "internalId": "INV-9"}]},
                                                                  "documents": [doc]})
        self.assertEqual(r["results"][0]["status"], "Submitted")
        st, r = call("GET", f"/api/einvoice/{xid}/signed")
        self.assertEqual(r["document"]["signatures"][0]["value"], "SIG")
        self.assertEqual(call("POST", f"/api/einvoice/{xid}/record-status", {"details": {"status": "Valid"}}, tok_c)[1]["status"], "Valid")
        st, r = call("POST", f"/api/extracts/{xid}/reopen", {})
        self.assertEqual(st, 400)                                                                      # with the tax authority: cancel first
        self.assertEqual(call("POST", f"/api/einvoice/{xid}/record-cancel", {"reason": "x"}, tok_c)[0], 403)
        st, r = call("POST", "/api/einvoice-templates", {"code": "T1", "name": "Standard", "doc_type": "I"})
        self.assertEqual(st, 200, r)
        self.assertEqual(call("GET", "/api/einvoice-templates", t=tok_c)[1]["rows"][0]["code"], "T1")
        st, r = call("POST", "/api/einvoice-received", {"rows": [Received.ROW]})
        self.assertEqual(r["saved"], 1)
        self.assertEqual(call("GET", "/api/einvoice-received/R-1", t=tok_c)[1]["issuer_name"], "Supplier Co")
        call("POST", "/api/einvoice-received/R-1/raw", {"raw": {"document": json.dumps(Received.DOC)}})
        st, r = call("POST", "/api/einvoice-received/R-1/book", {})
        self.assertEqual(st, 200, r)
        st, r = call("PUT", "/api/einvoice/settings", {"eta_connection": "nope"})
        self.assertEqual(st, 400)
        st, r = call("PUT", "/api/einvoice/settings", {"eta_connection": "direct", "eta_signer_key": "k3y"})
        self.assertEqual((r["eta_connection"], r["eta_signer_key"]), ("direct", "••••••••"))         # the key is not shown back


class WithTheRealSigner(unittest.TestCase):
    """The books prepare, the Signer signs with a certificate, the books keep it, the Signer checks it again."""

    def test_whole_run(self):
        try:
            import beyond_signer as bs
            from cryptography.hazmat.primitives.asymmetric import rsa
            rsa.generate_private_key(public_exponent=65537, key_size=1024)
            try:
                from tests.test_signer import make_cert
            except ImportError:
                from test_signer import make_cert
        except Exception:  # noqa: BLE001
            self.skipTest("the Signer's libraries are not installed")
        db = books()
        inv = invoice(db)
        acc.post_extract(db, inv)
        tmp = tempfile.mkdtemp()
        pfx, _, _ = make_cert(tmp)
        cfg = bs.Config(str(Path(tmp) / "s.json"))
        cfg.update({"mode": "pfx", "pfx_path": str(pfx)})
        signer = bs.App(cfg)
        signer.keys.unlock("secret-pw")
        prep = einvoice.prepare(db, [inv])[0]
        signed = signer.route("POST", "/v1/sign", {}, {"documents": [prep["document"]]})["documents"]
        einvoice.record_signed(db, signed)
        kept = json.loads(db.one("SELECT eta_signed FROM extracts WHERE id=?", (inv,))["eta_signed"])
        self.assertTrue(signer.route("POST", "/v1/verify", {}, {"document": kept})["valid"])
        kept["totalAmount"] = 1
        self.assertFalse(signer.route("POST", "/v1/verify", {}, {"document": kept})["valid"])


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "tests"))
    unittest.main()
