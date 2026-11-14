"""Large changes and copies of the books arrive in pieces (supabase/migrations/*_big_changes.sql), on a real PostgreSQL
with every migration applied (tests/pg): a change or copy that names its pieces is accepted only when all arrived;
members read the pieces back; others cannot; unused pieces are not kept forever; old copies' pieces go with them."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

OP = dict(p_version="t", p_app_user="", p_device="d", p_method="POST", p_path="/api/import?format=parsed",
          p_content_type="application/x-beyond-import", p_clock="2026-11-14 10:00:00")
PID = "abcdef0123456789abcd"


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class BigChanges(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.admin = cls.ws.as_user("boss@co.test")
        cls.cid = cls.admin.rpc("create_company", p_name="Big Co")["id"]
        cls.admin.rpc("set_member", p_company=cls.cid, p_email="clerk@co.test", p_is_admin=False, p_gateway=False, p_active=True)
        cls.clerk, cls.stranger = cls.ws.as_user("clerk@co.test"), cls.ws.as_user("nobody@else.test")
        cls.seq = 0

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def op(self, who, body):
        r = who.rpc("append_op", p_company=self.cid, p_seq=self.seq + 1, p_body=body, **OP)
        type(self).seq += 1
        return r

    def test_a_change_in_pieces_is_accepted_once_every_piece_arrived(self):
        pid = PID[:-1] + "1"
        self.clerk.rpc("put_part", p_company=self.cid, p_id=pid, p_idx=0, p_data="b64:AAAA")
        with self.assertRaises(pgtest.SQLError):                  # piece 2 of 2 is missing
            self.op(self.clerk, f"parts:{pid}:2")
        self.clerk.rpc("put_part", p_company=self.cid, p_id=pid, p_idx=1, p_data="BBBB")
        with self.assertRaises(pgtest.SQLError):                  # claims fewer pieces than were sent
            self.op(self.clerk, f"parts:{pid}:1")
        self.op(self.clerk, f"parts:{pid}:2")
        rows = self.admin.select(f"select idx, data, kind from public.op_parts where part_id = '{pid}' order by idx")
        self.assertEqual([(r["idx"], r["data"], r["kind"]) for r in rows], [(0, "b64:AAAA", "op"), (1, "BBBB", "op")])
        self.clerk.rpc("put_part", p_company=self.cid, p_id=pid, p_idx=0, p_data="changed")   # a used piece never changes
        self.assertEqual(self.clerk.select(f"select data from public.op_parts where part_id = '{pid}' and idx = 0")[0]["data"], "b64:AAAA")

    def test_ordinary_changes_are_unaffected(self):
        self.op(self.clerk, '{"code": "X"}')
        with self.assertRaises(pgtest.SQLError):
            self.op(self.clerk, "parts:bad")

    def test_only_members_send_and_read_pieces(self):
        with self.assertRaises(pgtest.SQLError):
            self.stranger.rpc("put_part", p_company=self.cid, p_id=PID[:-1] + "2", p_idx=0, p_data="x")
        self.clerk.rpc("put_part", p_company=self.cid, p_id=PID[:-1] + "3", p_idx=0, p_data="secret")
        self.assertEqual(self.stranger.select("select * from public.op_parts"), [])
        with self.assertRaises(pgtest.SQLError):
            self.stranger.execute("insert into public.op_parts(company_id, part_id, idx, data) values "
                                  f"('{self.cid}', '{PID}', 0, 'x');")
        with self.assertRaises(pgtest.SQLError):                  # the inner functions are not callable directly
            self.clerk.rpc("beyond_take_parts", p_company=self.cid, p_ref="parts:x:1", p_kind="op")
        for bad in (dict(p_id="UPPER-CASE-ID-123456", p_idx=0), dict(p_id=PID, p_idx=-1), dict(p_id=PID, p_idx=5000)):
            with self.assertRaises(pgtest.SQLError):
                self.clerk.rpc("put_part", p_company=self.cid, p_data="x", **bad)

    def test_unused_pieces_go_after_a_day(self):
        pid = PID[:-1] + "4"
        self.clerk.rpc("put_part", p_company=self.cid, p_id=pid, p_idx=0, p_data="left behind")
        self.ws.sql(f"update public.op_parts set created_at = now() - interval '2 days' where part_id = '{pid}';")
        self.clerk.rpc("put_part", p_company=self.cid, p_id=PID[:-1] + "5", p_idx=0, p_data="new")
        self.assertEqual(self.admin.select(f"select * from public.op_parts where part_id = '{pid}'"), [])

    def test_copies_in_pieces_and_old_copies_take_their_pieces_along(self):
        seqs = []
        for i in range(4):
            self.op(self.admin, '{"n": %d}' % i)
            seqs.append(self.seq)
            pid = PID[:-2] + f"s{i}"
            self.admin.rpc("put_part", p_company=self.cid, p_id=pid, p_idx=0, p_data=f"copy{i}")
            self.admin.rpc("put_snapshot", p_company=self.cid, p_seq=self.seq, p_version="t", p_data=f"parts:{pid}:1")
        kept = self.admin.select("select part_id from public.op_parts where kind = 'snap' order by part_id")
        self.assertEqual([r["part_id"] for r in kept], [PID[:-2] + f"s{i}" for i in (1, 2, 3)])   # the latest three copies
        with self.assertRaises(pgtest.SQLError):                  # a copy whose piece never arrived
            self.admin.rpc("put_snapshot", p_company=self.cid, p_seq=self.seq, p_version="t", p_data=f"parts:{PID[:-2]}zz:1")
        with self.assertRaises(pgtest.SQLError):                  # copies: administrators only, as before
            self.clerk.rpc("put_snapshot", p_company=self.cid, p_seq=self.seq, p_version="t", p_data="plain")


if __name__ == "__main__":
    unittest.main()
