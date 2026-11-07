"""BeyondERP Signer: CAdES-BES signatures from a certificate file and from a PKCS#11 token (SoftHSM stands in for the
USB token), checked by the Signer and independently by OpenSSL; the local service (pairing, browser access, its own
settings page) and the Tax Authority gateway against a stand-in ETA server."""
import base64
import datetime as _dt
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "signer"))

try:
    import asn1crypto  # noqa: F401
    import cryptography  # noqa: F401
    import beyond_signer as bs
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    _rsa.generate_private_key(public_exponent=65537, key_size=1024)       # the library really works here
    HAVE = True
except Exception:  # noqa: BLE001
    HAVE = False
try:
    import PyKCS11  # noqa: F401
    SOFTHSM = next((p for p in ("/usr/lib/softhsm/libsofthsm2.so", "/usr/lib/x86_64-linux-gnu/softhsm/libsofthsm2.so",
                                "/usr/local/lib/softhsm/libsofthsm2.so") if os.path.exists(p)), None) if shutil.which("softhsm2-util") else None
except ImportError:
    SOFTHSM = None

DOC = {"issuer": {"type": "B", "id": "999888777", "name": "Sample Company", "address": {"country": "EG", "governate": "Cairo"}},
       "receiver": {"type": "B", "id": "100200300", "name": "Delta Tech Solutions", "address": {"country": "EG"}},
       "documentType": "I", "documentTypeVersion": "1.0", "dateTimeIssued": "2026-11-05T10:00:00Z", "taxpayerActivityCode": "4100",
       "internalID": "INV-1", "invoiceLines": [{"description": "Fit-out \"phase 1\"", "itemType": "EGS", "itemCode": "EG-999888777-1",
                                                "unitType": "EA", "quantity": 1.0, "salesTotal": 1000.0, "total": 1140.0, "netTotal": 1000.0,
                                                "unitValue": {"currencySold": "EGP", "amountEGP": 1000.0},
                                                "taxableItems": [{"taxType": "T1", "amount": 140.0, "subType": "V009", "rate": 14.0}]}],
       "totalSalesAmount": 1000.0, "netAmount": 1000.0, "taxTotals": [{"taxType": "T1", "amount": 140.0}], "totalAmount": 1140.0}


def make_cert(tmp, cn="Sample Company e-seal"):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn), x509.NameAttribute(NameOID.COUNTRY_NAME, "EG"),
                      x509.NameAttribute(x509.ObjectIdentifier("2.5.4.97"), "999888777")])
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(4711)
            .not_valid_before(now - _dt.timedelta(days=1)).not_valid_after(now + _dt.timedelta(days=365)).sign(key, hashes.SHA256()))
    pfx = Path(tmp) / "company.pfx"
    pfx.write_bytes(pkcs12.serialize_key_and_certificates(b"company", key, cert, None, serialization.BestAvailableEncryption(b"secret-pw")))
    pem = Path(tmp) / "key.pem"
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return pfx, pem, cert


def openssl_verifies(sig_b64, data, tmp):
    if not shutil.which("openssl"):
        return None
    (Path(tmp) / "sig.der").write_bytes(base64.b64decode(sig_b64))
    (Path(tmp) / "data.txt").write_bytes(data)
    r = subprocess.run(["openssl", "cms", "-verify", "-inform", "DER", "-in", str(Path(tmp) / "sig.der"), "-content", str(Path(tmp) / "data.txt"),
                        "-binary", "-noverify", "-out", os.devnull], capture_output=True, text=True)
    return "Verification successful" in (r.stderr + r.stdout)


@unittest.skipUnless(HAVE, "asn1crypto / cryptography not installed")
class CertificateFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.pfx, _, self.cert = make_cert(self.tmp)
        self.cfg = bs.Config(str(Path(self.tmp) / "settings.json"))
        self.cfg.update({"mode": "pfx", "pfx_path": str(self.pfx)})
        self.app = bs.App(self.cfg)

    def test_the_same_text_is_signed_as_the_erp_serializes(self):
        from erp import einvoice
        self.assertEqual(bs.serialize(DOC), einvoice.serialize(DOC))
        self.assertIn('"INTERNALID""INV-1"', bs.serialize(DOC))
        self.assertIn('"INVOICELINES""INVOICELINES""DESCRIPTION""Fit-out \\"phase 1\\""', bs.serialize(DOC))

    def test_signs_and_verifies_and_openssl_agrees(self):
        with self.assertRaises(bs.SignerError):
            self.app.keys.unlock("wrong")
        self.app.keys.unlock("secret-pw")
        signed = self.app.sign_doc(DOC)
        sig = signed["signatures"][0]["value"]
        self.assertEqual(signed["signatures"][0]["signatureType"], "I")
        ser = bs.serialize(DOC).encode("utf-8")
        v = bs.verify(ser, sig)
        self.assertTrue(v["valid"], v)
        self.assertEqual(v["certificate"]["id_in_certificate"], "999888777")
        self.assertNotEqual(openssl_verifies(sig, ser, self.tmp), False)            # None: no openssl here
        tampered = json.loads(json.dumps(DOC))
        tampered["totalAmount"] = 1.0
        v = self.app.route("POST", "/v1/verify", {}, {"document": {**tampered, "signatures": signed["signatures"]}})
        self.assertFalse(v["valid"])
        self.assertFalse(v["checks"][0]["ok"])                                       # the document no longer matches
        self.assertFalse(openssl_verifies(sig, bs.serialize(tampered).encode(), self.tmp) or False)
        from asn1crypto import cms
        si = cms.ContentInfo.load(base64.b64decode(sig))["content"]["signer_infos"][0]
        names = {a["type"].native for a in si["signed_attrs"]}
        self.assertEqual(names, {"content_type", "signing_time", "message_digest", "signing_certificate_v2"})   # CAdES-BES
        self.assertIsNone(cms.ContentInfo.load(base64.b64decode(sig))["content"]["encap_content_info"]["content"].native)   # detached

    def test_the_pin_is_kept_for_a_while_then_asked_again(self):
        self.cfg.update({"pin_minutes": 1})
        st = self.app.keys.unlock("secret-pw")
        self.assertTrue(st["unlocked"])
        self.app.keys.pin_until = 0                                                  # the time ran out
        with self.assertRaises(bs.SignerError):
            self.app.sign_doc(DOC)
        self.cfg.update({"pfx_password": "secret-pw"})                              # a remembered password: no prompt
        self.assertTrue(self.app.sign_doc(DOC)["signatures"])

    def test_secrets_are_not_in_the_settings_file_in_clear_on_windows(self):
        self.cfg.update({"eta_client_secret": "s3cr3t-value"})
        raw = Path(self.cfg.path).read_text()
        self.assertNotIn('"eta_client_secret": "s3cr3t-value"', raw)
        self.assertEqual(bs.Config(self.cfg.path).get("eta_client_secret"), "s3cr3t-value")
        if os.name != "nt":
            self.assertEqual(os.stat(self.cfg.path).st_mode & 0o077, 0)            # owner only


@unittest.skipUnless(HAVE and SOFTHSM, "SoftHSM (a software token) not installed")
class UsbToken(unittest.TestCase):
    """The PKCS#11 path the USB token takes, with SoftHSM as the token."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        conf = Path(self.tmp) / "softhsm2.conf"
        (Path(self.tmp) / "tokens").mkdir()
        conf.write_text(f"directories.tokendir = {self.tmp}/tokens\nobjectstore.backend = file\n")
        os.environ["SOFTHSM2_CONF"] = str(conf)
        subprocess.run(["softhsm2-util", "--init-token", "--free", "--label", "ePass2003", "--pin", "123456", "--so-pin", "12345678"],
                       check=True, capture_output=True)
        _, pem, cert = make_cert(self.tmp)
        subprocess.run(["softhsm2-util", "--import", str(pem), "--token", "ePass2003", "--label", "seal", "--id", "a1",
                        "--pin", "123456"], check=True, capture_output=True)
        import PyKCS11
        from cryptography.hazmat.primitives.serialization import Encoding
        lib = PyKCS11.PyKCS11Lib()
        lib.load(SOFTHSM)
        s = lib.openSession(lib.getSlotList(tokenPresent=True)[0], PyKCS11.CKF_SERIAL_SESSION | PyKCS11.CKF_RW_SESSION)
        s.login("123456")
        s.createObject([(PyKCS11.CKA_CLASS, PyKCS11.CKO_CERTIFICATE), (PyKCS11.CKA_CERTIFICATE_TYPE, PyKCS11.CKC_X_509),
                        (PyKCS11.CKA_TOKEN, True), (PyKCS11.CKA_ID, bytes.fromhex("a1")), (PyKCS11.CKA_LABEL, "seal"),
                        (PyKCS11.CKA_SUBJECT, cert.subject.public_bytes()),
                        (PyKCS11.CKA_VALUE, cert.public_bytes(Encoding.DER))])
        s.logout()
        s.closeSession()
        self.cfg = bs.Config(str(Path(self.tmp) / "settings.json"))
        self.cfg.update({"mode": "pkcs11", "driver": SOFTHSM})
        self.app = bs.App(self.cfg)

    def tearDown(self):
        self.app.keys.close()

    def test_sign_with_the_token(self):
        st = self.app.keys.status()
        self.assertTrue(st["present"], st)
        self.assertEqual(st["token"]["label"], "ePass2003")
        with self.assertRaises(bs.SignerError) as e:
            self.app.keys.unlock("000000")
        self.assertEqual(e.exception.code, "bad_pin")
        with self.assertRaises(bs.SignerError) as e:
            self.app.sign_doc(DOC)
        self.assertEqual(e.exception.code, "pin_needed")                             # asks the ERP for the PIN
        st = self.app.keys.unlock("123456")
        self.assertIn("Sample Company e-seal", st["certificate"]["subject"])
        signed = self.app.route("POST", "/v1/sign", {}, {"documents": [DOC, {**DOC, "internalID": "INV-2"}]})["documents"]
        for d in signed:
            ser = bs.serialize(bs.unsigned(d)).encode("utf-8")
            self.assertTrue(bs.verify(ser, d["signatures"][0]["value"])["valid"])
            self.assertNotEqual(openssl_verifies(d["signatures"][0]["value"], ser, self.tmp), False)
        self.app.route("POST", "/v1/lock", {}, {})
        self.assertFalse(self.app.keys.status()["unlocked"])


class FakeETA(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def _out(self, status, obj, ctype="application/json"):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_POST(self):
        b = self._body()
        if self.path == "/connect/token":
            ok = b"client_secret=good" in b
            return self._out(200, {"access_token": "tok", "expires_in": 3600}) if ok else self._out(400, {"error": "invalid_client"})
        if self.path == "/api/v1/documentsubmissions":
            assert self.headers["Authorization"] == "Bearer tok"
            docs = json.loads(b)["documents"]
            FakeETA.seen.append(docs)
            return self._out(202, {"submissionId": "SUB1", "acceptedDocuments": [{"uuid": "U-" + d["internalID"], "longId": "L1",
                                                                                     "internalId": d["internalID"]} for d in docs if d.get("signatures")],
                                   "rejectedDocuments": [{"internalId": d["internalID"], "error": {"message": "unsigned"}} for d in docs if not d.get("signatures")]})
        return self._out(404, {})

    def do_GET(self):
        if self.path.startswith("/api/v1/documents/recent"):
            return self._out(200, {"result": [{"uuid": "R-1", "issuerId": "200300400", "issuerName": "Supplier Co", "total": 570.0,
                                               "status": "Valid", "dateTimeIssued": "2026-11-01T09:00:00Z", "documentType": "i"}],
                                   "metadata": {"totalPages": 1, "totalCount": 1}, "_q": self.path})
        if self.path.endswith("/details"):
            return self._out(200, {"uuid": self.path.split("/")[4], "status": "Valid", "longId": "LONG1", "validationResults": {}})
        if self.path.endswith("/pdf"):
            return self._out(200, b"%PDF-1.4 fake", "application/pdf")
        if self.path.endswith("/raw"):
            return self._out(200, {"uuid": self.path.split("/")[4], "document": json.dumps(DOC)})
        return self._out(404, {})

    def do_PUT(self):
        b = json.loads(self._body())
        return self._out(200, {}) if b.get("reason") else self._out(400, {"error": {"message": "reason required"}})


@unittest.skipUnless(HAVE, "asn1crypto / cryptography not installed")
class LocalService(unittest.TestCase):
    """The Signer as the ERP sees it: over HTTP on this computer, paired, with browser (CORS) access."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        pfx, _, _ = make_cert(self.tmp)
        self.eta = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeETA)
        threading.Thread(target=self.eta.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.eta.server_address[1]}"
        self._hosts = bs.ETA_HOSTS
        bs.ETA_HOSTS = {"preprod": (base, base, base), "prod": (base, base, base)}
        cfg = bs.Config(str(Path(self.tmp) / "settings.json"))
        cfg.update({"mode": "pfx", "pfx_path": str(pfx), "eta_client_id": "id", "eta_client_secret": "good"})
        self.app = bs.App(cfg)
        self.srv = bs.serve(0, self.app, open_browser=False)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.key = cfg.get("pairing_key")

    def tearDown(self):
        bs.ETA_HOSTS = self._hosts
        self.srv.shutdown()
        self.eta.shutdown()
        self.eta.server_close()

    def call(self, method, path, body=None, key=True, origin="https://mybeyonderp.com"):
        h = {"Content-Type": "application/json", "Origin": origin}
        if key:
            h["X-Signer-Key"] = self.key if key is True else key
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"{}"), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), dict(e.headers)

    def test_pairing_browser_access_and_signing(self):
        st, r, _ = self.call("GET", "/v1/hello", key=False)
        self.assertEqual((st, r["signer"]), (200, "BeyondERP Signer"))
        self.assertEqual(self.call("GET", "/v1/status", key=False)[0], 401)          # another website: no key, nothing
        self.assertEqual(self.call("GET", "/v1/status", key="guess")[0], 401)
        req = urllib.request.Request(self.url + "/v1/sign", method="OPTIONS", headers={"Origin": "https://mybeyonderp.com",
                                     "Access-Control-Request-Method": "POST", "Access-Control-Request-Private-Network": "true"})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.headers["Access-Control-Allow-Private-Network"], "true")
            self.assertEqual(r.headers["Access-Control-Allow-Origin"], "https://mybeyonderp.com")
            self.assertIn("x-signer-key", r.headers["Access-Control-Allow-Headers"])
        st, r, h = self.call("GET", "/v1/status")
        self.assertEqual(st, 200)
        self.assertEqual(h["Access-Control-Allow-Origin"], "https://mybeyonderp.com")
        self.assertTrue(r["eta"]["ready"])
        self.assertNotIn("good", json.dumps(r))                                       # the secret never leaves the Signer
        st, r, _ = self.call("POST", "/v1/sign", {"documents": [DOC]})
        self.assertEqual((st, r["code"]), (401, "pin_needed"))
        self.assertEqual(self.call("POST", "/v1/unlock", {"pin": "nope"})[0], 401)
        self.assertEqual(self.call("POST", "/v1/unlock", {"pin": "secret-pw"})[0], 200)
        st, r, _ = self.call("POST", "/v1/sign", {"documents": [DOC]})
        signed = r["documents"][0]
        st, v, _ = self.call("POST", "/v1/verify", {"document": signed})
        self.assertTrue(v["valid"], v)

    def raw(self, method, path, data=None, headers=None):
        """One request as a browser would send it, without following redirects."""
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=20)
        h = {"Host": f"localhost:{self.srv.port}", **(headers or {})}
        if data is not None:
            h.setdefault("Content-Type", "application/x-www-form-urlencoded")
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = r.status, dict(r.headers), r.read().decode("utf-8", "replace")
        c.close()
        return out

    def form_token(self):
        import re
        st, _, page = self.raw("GET", "/")
        self.assertEqual(st, 200)
        return re.search(r'name="token" value="([^"]+)"', page).group(1)

    def test_the_settings_page_is_local_and_not_changeable_from_other_sites(self):
        with urllib.request.urlopen(self.url + "/") as r:
            page = r.read().decode()
            self.assertIn(self.key, page)                                             # shown on this computer
            self.assertNotIn("Access-Control-Allow-Origin", r.headers)                # other sites can't read it
            self.assertIn("frame-ancestors 'none'", r.headers["Content-Security-Policy"])   # nor show it in a frame
            self.assertEqual(r.headers["X-Frame-Options"], "DENY")
            # "no-referrer" would make browsers send Origin: null with the form, and the Signer would refuse its own page
            self.assertEqual(r.headers["Referrer-Policy"], "same-origin")
        req = urllib.request.Request(self.url + "/", headers={"Origin": "https://evil.example"})
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(req)
        # another website posting the form: refused before anything is read, even with the page's own token
        tok = self.form_token()
        st, _, _ = self.raw("POST", "/settings", f"token={tok}&do=save&eta_client_id=x".encode(), {"Origin": "https://evil.example"})
        self.assertEqual(st, 403)
        st, _, _ = self.raw("POST", "/settings", f"token={tok}&do=save&eta_client_id=x".encode(), {"Origin": "null"})
        self.assertEqual(st, 403)
        # the Signer's own window without the page's token (an old page, or a guess): nothing changes
        st, h, _ = self.raw("POST", "/settings", b"token=wrong&do=save&eta_client_id=x", {"Origin": f"http://localhost:{self.srv.port}"})
        self.assertEqual((st, h["Location"]), (303, "/?m=expired"))
        self.assertEqual(self.app.cfg.get("eta_client_id"), "id")                     # unchanged

    def test_a_web_page_renamed_to_this_computer_is_refused(self):
        # DNS rebinding: evil.example made to point at 127.0.0.1 still arrives with its own name in the Host header
        for path in ("/", "/v1/hello", "/v1/status"):
            st, h, body = self.raw("GET", path, headers={"Host": f"evil.example:{self.srv.port}", "X-Signer-Key": self.key})
            self.assertEqual(st, 403, path)
            self.assertNotIn(self.key, body)
        st, _, _ = self.raw("OPTIONS", "/v1/sign", headers={"Host": "evil.example", "Origin": "https://evil.example"})
        self.assertEqual(st, 403)
        for host in (f"localhost:{self.srv.port}", f"127.0.0.1:{self.srv.port}", f"LOCALHOST:{self.srv.port}"):
            self.assertEqual(self.raw("GET", "/v1/hello", headers={"Host": host})[0], 200, host)

    def test_changes_from_the_signers_own_window(self):
        own = {"Origin": f"http://localhost:{self.srv.port}"}
        tok = self.form_token()
        st, h, _ = self.raw("POST", "/settings", f"token={tok}&do=save&mode=pfx&eta_client_id=new-id&pin_minutes=abc".encode(), own)
        self.assertEqual((st, h["Location"]), (303, "/?m=saved"))                    # then shown again, so a reload never posts twice
        self.assertEqual((self.app.cfg.get("eta_client_id"), self.app.cfg.get("pin_minutes")), ("new-id", 30))
        self.assertEqual(self.app.cfg.get("eta_client_secret"), "good")              # a blank secret field keeps the secret
        self.assertIn("Saved.", self.raw("GET", "/?m=saved")[2])
        old = self.app.cfg.get("pairing_key")
        st, h, _ = self.raw("POST", "/settings", f"token={tok}&do=newkey".encode(), own)
        self.assertEqual(h["Location"], "/?m=newkey")
        self.assertNotEqual(self.app.cfg.get("pairing_key"), old)
        self.assertEqual(self.call("GET", "/v1/status")[0], 401)                     # the old key no longer works
        stopped = []
        self.app.on_quit = lambda: stopped.append(1)
        st, _, page = self.raw("POST", "/settings", f"token={tok}&do=quit".encode(), own)
        self.assertEqual(st, 200)
        self.assertIn("has stopped", page)
        threading.Event().wait(0.8)
        self.assertEqual(stopped, [1])

    def test_a_second_start_finds_the_running_signer(self):
        self.assertTrue(bs.running(self.srv.port))
        self.assertFalse(bs.running(1))                                              # nothing there
        self.assertEqual(bs.page_url(47321), "http://localhost:47321/")             # what the window opens: a name, not an address
        with self.assertRaises(OSError):
            bs.Service(self.srv.port)                                                # the port is already the Signer's

    def test_the_tax_authority_through_the_signer(self):
        self.call("POST", "/v1/unlock", {"pin": "secret-pw"})
        st, r, _ = self.call("GET", "/v1/eta/test")
        self.assertEqual(st, 200, r)
        st, r, _ = self.call("POST", "/v1/eta/submit", {"documents": [DOC, {**DOC, "internalID": "INV-2"}]})
        self.assertEqual(st, 200, r)
        self.assertEqual([a["uuid"] for a in r["submission"]["acceptedDocuments"]], ["U-INV-1", "U-INV-2"])
        sent = FakeETA.seen[-1]
        self.assertTrue(all(d["signatures"][0]["value"] for d in sent))              # signed before sending
        self.assertTrue(bs.verify(bs.serialize(bs.unsigned(sent[0])).encode(), sent[0]["signatures"][0]["value"])["valid"])
        st, r, _ = self.call("GET", "/v1/eta/document/U-INV-1/details")
        self.assertEqual((r["status"], r["_share_url"].endswith("/print/documents/U-INV-1/share/LONG1")), ("Valid", True))
        st, r, _ = self.call("GET", "/v1/eta/document/U-INV-1/pdf")
        self.assertTrue(base64.b64decode(r["pdf"]).startswith(b"%PDF"))
        self.assertEqual(self.call("PUT", "/v1/eta/document/U-INV-1/state", {"status": "cancelled", "reason": ""})[0], 400)
        self.assertEqual(self.call("PUT", "/v1/eta/document/U-INV-1/state", {"status": "cancelled", "reason": "Wrong customer"})[1]["status"], "cancelled")
        self.assertEqual(self.call("PUT", "/v1/eta/document/R-1/state", {"status": "rejected", "reason": "Not ordered"})[1]["status"], "rejected")
        self.assertEqual(self.call("PUT", "/v1/eta/document/R-1/state", {"status": "deleted", "reason": "x"})[0], 400)
        st, r, _ = self.call("GET", "/v1/eta/recent?direction=Received&page=1&size=20")
        self.assertEqual(r["result"][0]["issuerName"], "Supplier Co")
        self.assertIn("direction=Received", r["_q"])
        self.app.cfg.update({"eta_client_secret": "bad"})
        self.app.eta._tok = None
        st, r, _ = self.call("GET", "/v1/eta/test")
        self.assertEqual(st, 400)



class Packaging(unittest.TestCase):
    """The Windows build: one name for the installer everywhere, a windowed program with BEYONDERP's details, and an
    ERP that links to whatever the build published."""

    def read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8-sig")

    def test_the_installer_is_built_published_and_offered_under_one_name(self):
        import re
        iss, spec, wf = self.read("packaging/signer.iss"), self.read("packaging/signer.spec"), self.read(".github/workflows/deploy.yml")
        self.assertIn("OutputBaseFilename=BeyondERP-Signer-Setup", iss)
        self.assertIn("dist-installer/BeyondERP-Signer-Setup.exe", wf)                # published on the release
        self.assertIn("pyinstaller packaging/signer.spec", wf)
        self.assertIn("packaging\\signer.iss", wf)
        self.assertNotIn("--console", wf)                                             # no console window any more
        self.assertIn('"BeyondERP-Signer-Setup.exe"', self.read("packaging/collect_downloads.py"))
        self.assertIn("console=False", spec)
        for want in ('"CompanyName", "BEYONDERP"', '"LegalCopyright", COPYRIGHT', "Copyright (c) 2026 BEYONDERP. All rights reserved."):
            self.assertIn(want, spec)
        for want in ("AppPublisher=BEYONDERP", "AppCopyright={#Copyright}", "PrivilegesRequired=lowest", "LicenseFile=EULA.txt",
                     'Name: "arabic"', "--background", "uninsdeletevalue"):
            self.assertIn(want, iss)
        # the installer's start-with-Windows value is the one the Signer's own switch reads and writes
        self.assertIn('ValueName: "{#Name}"', iss)
        self.assertIn('#define Name "BeyondERP Signer"', iss)
        src = self.read("signer/beyond_signer.py")
        self.assertIn('RUN_KEY, RUN_NAME = r"Software\\Microsoft\\Windows\\CurrentVersion\\Run", "BeyondERP Signer"', src)
        self.assertEqual(re.search(r'#define Exe "([^"]+)"', iss).group(1), 'BeyondERP-Signer.exe')
        self.assertIn('name="BeyondERP-Signer"', spec)

    def test_the_erp_links_to_the_published_signer_and_shows_no_address(self):
        js = self.read("erp/static/app.js")
        self.assertNotIn('href="downloads/BeyondERP-Signer.exe"', js)                 # taken from the downloads list instead
        self.assertIn('f.kind === "signer-windows"', js)
        self.assertNotIn("keep its window open", js)
        self.assertNotIn('ph: "http://127.0.0.1:47321', js)

if __name__ == "__main__":
    unittest.main()
