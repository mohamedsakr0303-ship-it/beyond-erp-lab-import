"""BeyondERP Signer — signs Egyptian e-invoices with the company's token and talks to the Tax Authority (ETA).

It runs on the computer the USB token (or e-seal certificate file) is plugged into, and only answers that computer
(localhost). The ERP, open in a browser or the desktop app on the same computer, sends it a document; the Signer
signs it with the token (CAdES-BES, as ETA requires for documentTypeVersion 1.0) and, when asked, submits it and
fetches statuses, the official printout, received documents, cancels and rejections. The ETA client secret lives
here, encrypted on this computer, never in the books.

  Pairing   The ERP must send this Signer's pairing key (X-Signer-Key) with every call, so no other website the person
            visits can use the token. The key is shown in the Signer's own window (Start menu → BeyondERP Signer).
  PIN       The token PIN is typed in the ERP, sent only to this computer, and kept in memory for a few minutes
            (Settings → PIN timeout), or remembered here, encrypted, if the person chooses.
  Storage   On Windows the secrets (ETA client secret, PIN, certificate-file password) are encrypted with DPAPI for
            the signed-in Windows user; elsewhere the settings file is readable by its owner only.

  Desktop   On Windows it is installed like any program (BeyondERP-Signer-Setup.exe), has no console window, keeps an
            icon by the clock (open, start with Windows, quit) and shows its settings in a window of its own.
  Local     Only this computer's own names are answered (localhost, 127.0.0.1, ::1): a web page that points its own
            site name at this computer is refused, and the settings change only from the Signer's own window.

Run:  BeyondERP Signer from the Start menu   (or: python beyond_signer.py [--port 47321] [--background] [--tray])
"""
import base64
import datetime as _dt
import hashlib
import html
import http.server
import json
import os
import secrets
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

VERSION = "1.1.0"
PORT = 47321
ETA_HOSTS = {"preprod": ("https://id.preprod.eta.gov.eg", "https://api.preprod.invoicing.eta.gov.eg",
                         "https://preprod.invoicing.eta.gov.eg"),
             "prod": ("https://id.eta.gov.eg", "https://api.invoicing.eta.gov.eg", "https://invoicing.eta.gov.eg")}
# token drivers (PKCS#11 libraries) found on Egyptian signing computers: ePass2003 (Egypt Trust, MCSD), Watchdata,
# SafeNet, Bit4id; the first one present is used unless one is chosen in the settings
DRIVERS = [r"C:\Windows\System32\eps2003csp11.dll", r"C:\Windows\System32\eps2003csp11v2.dll", r"C:\Windows\System32\ShuttleCsp11_3003.dll",
           r"C:\Windows\System32\WDPKCS.dll", r"C:\Windows\System32\eTPKCS11.dll", r"C:\Windows\System32\bit4ipki.dll",
           "/usr/lib/libeps2003csp11.so", "/usr/local/lib/libeps2003csp11.so", "/usr/lib/libeTPkcs11.so",
           "/Library/Frameworks/eToken.framework/Versions/Current/libeToken.dylib", "/usr/local/lib/libcastle.1.0.0.dylib"]
SECRET_KEYS = ("eta_client_secret", "pin", "pfx_password")


# ================================================================== settings (secrets encrypted on Windows)
def _home():
    base = os.environ.get("APPDATA") if os.name == "nt" else os.path.join(os.path.expanduser("~"), ".config")
    d = os.environ.get("BEYOND_SIGNER_HOME") or os.path.join(base or os.path.expanduser("~"), "BeyondERP Signer")
    os.makedirs(d, exist_ok=True)
    return d


def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = BLOB(len(data), buf), BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("Windows could not " + ("encrypt" if protect else "decrypt") + " the setting")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _seal(v):
    if not v:
        return ""
    raw = v.encode("utf-8")
    if os.name == "nt":
        return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode()
    return "plain:" + base64.b64encode(raw).decode()


def _open(v):
    if not v:
        return ""
    kind, _, b = v.partition(":")
    raw = base64.b64decode(b)
    return (_dpapi(raw, False) if kind == "dpapi" else raw).decode("utf-8")


DEFAULTS = {"mode": "pkcs11", "driver": "", "cert_id": "", "pfx_path": "", "pin_minutes": 30, "remember_pin": False,
            "eta_env": "preprod", "eta_client_id": "", "pairing_key": "", "lang": "en"}


class Config:
    def __init__(self, path=None):
        self.path = path or os.path.join(_home(), "settings.json")
        self.lock = threading.Lock()
        self.data = dict(DEFAULTS)
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.data.update(json.load(f))
            except (OSError, ValueError):
                pass
        if not self.data.get("pairing_key"):
            self.data["pairing_key"] = secrets.token_urlsafe(18)
            self.save()

    def get(self, k):
        v = self.data.get(k, DEFAULTS.get(k))
        return _open(v) if k in SECRET_KEYS else v

    def update(self, d):
        with self.lock:
            for k, v in d.items():
                if k in SECRET_KEYS:
                    if v is None:
                        continue                       # unchanged
                    self.data[k] = _seal(v)
                elif k in DEFAULTS:
                    self.data[k] = v
            self.save()

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)


# ================================================================== ETA canonical serialization (what is signed)
def serialize(obj):
    """The string ETA hashes and signs: every key upper-cased and quoted, every value quoted, arrays repeating the
    key before each element. Identical to the ERP's (erp/einvoice.py), so both agree on what was signed."""
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


def unsigned(doc):
    return {k: v for k, v in doc.items() if k != "signatures"}


# ================================================================== CAdES-BES (detached CMS SignedData)
CONTENT_OID = "1.2.840.113549.1.7.5"      # the content type ETA's reference signer puts on the signed data


def cades(data, cert_der, sign_fn, when=None):
    """A detached CAdES-BES signature over `data` (bytes): signed attributes content-type, signing-time,
    message-digest (SHA-256) and signing-certificate-v2; RSA PKCS#1 v1.5 with SHA-256 made by `sign_fn`."""
    from asn1crypto import algos, cms, core, tsp, x509
    cert = x509.Certificate.load(cert_der)
    when = when or _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    ess = tsp.SigningCertificateV2({"certs": [tsp.ESSCertIDv2({
        "hash_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}), "cert_hash": hashlib.sha256(cert_der).digest(),
        "issuer_serial": tsp.IssuerSerial({"issuer": [x509.GeneralName({"directory_name": cert.issuer})],
                                            "serial_number": cert.serial_number})})]})
    attrs = cms.CMSAttributes([
        cms.CMSAttribute({"type": "content_type", "values": [cms.ContentType(CONTENT_OID)]}),
        cms.CMSAttribute({"type": "signing_time", "values": [cms.Time({"utc_time": core.UTCTime(when)})]}),
        cms.CMSAttribute({"type": "message_digest", "values": [hashlib.sha256(data).digest()]}),
        cms.CMSAttribute({"type": "signing_certificate_v2", "values": [ess]}),
    ])
    signature = sign_fn(attrs.dump())                           # signed over the attributes as a SET (tag 0x31)
    si = cms.SignerInfo({
        "version": "v1", "sid": cms.SignerIdentifier({"issuer_and_serial_number": cms.IssuerAndSerialNumber(
            {"issuer": cert.issuer, "serial_number": cert.serial_number})}),
        "digest_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}), "signed_attrs": attrs,
        "signature_algorithm": algos.SignedDigestAlgorithm({"algorithm": "rsassa_pkcs1v15"}), "signature": signature})
    sd = cms.SignedData({"version": "v1", "digest_algorithms": [algos.DigestAlgorithm({"algorithm": "sha256"})],
                         "encap_content_info": {"content_type": CONTENT_OID}, "certificates": [cert], "signer_infos": [si]})
    return base64.b64encode(cms.ContentInfo({"content_type": "signed_data", "content": sd}).dump()).decode()


def verify(data, signature_b64, now=None):
    """Check a detached CAdES-BES signature over `data`: the digest matches, the RSA signature is valid for the
    certificate inside, and the certificate was valid when signed. Returns what was found, never raises."""
    from asn1crypto import cms
    out = {"valid": False, "checks": []}

    def check(name, ok, detail=""):
        out["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        return ok
    try:
        ci = cms.ContentInfo.load(base64.b64decode(signature_b64))
        sd = ci["content"]
        si = sd["signer_infos"][0]
        cert = sd["certificates"][0].chosen
        attrs = {a["type"].native: a["values"][0] for a in si["signed_attrs"]}
        digest = attrs["message_digest"].native
        ok1 = check("The document matches what was signed", digest == hashlib.sha256(data).digest())
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.x509 import load_der_x509_certificate
        c = load_der_x509_certificate(cert.dump())
        signed = si["signed_attrs"].dump()
        signed = b"\x31" + signed[1:]                         # [0] IMPLICIT → SET OF, as it was signed
        try:
            c.public_key().verify(si["signature"].native, signed, padding.PKCS1v15(), hashes.SHA256())
            ok2 = check("The signature was made with this certificate's key", True)
        except Exception as e:      # noqa: BLE001
            ok2 = check("The signature was made with this certificate's key", False, str(e) or "invalid signature")
        when = attrs.get("signing_time").native if "signing_time" in attrs else None
        nb, na = c.not_valid_before.replace(tzinfo=_dt.timezone.utc), c.not_valid_after.replace(tzinfo=_dt.timezone.utc)
        at = when or now or _dt.datetime.now(_dt.timezone.utc)
        ok3 = check("The certificate was valid when signed", nb <= at <= na, f"{nb:%Y-%m-%d} → {na:%Y-%m-%d}")
        ok4 = check("The certificate is named in the signature (signing-certificate-v2)", "signing_certificate_v2" in attrs)
        out.update(valid=bool(ok1 and ok2 and ok3 and ok4), signed_at=when.isoformat() if when else "",
                   certificate=_cert_info(c), fingerprint=hashlib.sha256(cert.dump()).hexdigest())
    except Exception as e:          # noqa: BLE001
        check("The signature can be read", False, str(e)[:200])
    return out


def _cert_info(c):
    def name(n):
        return ", ".join(f"{a.oid._name}={a.value}" for a in n)
    tax = ""
    for a in c.subject:
        if a.oid.dotted_string in ("2.5.4.97", "2.5.4.5", "2.5.4.45") and str(a.value).strip():
            tax = tax or str(a.value).strip()
    return {"subject": name(c.subject), "issuer": name(c.issuer), "serial": format(c.serial_number, "x"),
            "valid_from": c.not_valid_before.date().isoformat(), "valid_to": c.not_valid_after.date().isoformat(), "id_in_certificate": tax}


# ================================================================== the token (PKCS#11) or a certificate file (.pfx)
class SignerError(Exception):
    def __init__(self, msg, code="error", status=400):
        super().__init__(msg)
        self.code, self.status = code, status


class Keys:
    """The signing key and its certificate: a USB token through its PKCS#11 driver, or a .pfx / .p12 file."""

    def __init__(self, cfg):
        self.cfg, self.lock = cfg, threading.RLock()
        self.lib = self.session = self.key = self.cert_der = None
        self.pin_until, self.pfx = 0, None

    def driver(self):
        p = self.cfg.get("driver")
        if p:
            return p if os.path.exists(p) else None
        return next((d for d in DRIVERS if os.path.exists(d)), None)

    def status(self):
        with self.lock:
            mode = self.cfg.get("mode")
            st = {"mode": mode, "unlocked": self.is_open(), "unlocked_for": max(0, int(self.pin_until - time.time())) if self.is_open() else 0}
            if mode == "pfx":
                p = self.cfg.get("pfx_path")
                st.update(file=p, present=bool(p and os.path.exists(p)))
                if self.cert_der:
                    st["certificate"] = self._info()
                return st
            drv = self.driver()
            st.update(driver=drv or "", present=False)
            if not drv:
                st["problem"] = "No token driver found. Install the driver from the token's supplier (Egypt Trust / MCSD), or choose its .dll in the settings."
                return st
            try:
                lib = self._lib(drv)
                slots = lib.getSlotList(tokenPresent=True)
                st["present"] = bool(slots)
                if slots:
                    ti = lib.getTokenInfo(slots[0])
                    st["token"] = {"label": ti.label.strip(), "model": ti.model.strip(), "serial": ti.serialNumber.strip(),
                                   "manufacturer": ti.manufacturerID.strip()}
                else:
                    st["problem"] = "Plug in the token."
            except Exception as e:      # noqa: BLE001
                st["problem"] = f"The token driver could not be loaded: {e}"
            if self.cert_der:
                st["certificate"] = self._info()
            return st

    def _lib(self, drv):
        if self.lib is None or getattr(self, "_drv", None) != drv:
            import PyKCS11
            lib = PyKCS11.PyKCS11Lib()
            lib.load(drv)
            self.lib, self._drv = lib, drv
        return self.lib

    def _info(self):
        from cryptography.x509 import load_der_x509_certificate
        return _cert_info(load_der_x509_certificate(self.cert_der))

    def is_open(self):
        return (self.session is not None or self.pfx is not None) and time.time() < self.pin_until

    def close(self):
        with self.lock:
            if self.session is not None:
                try:
                    self.session.logout()
                    self.session.closeSession()
                except Exception:       # noqa: BLE001
                    pass
            self.session = self.key = self.pfx = None
            self.pin_until = 0

    def unlock(self, pin=None):
        with self.lock:
            pin = pin or (self.cfg.get("pin") if self.cfg.get("remember_pin") else "")
            minutes = max(1, min(int(self.cfg.get("pin_minutes") or 30), 720))
            if self.cfg.get("mode") == "pfx":
                path = self.cfg.get("pfx_path")
                if not path or not os.path.exists(path):
                    raise SignerError("Choose the certificate file (.pfx) in the Signer's settings.", "no_file")
                from cryptography.hazmat.primitives.serialization import Encoding, pkcs12
                if not (pin or self.cfg.get("pfx_password")):
                    raise SignerError("Enter the certificate file's password.", "pin_needed", 401)
                try:
                    with open(path, "rb") as f:
                        key, cert, _ = pkcs12.load_key_and_certificates(f.read(), (pin or self.cfg.get("pfx_password") or "").encode() or None)
                except ValueError:
                    raise SignerError("Wrong password for the certificate file.", "bad_pin", 401) from None
                self.pfx, self.cert_der = key, cert.public_bytes(Encoding.DER)
                self.pin_until = time.time() + minutes * 60
                return self.status()
            if not pin:
                raise SignerError("Enter the token PIN.", "pin_needed", 401)
            import PyKCS11
            drv = self.driver()
            if not drv:
                raise SignerError("No token driver found.", "no_driver")
            lib = self._lib(drv)
            slots = lib.getSlotList(tokenPresent=True)
            if not slots:
                raise SignerError("Plug in the token.", "no_token")
            self.close()
            s = lib.openSession(slots[0], PyKCS11.CKF_SERIAL_SESSION | PyKCS11.CKF_RW_SESSION)
            try:
                s.login(pin)
            except PyKCS11.PyKCS11Error as e:
                s.closeSession()
                msg = {"CKR_PIN_INCORRECT": "Wrong PIN.", "CKR_PIN_LOCKED": "The token is locked after too many wrong PINs. Unlock it with the supplier's tool."}
                raise SignerError(next((v for k, v in msg.items() if k in str(e)), f"The token refused the PIN: {e}"), "bad_pin", 401) from None
            certs = s.findObjects([(PyKCS11.CKA_CLASS, PyKCS11.CKO_CERTIFICATE)])
            keys = s.findObjects([(PyKCS11.CKA_CLASS, PyKCS11.CKO_PRIVATE_KEY)])
            if not certs or not keys:
                s.logout()
                s.closeSession()
                raise SignerError("No signing certificate and key were found on the token.", "no_cert")
            want = self.cfg.get("cert_id")
            pick = None
            for c in certs:
                cid = bytes(s.getAttributeValue(c, [PyKCS11.CKA_ID])[0] or b"").hex()
                k = next((k for k in keys if bytes(s.getAttributeValue(k, [PyKCS11.CKA_ID])[0] or b"").hex() == cid), None)
                if k is not None and (not want or want == cid):
                    pick = (c, k)
                    break
            if not pick:
                pick = (certs[0], keys[0])
            self.cert_der = bytes(s.getAttributeValue(pick[0], [PyKCS11.CKA_VALUE])[0])
            self.session, self.key = s, pick[1]
            self.pin_until = time.time() + minutes * 60
            if self.cfg.get("remember_pin"):
                self.cfg.update({"pin": pin})
            return self.status()

    def sign_raw(self, data):
        with self.lock:
            if not self.is_open():
                self.close()
                self.unlock()                                # a remembered PIN; otherwise asks for one
            if self.pfx is not None:
                from cryptography.hazmat.primitives import hashes
                from cryptography.hazmat.primitives.asymmetric import padding
                return self.pfx.sign(data, padding.PKCS1v15(), hashes.SHA256())
            import PyKCS11
            return bytes(self.session.sign(self.key, data, PyKCS11.Mechanism(PyKCS11.CKM_SHA256_RSA_PKCS, None)))

    def sign(self, serialized):
        with self.lock:
            if not self.is_open():
                self.close()
                self.unlock()                                # a remembered PIN; otherwise asks for one
            return cades(serialized.encode("utf-8"), self.cert_der, self.sign_raw)


# ================================================================== the Tax Authority
class ETA:
    def __init__(self, cfg):
        self.cfg, self._tok, self._exp = cfg, None, 0

    def hosts(self):
        return ETA_HOSTS.get(self.cfg.get("eta_env") or "preprod", ETA_HOSTS["preprod"])

    def http(self, method, url, body=None, headers=None, raw=False, timeout=60):
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data, ctype = r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            data = e.read()
            try:
                detail = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                detail = data.decode("utf-8", "replace")[:500]
            text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)[:1500]
            raise SignerError(f"The Tax Authority answered {e.code}" + (f": {text}" if text else ""), "eta_http",
                              e.code if 400 <= e.code < 500 else 502) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SignerError(f"Could not reach the Tax Authority: {e}", "eta_unreachable", 502) from None
        if raw:
            return data, ctype
        return json.loads(data.decode("utf-8")) if data else {}

    def token(self):
        if self._tok and time.time() < self._exp - 60:
            return self._tok
        cid, sec = self.cfg.get("eta_client_id"), self.cfg.get("eta_client_secret")
        if not cid or not sec:
            raise SignerError("Enter the ETA client ID and secret in the Signer's settings.", "eta_not_set")
        body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": cid, "client_secret": sec,
                                       "scope": "InvoicingAPI"}).encode()
        r = self.http("POST", self.hosts()[0] + "/connect/token", body, {"Content-Type": "application/x-www-form-urlencoded"})
        if not r.get("access_token"):
            raise SignerError("Signing in to the Tax Authority failed. Check the client ID, secret and environment.", "eta_login")
        self._tok, self._exp = r["access_token"], time.time() + int(r.get("expires_in") or 3600)
        return self._tok

    def api(self, method, path, body=None, raw=False):
        h = {"Authorization": "Bearer " + self.token(), "Accept-Language": "en"}
        if body is not None:
            h["Content-Type"] = "application/json"
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        return self.http(method, self.hosts()[1] + path, body, h, raw=raw)

    def share_url(self, uuid, long_id):
        return f"{self.hosts()[2]}/print/documents/{uuid}/share/{long_id}" if uuid and long_id else ""


# ================================================================== the local service
class App:
    def __init__(self, cfg=None):
        self.cfg = cfg or Config()
        self.keys, self.eta = Keys(self.cfg), ETA(self.cfg)
        self.form_token = secrets.token_urlsafe(24)       # the settings page's own: changes only from a page shown here
        self.on_quit = None                               # "Stop the Signer" on the settings page

    # what the ERP may call (all need the pairing key)
    def route(self, method, path, q, body):
        if path == "/v1/status":
            return {"signer": "BeyondERP Signer", "version": VERSION, "keys": self.keys.status(),
                    "eta": {"env": self.cfg.get("eta_env"), "ready": bool(self.cfg.get("eta_client_id") and self.cfg.get("eta_client_secret"))}}
        if path == "/v1/unlock" and method == "POST":
            return self.keys.unlock(str(body.get("pin") or ""))
        if path == "/v1/lock" and method == "POST":
            self.keys.close()
            return {"ok": True}
        if path == "/v1/sign" and method == "POST":
            docs = body.get("documents") or ([body["document"]] if body.get("document") else [])
            if body.get("serialized") and not docs:
                return {"signature": self.keys.sign(body["serialized"])}
            return {"documents": [self.sign_doc(d) for d in docs[:500]], "certificate": self.keys.status().get("certificate")}
        if path == "/v1/verify" and method == "POST":
            doc = body.get("document") or {}
            sigs = [s.get("value") for s in (doc.get("signatures") or []) if s.get("value")] or ([body["signature"]] if body.get("signature") else [])
            if not sigs:
                return {"valid": False, "checks": [{"check": "The document carries a signature", "ok": False, "detail": ""}]}
            ser = body.get("serialized") or serialize(unsigned(doc))
            return verify(ser.encode("utf-8"), sigs[0])
        if path == "/v1/eta/test":
            self.eta._tok = None
            self.eta.token()
            return {"ok": True, "env": self.cfg.get("eta_env")}
        if path == "/v1/eta/submit" and method == "POST":
            docs = [d if (d.get("signatures") or d.get("documentTypeVersion") == "0.9") else self.sign_doc(d) for d in (body.get("documents") or [])[:100]]
            if not docs:
                raise SignerError("No documents to submit.")
            res = self.eta.api("POST", "/api/v1/documentsubmissions", {"documents": docs})
            return {"submission": res, "documents": docs}
        if path.startswith("/v1/eta/document/"):
            parts = path.split("/")[4:]
            uuid = urllib.parse.unquote(parts[0])
            what = parts[1] if len(parts) > 1 else "details"
            if what == "details":
                r = self.eta.api("GET", f"/api/v1/documents/{uuid}/details")
                r["_share_url"] = self.eta.share_url(uuid, r.get("longId"))
                return r
            if what == "raw":
                return self.eta.api("GET", f"/api/v1/documents/{uuid}/raw")
            if what == "pdf":
                data, _ = self.eta.api("GET", f"/api/v1/documents/{uuid}/pdf", raw=True)
                return {"pdf": base64.b64encode(data).decode(), "name": f"{uuid}.pdf"}
            if what == "state" and method == "PUT":
                st = body.get("status")
                if st not in ("cancelled", "rejected"):
                    raise SignerError("The state is cancelled or rejected.")
                reason = str(body.get("reason") or "").strip()
                if not reason:
                    raise SignerError("Give a reason.")
                self.eta.api("PUT", f"/api/v1/documents/state/{uuid}/state", {"status": st, "reason": reason[:500]})
                return {"ok": True, "status": st}
        if path == "/v1/eta/recent":
            params = {"pageNo": int(q.get("page") or 1), "pageSize": min(int(q.get("size") or 50), 100)}
            for k in ("direction", "status", "documentType", "submissionDateFrom", "submissionDateTo", "issueDateFrom", "issueDateTo"):
                if q.get(k):
                    params[k] = q[k]
            return self.eta.api("GET", "/api/v1/documents/recent?" + urllib.parse.urlencode(params))
        raise SignerError("Not found", "not_found", 404)

    def sign_doc(self, doc):
        if doc.get("documentTypeVersion") == "0.9":
            return doc
        sig = self.keys.sign(serialize(unsigned(doc)))
        return {**unsigned(doc), "signatures": [{"signatureType": "I", "value": sig}]}

    # the Signer's own settings page (this computer only; changes need the page's own token)
    def settings_page(self, msg=""):
        c, st = self.cfg, self.keys.status()
        e = html.escape
        found = [d for d in DRIVERS if os.path.exists(d)]
        rows = "".join(f"<option value='{e(d)}' {'selected' if c.get('driver') == d else ''}>{e(d)}</option>" for d in found)
        cert = st.get("certificate") or {}
        tok = f'<input type="hidden" name="token" value="{e(self.form_token)}">'
        note = MESSAGES.get(msg)
        ready = bool(st.get("present"))
        auto = ""
        if autostart_supported():
            on = autostart()
            auto = f"""<form method="post" action="/settings" class="line">{tok}<input type="hidden" name="do" value="autostart"><input type="hidden" name="on" value="{'0' if on else '1'}">
<div><b>Start with Windows · التشغيل مع Windows</b><div class="muted">{'On: the Signer starts by itself when you sign in.' if on else 'Off: start it from the Start menu before you sign invoices.'}</div></div>
<button class="ghost">{'Turn off · إيقاف' if on else 'Turn on · تشغيل'}</button></form>"""
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BeyondERP Signer</title><link rel="icon" href="/icon.png"><style>
:root{{--bg:#f4f6f8;--panel:#fff;--ink:#17222b;--muted:#5f6c77;--line:#dde3e8;--acc:#0f8f84;--on-acc:#fff;--bad:#c0392b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0e1519;--panel:#152027;--ink:#e6eef2;--muted:#9aabb5;--line:#26343d;--acc:#3cc8b9;--on-acc:#062420;--bad:#ff7b6b}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Tahoma,sans-serif;padding:20px 16px 28px}}
main{{max-width:760px;margin:0 auto;display:grid;gap:14px}}h1{{margin:0;font-size:1.35rem}}h2{{margin:0 0 8px;font-size:1.02rem}}
header{{display:flex;gap:12px;align-items:center}}header img{{width:44px;height:44px;border-radius:10px}}header .pill{{margin-inline-start:auto}}
.pill{{font-size:.82rem;font-weight:600;padding:4px 10px;border-radius:999px;border:1px solid currentColor;white-space:nowrap}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}}label{{display:grid;gap:4px;font-weight:600;font-size:.9rem}}
input,select{{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);min-width:0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}.muted{{color:var(--muted);font-size:.88rem}}
button{{font:inherit;font-weight:600;padding:9px 16px;border-radius:8px;border:0;background:var(--acc);color:var(--on-acc);cursor:pointer}}
button.ghost{{background:none;color:var(--ink);border:1px solid var(--line)}}button.warn{{background:none;color:var(--bad);border:1px solid var(--line)}}
.key{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}code{{background:var(--bg);padding:6px 10px;border-radius:8px;font-size:1.05rem;letter-spacing:.04em;user-select:all;word-break:break-all}}
.line{{display:flex;gap:12px;align-items:center;justify-content:space-between;padding:10px 0;border-top:1px solid var(--line)}}.line:first-of-type{{border-top:0}}
.ok{{color:var(--acc)}}.bad{{color:var(--bad)}}.msg{{padding:10px 12px;border-radius:10px;background:var(--panel);border:1px solid var(--acc)}}
footer{{text-align:center}}</style></head><body><main>
<header><img src="/icon.png" alt=""><div><h1>BeyondERP Signer</h1><div class="muted">Version {VERSION} · works on this computer only · يعمل على هذا الجهاز فقط</div></div>
<span class="pill {'ok' if ready else 'bad'}">{'Ready · جاهز' if ready else 'Not ready · غير جاهز'}</span></header>
{f'<div class="msg" role="status">{e(note)}</div>' if note else ''}
<section class="card"><h2>Pairing key · مفتاح الربط</h2><p>In the ERP, open E-invoicing → Settings → BeyondERP Signer and paste this key once on this computer.</p>
<div class="key"><code id="pk">{e(c.get('pairing_key'))}</code><button type="button" class="ghost" id="cp">Copy · نسخ</button></div>
<p class="muted">You can close this window: the Signer keeps working in the background, with its icon by the clock. Open this window again from that icon or the Start menu.
Anyone with this key and the token PIN can sign as the company.</p></section>
<form class="card" method="post" action="/settings">{tok}<input type="hidden" name="do" value="save">
<h2>Signing key · مفتاح التوقيع</h2><div class="grid">
<label>Uses<select name="mode"><option value="pkcs11" {'selected' if c.get('mode') == 'pkcs11' else ''}>USB token</option><option value="pfx" {'selected' if c.get('mode') == 'pfx' else ''}>Certificate file (.pfx / .p12)</option></select></label>
<label>Token driver<select name="driver"><option value="">Find it automatically</option>{rows}</select></label>
<label>Or the driver's path<input name="driver_path" placeholder="C:\\Windows\\System32\\…dll" value="{e(c.get('driver') if c.get('driver') not in found else '')}"></label>
<label>Certificate file<input name="pfx_path" value="{e(c.get('pfx_path'))}" placeholder="C:\\…\\company.pfx"></label>
<label>Certificate file password<input name="pfx_password" type="password" placeholder="unchanged"></label>
<label>Keep the PIN for (minutes)<input name="pin_minutes" type="number" min="1" max="720" value="{e(str(c.get('pin_minutes')))}"></label>
<label style="grid-column:1/-1;display:flex;gap:8px;align-items:center;font-weight:400"><input type="checkbox" name="remember_pin" {'checked' if c.get('remember_pin') else ''}> Remember the PIN on this computer (encrypted for this Windows user)</label></div>
<p class="muted">Status: {'<b class="ok">token present</b>' if ready else '<b class="bad">' + e(st.get('problem') or 'not ready') + '</b>'}
{(' · ' + e(cert.get('subject', '')) + ' · valid to ' + e(cert.get('valid_to', ''))) if cert else ''}</p>
<h2>Tax Authority · مصلحة الضرائب</h2><div class="grid">
<label>Environment<select name="eta_env"><option value="preprod" {'selected' if c.get('eta_env') == 'preprod' else ''}>Pre-production (testing)</option><option value="prod" {'selected' if c.get('eta_env') == 'prod' else ''}>Production</option></select></label>
<label>Client ID<input name="eta_client_id" value="{e(c.get('eta_client_id'))}" autocomplete="off"></label>
<label>Client secret<input name="eta_client_secret" type="password" placeholder="{'unchanged' if c.get('eta_client_secret') else ''}" autocomplete="off"></label></div>
<p class="muted">From the ETA portal: your ERP system's registration (client ID and secret). Kept here, encrypted, never sent to the ERP.</p>
<p><button>Save · حفظ</button></p></form>
<section class="card"><h2>This computer · هذا الجهاز</h2>{auto}
<form method="post" action="/settings" class="line">{tok}<input type="hidden" name="do" value="newkey">
<div><b>A new pairing key · مفتاح ربط جديد</b><div class="muted">Every computer and browser paired before will need the new key.</div></div><button class="ghost">Make a new key</button></form>
<form method="post" action="/settings" class="line">{tok}<input type="hidden" name="do" value="quit">
<div><b>Stop the Signer · إيقاف البرنامج</b><div class="muted">Invoices cannot be signed until it is started again.</div></div><button class="warn">Stop</button></form></section>
<footer class="muted">Copyright © 2026 BEYONDERP. All rights reserved.</footer>
</main><script>
document.getElementById("cp").onclick = function () {{
  var b = this, k = document.getElementById("pk").textContent;
  navigator.clipboard.writeText(k).then(function () {{ b.textContent = "Copied · تم النسخ"; setTimeout(function () {{ b.textContent = "Copy · نسخ"; }}, 1800); }});
}};
</script></body></html>"""

    def save_settings(self, form):
        """A change from the settings page; returns what happened (one of MESSAGES)."""
        if not secrets.compare_digest(str(form.get("token") or ""), self.form_token):
            raise SignerError("This page has expired. Reload it.", "expired", 403)
        act = form.get("do") or "save"
        if act == "newkey":
            self.cfg.update({"pairing_key": secrets.token_urlsafe(18)})
            return "newkey"
        if act == "autostart":
            return "autostart_on" if autostart(form.get("on") == "1") else "autostart_off"
        if act == "quit":
            return "quit"
        try:
            minutes = max(1, min(int(form.get("pin_minutes") or 30), 720))
        except ValueError:
            minutes = 30
        upd = {"mode": form.get("mode") if form.get("mode") in ("pkcs11", "pfx") else "pkcs11",
               "driver": (form.get("driver_path") or form.get("driver") or "").strip(),
               "pfx_path": (form.get("pfx_path") or "").strip(), "eta_env": "prod" if form.get("eta_env") == "prod" else "preprod",
               "eta_client_id": (form.get("eta_client_id") or "").strip(), "remember_pin": bool(form.get("remember_pin")),
               "pin_minutes": minutes}
        if form.get("eta_client_secret"):
            upd["eta_client_secret"] = form["eta_client_secret"].strip()
        if form.get("pfx_password"):
            upd["pfx_password"] = form["pfx_password"]
        if not upd["remember_pin"]:
            upd["pin"] = ""
        self.cfg.update(upd)
        self.keys.close()
        self.eta._tok = None
        return "saved"


MESSAGES = {"saved": "Saved. · تم الحفظ.",
            "newkey": "A new pairing key was made: paste it in the ERP on every computer that uses this Signer. · تم إنشاء مفتاح ربط جديد.",
            "expired": "This page had expired, so nothing was changed. Try again. · انتهت صلاحية الصفحة ولم يتغير شيء. أعد المحاولة.",
            "autostart_on": "The Signer will start by itself when you sign in to Windows. · سيعمل البرنامج تلقائيًا عند تسجيل الدخول.",
            "autostart_off": "The Signer will no longer start with Windows. · لن يعمل البرنامج تلقائيًا مع Windows."}
STOPPED_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>BeyondERP Signer</title><style>body{margin:0;display:grid;place-items:center;
min-height:100vh;font:15px/1.5 system-ui,"Segoe UI",Tahoma,sans-serif;background:#f4f6f8;color:#17222b;text-align:center}
@media (prefers-color-scheme:dark){body{background:#0e1519;color:#e6eef2}}</style></head><body><div><h1 style="font-size:1.3rem">The BeyondERP Signer has stopped.</h1>
<p>تم إيقاف برنامج التوقيع.</p><p>Start it again from the Start menu before you sign invoices. You can close this window.</p></div></body></html>"""
# what the settings page may load and do: nothing from elsewhere, never inside another site's frame
PAGE_HEADERS = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self'; "
                                           "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
                "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",   # no-referrer would make the browser send Origin: null
                "X-Content-Type-Options": "nosniff"}


class Handler(http.server.BaseHTTPRequestHandler):
    app = None
    server_version = "BeyondERP-Signer/" + VERSION
    sys_version = ""

    def log_message(self, fmt, *args):         # quiet: only problems are printed
        pass

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "content-type, x-signer-key")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
            self.send_header("Access-Control-Allow-Private-Network", "true")    # a public site calling this computer
            self.send_header("Access-Control-Max-Age", "600")

    def _send(self, status, obj=None, ctype="application/json; charset=utf-8", body=None, cors=True, headers=None):
        data = body if body is not None else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if cors:
            self._cors()
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if "Cache-Control" not in (headers or {}):
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _known_host(self):
        # A web page can rename its own site to this computer's address (DNS rebinding); the browser then still sends
        # that site's name here, so only this computer's own names are answered.
        return (self.headers.get("Host") or "").strip().lower() in getattr(self.server, "hosts", ())

    def _own_origin(self, origin):
        return origin in {"http://" + h for h in getattr(self.server, "hosts", ())}

    def do_OPTIONS(self):
        if not (self._local() and self._known_host()):
            return self._send(403, {"error": "forbidden"}, cors=False)
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _handle(self, method):
        if not self._local():
            return self._send(403, {"error": "This Signer only answers its own computer."}, cors=False)
        if not self._known_host():
            return self._send(403, {"error": "This Signer answers only at localhost or 127.0.0.1."}, cors=False)
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(length, 20_000_000)) if length else b""
        if u.path == "/icon.png" and method == "GET":
            p = _resource("icon.png")
            if not p:
                return self._send(404, {"error": "Not found"}, cors=False)
            with open(p, "rb") as f:
                return self._send(200, body=f.read(), ctype="image/png", cors=False, headers={"Cache-Control": "max-age=86400"})
        if u.path in ("/", "/settings"):
            origin = self.headers.get("Origin")
            if origin and not self._own_origin(origin):             # another website: it may neither read nor change it
                return self._send(403, {"error": "forbidden"}, cors=False)
            if method == "POST":
                try:
                    done = self.app.save_settings(dict(urllib.parse.parse_qsl(raw.decode("utf-8"))))
                except SignerError as e:
                    done = e.code if e.code in MESSAGES else "expired"
                if done == "quit":
                    if self.app.on_quit:
                        threading.Timer(0.4, self.app.on_quit).start()
                    return self._send(200, body=STOPPED_PAGE.encode("utf-8"), ctype="text/html; charset=utf-8", cors=False, headers=PAGE_HEADERS)
                return self._send(303, body=b"", ctype="text/plain", cors=False, headers={"Location": "/?m=" + done})   # a reload never posts twice
            if method != "GET":
                return self._send(405, {"error": "Method not allowed"}, cors=False)
            return self._send(200, body=self.app.settings_page(q.get("m", "")).encode("utf-8"), ctype="text/html; charset=utf-8",
                              cors=False, headers=PAGE_HEADERS)
        if u.path == "/v1/hello":                     # no key: lets the ERP tell "not running" from "not paired"
            return self._send(200, {"signer": "BeyondERP Signer", "version": VERSION})
        key = self.headers.get("X-Signer-Key") or ""
        if not secrets.compare_digest(key, self.app.cfg.get("pairing_key") or ""):
            return self._send(401, {"error": "Pair the ERP with this Signer: paste the Signer's pairing key in the ERP.", "code": "not_paired"})
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
            return self._send(200, self.app.route(method, u.path, q, body))
        except SignerError as e:
            return self._send(e.status, {"error": str(e), "code": e.code})
        except Exception as e:      # noqa: BLE001
            print("error:", repr(e), file=sys.stderr)
            return self._send(500, {"error": f"The Signer hit a problem: {e}", "code": "internal"})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"     # on Windows that would let a second program share the port

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)    # the port is the Signer's alone
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]


class Server6(Server):
    address_family = socket.AF_INET6


class Service:
    """The Signer on this computer's own addresses: 127.0.0.1 and, where the computer has it, ::1, so that
    http://localhost answers however Windows resolves it. Nothing outside this computer can reach either."""

    def __init__(self, port):
        self.servers = [Server(("127.0.0.1", port), Handler)]
        self.port = self.servers[0].server_address[1]
        try:
            self.servers.append(Server6(("::1", self.port), Handler))
        except OSError:
            pass                                   # no IPv6 on this computer
        self.hosts = {f"{h}:{self.port}" for h in ("127.0.0.1", "localhost", "[::1]")}
        for s in self.servers:
            s.hosts = self.hosts
        self.server_address = ("127.0.0.1", self.port)
        self._running = []

    def serve_forever(self):
        for s in self.servers[1:]:
            self._running.append(s)
            threading.Thread(target=s.serve_forever, daemon=True).start()
        self._running.append(self.servers[0])
        self.servers[0].serve_forever()

    def shutdown(self):
        for s in self._running:
            s.shutdown()
        for s in self.servers:
            s.server_close()
        self._running = []


def serve(port=PORT, app=None, open_browser=False):
    Handler.app = app or App()
    svc = Service(port)
    if open_browser:
        threading.Timer(0.5, open_window, (page_url(svc.port),)).start()
    return svc


# ================================================================== on the desktop: its own window, the tray icon, start with Windows
FROZEN = getattr(sys, "frozen", False)
RUN_KEY, RUN_NAME = r"Software\Microsoft\Windows\CurrentVersion\Run", "BeyondERP Signer"


def page_url(port=PORT):
    return f"http://localhost:{port}/"


def _resource(name):
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (getattr(sys, "_MEIPASS", ""), here, os.path.join(here, "..", "packaging")):
        p = os.path.join(base, name) if base else ""
        if p and os.path.isfile(p):
            return p
    return ""


def _local_dir():
    """This computer only (not the roaming profile): the settings window's browser profile and the log."""
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.path.join(os.path.expanduser("~"), ".cache")
    d = os.path.join(base or os.path.expanduser("~"), "BeyondERP Signer")
    os.makedirs(d, exist_ok=True)
    return d


def _browsers():
    if os.name == "nt":
        roots = [os.environ.get(k, "") for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        rel = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
               r"BraveSoftware\Brave-Browser\Application\brave.exe"]
        return [os.path.join(r, x) for x in rel for r in roots if r]
    if sys.platform == "darwin":
        return ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    return [shutil.which(x) or "" for x in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")]


def open_window(url):
    """The settings in a window of their own (Edge or Chrome app mode: no tabs, no address bar), like any program's
    window; the default browser only when neither is installed."""
    for exe in _browsers():
        if exe and os.path.isfile(exe):
            try:
                subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={os.path.join(_local_dir(), 'window')}", "--no-first-run",
                                  "--no-default-browser-check", "--window-size=880,940"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=0x08000000 if os.name == "nt" else 0, close_fds=True)   # CREATE_NO_WINDOW
                return True
            except OSError:
                continue
    webbrowser.open(url)
    return False


def running(port=PORT):
    """A Signer already answers on this computer (a second start then only opens its window)."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/v1/hello", timeout=2) as r:
            return json.loads(r.read().decode("utf-8")).get("signer") == "BeyondERP Signer"
    except (OSError, ValueError):
        return False


def autostart_supported():
    return os.name == "nt" and FROZEN


def autostart(enable=None):
    """Start with Windows for the signed-in user: read it (enable=None), or turn it on or off. The installer sets the
    same value."""
    if not autostart_supported():
        return False
    import winreg
    if enable is None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                return bool(winreg.QueryValueEx(k, RUN_NAME)[0])
        except OSError:
            return False
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if enable:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{sys.executable}" --background')
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass
    return bool(enable)


def _arabic():
    if os.name != "nt":
        return False
    try:
        import ctypes
        return (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x01
    except Exception:       # noqa: BLE001
        return False


def notify(text, error=False):
    """A message the person sees: a Windows message box (the Signer has no console there), else the terminal."""
    if os.name == "nt" and FROZEN:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "BeyondERP Signer", 0x10 if error else 0x40)
        except Exception:   # noqa: BLE001
            print(text, file=sys.stderr)
        return
    print(text, file=sys.stderr)


def _log_to_file():
    """A windowed Signer has no console: what it would print (problems only, never a secret) goes to signer.log."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        p = os.path.join(_local_dir(), "signer.log")
        if os.path.exists(p) and os.path.getsize(p) > 1_000_000:
            os.replace(p, p + ".1")
        f = open(p, "a", encoding="utf-8", buffering=1)     # noqa: SIM115 — open for the life of the program
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f
    except OSError:
        pass


def tray(port):
    """The Signer's icon by the clock: open its window, start with Windows, quit. None where it can't be shown."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:       # noqa: BLE001
        return None
    img = None
    if _resource("icon.png"):
        try:
            img = Image.open(_resource("icon.png"))
        except Exception:   # noqa: BLE001
            img = None
    if img is None:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((4, 4, 60, 60), fill=(15, 143, 132, 255))
    ar = _arabic()

    def t(en, a):
        return a if ar else en
    items = [pystray.MenuItem(t("Open BeyondERP Signer", "فتح برنامج التوقيع"), lambda *_: open_window(page_url(port)), default=True)]
    if autostart_supported():
        items.append(pystray.MenuItem(t("Start with Windows", "التشغيل مع Windows"), lambda *_: autostart(not autostart()),
                                      checked=lambda _item: autostart()))
    items += [pystray.Menu.SEPARATOR, pystray.MenuItem(t("Quit", "إنهاء"), lambda icon, _item: icon.stop())]
    return pystray.Icon("BeyondERP Signer", img, t("BeyondERP Signer is running", "برنامج التوقيع BeyondERP Signer يعمل"), pystray.Menu(*items))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else PORT
    background = "--background" in argv or "--no-browser" in argv       # started with Windows: no window, just the icon
    _log_to_file()
    if running(port):                     # already running: a second start shows its window and leaves
        if not background:
            open_window(page_url(port))
        return 0
    try:
        svc = serve(port)
    except OSError as e:
        notify(f"BeyondERP Signer could not start: another program on this computer is using port {port}.\n\n({e})", error=True)
        return 1
    print(f"BeyondERP Signer {VERSION} is running on this computer: {page_url(svc.port)}", flush=True)
    if not background:
        threading.Timer(0.4, open_window, (page_url(svc.port),)).start()
    icon = tray(svc.port) if (os.name == "nt" or "--tray" in argv) else None
    if icon is None:                      # a terminal (Mac / Linux), or no tray here: runs until stopped
        Handler.app.on_quit = svc.shutdown
        try:
            svc.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    Handler.app.on_quit = icon.stop
    threading.Thread(target=svc.serve_forever, daemon=True).start()
    icon.run()                            # until Quit, in the icon's menu or on the settings page
    svc.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
