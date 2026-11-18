"""Fingerprint / face sign-in on this device (WebAuthn, the browser's passkeys), checked here with nothing but the
standard library — so it runs the same in the desktop app and in the browser edition (Pyodide).

Only what the app lock needs: a platform authenticator (Touch ID, Face ID, Android, Windows Hello) with user
verification, attestation "none", ES256 (P-256) or RS256 keys. register() reads the new credential's public key;
verify() checks a sign-in: the challenge we issued, our origin and site, the person verified on the device, and the
signature over authenticatorData ‖ SHA-256(clientDataJSON).
"""
import base64
import hashlib
import json


class WebAuthnError(Exception):
    pass


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    s = str(s or "")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------- CBOR (the subset WebAuthn uses)
def cbor(data: bytes, i=0):
    """(value, next index) of the CBOR item at i: integers, byte / text strings, arrays, maps, true / false / null."""
    if i >= len(data):
        raise WebAuthnError("truncated CBOR")
    ib = data[i]
    major, info = ib >> 5, ib & 31
    i += 1
    if info < 24:
        n = info
    elif info in (24, 25, 26, 27):
        size = 1 << (info - 24)
        n = int.from_bytes(data[i:i + size], "big")
        i += size
    else:
        raise WebAuthnError("unsupported CBOR")
    if major == 0:
        return n, i
    if major == 1:
        return -1 - n, i
    if major in (2, 3):
        raw = data[i:i + n]
        if len(raw) != n:
            raise WebAuthnError("truncated CBOR")
        return (raw if major == 2 else raw.decode()), i + n
    if major == 4:
        out = []
        for _ in range(n):
            v, i = cbor(data, i)
            out.append(v)
        return out, i
    if major == 5:
        out = {}
        for _ in range(n):
            k, i = cbor(data, i)
            v, i = cbor(data, i)
            out[k] = v
        return out, i
    if major == 7 and info in (20, 21, 22):
        return {20: False, 21: True, 22: None}[info], i
    raise WebAuthnError("unsupported CBOR")


# ---------------------------------------------------------------- signatures
P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
A = P - 3
B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
     0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)


def _add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    (x1, y1), (x2, y2) = p, q
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    lam = ((3 * x1 * x1 + A) * pow(2 * y1, -1, P) if p == q else (y2 - y1) * pow(x2 - x1, -1, P)) % P
    x3 = (lam * lam - x1 - x2) % P
    return x3, (lam * (x1 - x3) - y1) % P


def _mul(k, p):
    r = None
    while k:
        if k & 1:
            r = _add(r, p)
        p = _add(p, p)
        k >>= 1
    return r


def _der_sig(sig: bytes):
    """(r, s) of an ASN.1 DER ECDSA signature."""
    if len(sig) < 8 or sig[0] != 0x30:
        raise WebAuthnError("bad signature")
    i, out = 2, []
    for _ in range(2):
        if sig[i] != 0x02:
            raise WebAuthnError("bad signature")
        n = sig[i + 1]
        out.append(int.from_bytes(sig[i + 2:i + 2 + n], "big"))
        i += 2 + n
    return out


def es256_ok(x: int, y: int, msg: bytes, sig: bytes) -> bool:
    if (y * y - (x * x * x + A * x + B)) % P:
        return False                                   # not a point of the curve
    r, s = _der_sig(sig)
    if not (0 < r < N and 0 < s < N):
        return False
    z = int.from_bytes(hashlib.sha256(msg).digest(), "big")
    w = pow(s, -1, N)
    pt = _add(_mul(z * w % N, G), _mul(r * w % N, (x, y)))
    return pt is not None and pt[0] % N == r


_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def rs256_ok(n: int, e: int, msg: bytes, sig: bytes) -> bool:
    k = (n.bit_length() + 7) // 8
    if len(sig) != k:
        return False
    em = pow(int.from_bytes(sig, "big"), e, n).to_bytes(k, "big")
    t = _SHA256_PREFIX + hashlib.sha256(msg).digest()
    return em == b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t


# ---------------------------------------------------------------- ceremonies
def _client(client_json: bytes, kind: str, challenge: str, rp_id: str):
    try:
        c = json.loads(client_json.decode())
    except (ValueError, UnicodeDecodeError):
        raise WebAuthnError("bad client data") from None
    if c.get("type") != kind:
        raise WebAuthnError("wrong ceremony")
    if c.get("challenge") != challenge:
        raise WebAuthnError("the request expired — try again")
    host = str(c.get("origin") or "").split("://")[-1].split(":")[0].split("/")[0]
    if host != rp_id or not (str(c.get("origin", "")).startswith("https://") or host in ("localhost",)):
        raise WebAuthnError("not this site")
    return c


def _auth_data(ad: bytes, rp_id: str):
    if len(ad) < 37:
        raise WebAuthnError("bad authenticator data")
    if ad[:32] != hashlib.sha256(rp_id.encode()).digest():
        raise WebAuthnError("not this site")
    flags = ad[32]
    if not flags & 0x01 or not flags & 0x04:
        raise WebAuthnError("the person was not verified on the device")
    return flags, int.from_bytes(ad[33:37], "big")


def register(client_json_b64: str, attestation_b64: str, challenge: str, rp_id: str):
    """The new credential: {id, key: {alg, x, y} | {alg, n, e}, count}."""
    _client(unb64u(client_json_b64), "webauthn.create", challenge, rp_id)
    att, _ = cbor(unb64u(attestation_b64))
    ad = att.get("authData") if isinstance(att, dict) else None
    if not isinstance(ad, bytes):
        raise WebAuthnError("bad attestation")
    flags, count = _auth_data(ad, rp_id)
    if not flags & 0x40:
        raise WebAuthnError("no credential in the attestation")
    n = int.from_bytes(ad[53:55], "big")
    cred = ad[55:55 + n]
    cose, _ = cbor(ad, 55 + n)
    alg = cose.get(3)
    if alg == -7 and cose.get(1) == 2 and cose.get(-1) == 1:
        key = {"alg": -7, "x": b64u(cose[-2]), "y": b64u(cose[-3])}
    elif alg == -257 and cose.get(1) == 3:
        key = {"alg": -257, "n": b64u(cose[-1]), "e": b64u(cose[-2])}
    else:
        raise WebAuthnError("this kind of key is not supported")
    return {"id": b64u(cred), "key": key, "count": count}


def verify(key: dict, stored_count: int, client_json_b64: str, auth_data_b64: str, sig_b64: str, challenge: str, rp_id: str):
    """The signature counter after a good sign-in; raises WebAuthnError otherwise."""
    cj = unb64u(client_json_b64)
    _client(cj, "webauthn.get", challenge, rp_id)
    ad = unb64u(auth_data_b64)
    _, count = _auth_data(ad, rp_id)
    msg, sig = ad + hashlib.sha256(cj).digest(), unb64u(sig_b64)
    if key.get("alg") == -7:
        good = es256_ok(int.from_bytes(unb64u(key["x"]), "big"), int.from_bytes(unb64u(key["y"]), "big"), msg, sig)
    elif key.get("alg") == -257:
        good = rs256_ok(int.from_bytes(unb64u(key["n"]), "big"), int.from_bytes(unb64u(key["e"]), "big"), msg, sig)
    else:
        good = False
    if not good:
        raise WebAuthnError("the fingerprint / face check did not match")
    if count and stored_count and count <= stored_count:
        raise WebAuthnError("this credential looks copied — use your PIN")
    return count
