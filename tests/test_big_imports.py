"""Big imports and backups never fail on size.
- Every kind of backup the app exports (workbook, workbook with files, JSON, database file, compressed or not) reads,
  packs for the shared workspace and unpacks to exactly the same data, and imports to the same books either way.
- Compressed backups (.json.gz, .sqlite3.gz) import like the plain files.
- Through the shared workspace an import is logged as what was read (compact), large changes and copies of the books
  travel in pieces, and another device that joins gets the same books.
- Imports may be larger than the 25 MB limit for other calls."""
import datetime
import gzip
import http.cookiejar
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from erp import cloud, demo, interchange, server  # noqa: E402
from erp.db import Database  # noqa: E402
from erp.textl10n import Note  # noqa: E402


def same(a, b, path="data"):
    """Equal values of the same types (a Note keeps its Arabic, a date stays a date)."""
    if type(a) is not type(b):
        raise AssertionError(f"{path}: {type(a).__name__} != {type(b).__name__}")
    if isinstance(a, Note) and getattr(a, "ar", "") != getattr(b, "ar", ""):
        raise AssertionError(f"{path}: Arabic of a note differs")
    if isinstance(a, dict):
        if list(a) != list(b):
            raise AssertionError(f"{path}: keys differ")
        for k in a:
            same(a[k], b[k], f"{path}/{k}")
    elif isinstance(a, (list, tuple)):
        if len(a) != len(b):
            raise AssertionError(f"{path}: length {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            same(x, y, f"{path}[{i}]")
    elif a != b and not (a != a and b != b):     # NaN equals NaN here
        raise AssertionError(f"{path}: {a!r} != {b!r}")


class Node:
    def __init__(self, tmp, name):
        self.db = Database(Path(tmp) / f"{name}.sqlite3")
        self.srv = server.ERPServer(("127.0.0.1", 0), self.db, Path(tmp) / f"bk-{name}")
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None, raw=None):
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, method=method, data=data, headers={
            "Content-Type": "application/octet-stream" if raw is not None else "application/json", "X-Requested-With": "BeyondERP"})
        try:
            with self.op.open(req) as r:
                out = r.read()
                return r.status, (json.loads(out or b"{}") if "json" in (r.headers.get("Content-Type") or "") and not r.headers.get(
                    "Content-Disposition") else out)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def close(self):
        self.srv.cloud.stop()
        self.srv.shutdown()
        self.srv.server_close()
        self.db.close()


def books(db):
    return {"accounts": db.q("SELECT code, name, type, parent FROM accounts ORDER BY code"),
            "groups": db.q("SELECT code, name, parent FROM account_groups ORDER BY code"),
            "lines": db.q("SELECT e.date, e.memo, l.account, l.debit, l.credit FROM journal_lines l "
                          "JOIN journal_entries e ON e.id = l.entry_id ORDER BY e.date, e.id, l.id"),
            "parties": db.q("SELECT name FROM parties ORDER BY name")}


class BigImports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.src = Node(cls.tmp.name, "src")
        cls.src.call("POST", "/api/setup", {"username": "admin", "password": "secret123"})
        demo.load(cls.src.db, "admin")
        cls.files = {}
        for name, path in (("xlsx", "/api/export/workbook"), ("zip", "/api/export/workbook?files=1"),
                           ("json", "/api/export/json"), ("json.gz", "/api/export/json?gz=1"),
                           ("sqlite", "/api/backup"), ("sqlite.gz", "/api/backup?gz=1")):
            st, body = cls.src.call("GET", path)
            assert st == 200, (path, st, body)
            cls.files[name] = body

    @classmethod
    def tearDownClass(cls):
        cls.src.close()
        cls.tmp.cleanup()

    def read(self, fmt, raw):
        raw = interchange.gunzip_if(raw)
        if fmt == "json":
            return interchange.read_json(raw)
        if fmt == "sqlite":
            return interchange.read_sqlite(raw)
        if interchange.is_files_zip(raw):
            return interchange.read_files_zip(raw, interchange.read_xlsx)
        p = Path(self.tmp.name) / "in.xlsx"
        p.write_bytes(raw)
        return interchange.read_xlsx(str(p))

    def test_compressed_backups_are_much_smaller(self):
        self.assertEqual(gzip.decompress(self.files["json.gz"])[:1], b"{")
        self.assertLess(len(self.files["json.gz"]), len(self.files["json"]) / 2)
        self.assertEqual(gzip.decompress(self.files["sqlite.gz"])[:16], b"SQLite format 3\x00")

    def test_every_backup_packs_and_unpacks_to_the_same_data_and_books(self):
        for name, fmt in (("xlsx", "xlsx"), ("zip", "zip"), ("json", "json"), ("json.gz", "json"), ("sqlite.gz", "sqlite")):
            with self.subTest(name):
                data = self.read(fmt, self.files[name])
                packed = interchange.pack_read(data, fmt)
                source, back = interchange.read_parsed(interchange.gunzip_if(packed))
                self.assertEqual(source, fmt)
                same(data, back)
                one, two = (Database(Path(self.tmp.name) / f"{name}-{i}.sqlite3") for i in (1, 2))
                r1 = interchange.import_data(one, data, mode="replace", user="t")
                r2 = interchange.import_data(two, interchange.read_parsed(interchange.gunzip_if(packed))[1], mode="replace", user="t")
                self.assertEqual(r1["counts"], r2["counts"])
                self.assertEqual(books(one), books(two))
                self.assertTrue(books(one)["lines"])
                one.close(), two.close()

    def test_packing_keeps_types_json_does_not_have(self):
        data = {"Journal Vouchers": [{"date": datetime.date(2026, 1, 2), "at": datetime.datetime(2026, 1, 2, 3, 4, 5),
                                      "t": datetime.time(9, 30), "n": 1.5, "pair": (1, "a"), "blank": None}],
                "_extras": {"files": {"a.pdf": b"%PDF-1.4 \x00\xff"}, 7: "number key", "\u0000odd": 1},
                "_notes": [Note("Done", "تم"), "plain"]}
        same(data, interchange.read_parsed(gzip.decompress(interchange.pack_read(data, "xlsx")))[1])
        with self.assertRaises(interchange.NotPackable):
            interchange.pack_read({"x": [object()]}, "xlsx")
        for bad in (b"{}", b'{"beyond_import": 1, "data": {"Sheet": "not rows"}}', b"not json"):
            with self.assertRaises(Exception):
                interchange.read_parsed(bad)

    def test_compressed_files_import_like_plain_ones(self):
        node = Node(self.tmp.name, "gz")
        try:
            node.call("POST", "/api/setup", {"username": "admin", "password": "secret123"})
            for name, fmt in (("json.gz", "xlsx"), ("json.gz", "json"), ("sqlite.gz", "sqlite")):   # json.gz picked as Excel too
                with self.subTest(name, fmt=fmt):
                    st, r = node.call("POST", f"/api/import?format={fmt}&mode=replace&dry_run=1", raw=self.files[name])
                    self.assertEqual(st, 200, r)
                    plain = node.call("POST", f"/api/import?format={'sqlite' if 'sqlite' in name else 'json'}&mode=replace&dry_run=1",
                                      raw=self.files[name.replace(".gz", "")])[1]
                    self.assertEqual(r["counts"], plain["counts"])
            st, r = node.call("POST", "/api/import?format=json&mode=replace", raw=gzip.compress(b"x" * 100 + b"\x00" * 10))
            self.assertEqual(st, 400)
        finally:
            node.close()

    def test_imports_may_be_larger_than_other_calls(self):
        self.assertEqual(server.body_limit("/api/import"), server.MAX_IMPORT)
        self.assertEqual(server.body_limit("/api/import?format=xlsx"), server.MAX_IMPORT)
        self.assertEqual(server.body_limit("/api/import-sheet?sheet=Parties"), server.MAX_IMPORT)
        self.assertEqual(server.body_limit("/api/bus"), server.MAX_BODY)
        self.assertEqual(server.body_limit("/api/importer"), server.MAX_BODY)

    def test_through_the_workspace_an_import_is_compact_travels_in_pieces_and_reaches_other_devices(self):
        import mock_supabase
        sb = mock_supabase.start()
        url = f"http://127.0.0.1:{sb.server_address[1]}"
        a, c = Node(self.tmp.name, "wa"), Node(self.tmp.name, "wc")
        keep = cloud.PART
        cloud.PART = 40_000                       # small pieces, so this test's files go up in several
        try:
            a.call("POST", "/api/setup", {"username": "admin", "password": "secret123"})
            self.assertEqual(a.call("PUT", "/api/cloud/config", {"url": url, "key": "anon"})[0], 200)
            self.assertEqual(a.call("POST", "/api/cloud/signin", {"email": "owner@x.com", "password": "pw123456", "create": True})[0], 200)
            self.assertEqual(a.call("POST", "/api/cloud/claim", {})[0], 200)
            raw = self.files["xlsx"]
            st, r = a.call("POST", "/api/import?format=xlsx&mode=append", raw=raw)
            self.assertEqual(st, 200, r)
            op = sb.state.all_ops()[-1]
            self.assertIn("format=parsed", op["path"])
            self.assertEqual(op["content_type"], "application/x-beyond-import")
            self.assertTrue(op["body"].startswith("parts:"), op["body"][:40])
            body = a.srv.cloud.get_big(op["body"])
            self.assertLess(len(body), len(raw))                 # smaller than the workbook, even as base64 text
            self.assertGreater(sb.state.part_calls, 2)
            snaps = list(sb.state.snapshots.values())[0]
            self.assertTrue(snaps[max(snaps)]["data"].startswith("parts:"))   # the fresh copy after the import, in pieces
            mine = books(a.db)
            self.assertTrue(mine["lines"])
            # another device joins: copy and log come back piece by piece, and its books are the same
            c.call("POST", "/api/setup", {"username": "admin", "password": "secret123"})
            c.call("PUT", "/api/cloud/config", {"url": url, "key": "anon"})
            self.assertEqual(c.call("POST", "/api/cloud/signin", {"email": "owner@x.com", "password": "pw123456"})[0], 200)
            st, r = c.call("POST", "/api/cloud/join", {})
            self.assertEqual(st, 200, r)
            self.assertEqual(books(c.db), mine)
            # replaying the logged import from scratch gives the same books too
            fresh = Node(self.tmp.name, "replay")
            try:
                fresh.call("POST", "/api/setup", {"username": "admin", "password": "secret123"})
                rep = cloud.apply(fresh.srv, dict(op, body=body, app_user="admin"))
                self.assertEqual(rep.status, 200, rep.body[:300])
                self.assertEqual(books(fresh.db), mine)
            finally:
                fresh.close()
            # the screen can follow an upload: the status call carries its progress (none now)
            self.assertIn("progress", a.call("GET", "/api/cloud-status")[1])
        finally:
            cloud.PART = keep
            a.close(), c.close()
            sb.shutdown()


if __name__ == "__main__":
    unittest.main()
