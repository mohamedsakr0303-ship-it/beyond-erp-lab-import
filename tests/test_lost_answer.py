"""A change reaches the shared workspace but its answer is lost (the connection drops): trying again does not save it twice."""
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

from erp import cloud  # noqa: E402
from erp.db import Database  # noqa: E402


class LostAnswer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from erp.server import ERPServer
        import mock_supabase
        cls.sb = mock_supabase.start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Database(Path(cls.tmp.name) / "a.sqlite3")
        cls.srv = ERPServer(("127.0.0.1", 0), cls.db, Path(cls.tmp.name) / "bk")
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    @classmethod
    def tearDownClass(cls):
        cls.srv.cloud.stop()
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.db.close()
        cls.sb.shutdown()
        cls.tmp.cleanup()

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "X-Requested-With": "BeyondERP"})
        try:
            with self.op.open(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_trying_again_after_a_lost_answer_saves_once(self):
        self.assertEqual(self.call("POST", "/api/setup", {"username": "admin", "password": "secret123"})[0], 200)
        self.assertEqual(self.call("PUT", "/api/cloud/config", {"url": f"http://127.0.0.1:{self.sb.server_address[1]}", "key": "anon"})[0], 200)
        self.assertEqual(self.call("POST", "/api/cloud/signin", {"email": "owner@x.com", "password": "pw123456", "create": True})[0], 200)
        self.assertEqual(self.call("POST", "/api/cloud/claim", {})[0], 200)
        link = self.srv.cloud
        real = link.client

        def dropping():
            c = real()
            rpc = c.rpc

            def once(fn, params, token=None):
                out = rpc(fn, params, token)
                if fn == "append_op":
                    link.client = real                       # only the first answer is lost
                    raise cloud.Offline("The connection dropped")
                return out
            c.rpc = once
            return c
        link.client = dropping
        body = {"code": "LOST1", "name": "Saved once"}
        st, r = self.call("POST", "/api/bus", body)
        self.assertEqual(st, 503, r)                         # the screen hears nothing back
        self.assertEqual(len(self.sb.state.all_ops()), 1)    # but the workspace has it
        st, r = self.call("POST", "/api/bus", body)          # the person tries again
        self.assertEqual(st, 200, r)
        self.assertEqual(len(self.sb.state.all_ops()), 1)    # not a second time
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM business_units WHERE code='LOST1'")["n"], 1)
        st, r = self.call("POST", "/api/bus", {"code": "NEXT1", "name": "Another"})   # a different change goes through
        self.assertEqual(st, 200, r)
        self.assertEqual(len(self.sb.state.all_ops()), 2)


if __name__ == "__main__":
    unittest.main()
