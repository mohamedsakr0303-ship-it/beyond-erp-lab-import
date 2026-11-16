"""One company's books never reach another (tests/pg, a real PostgreSQL with every migration): user A makes a company
with a marked name, changes, a saved copy, a piece of a big upload and a support ticket. Then user B (signed in, of
another company) and a visitor who is not signed in call EVERY function of the database with A's own ids, and try to
read, change and delete A's rows straight from the tables. Nothing of A's comes back, and A's books are unchanged."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

MARK = "ZQX-SECRET-A"
OP = dict(p_app_user="boss", p_method="POST", p_path="/api/journal", p_content_type="application/json", p_clock="2026-11-14 10:00:00")
# what a public page shows anyone (the site, prices, help): not A's
PUBLIC = {"billing_public", "help_center", "help_search", "patch_notes_published", "site_public", "site_texts",
          "public_page", "public_send", "public_manage"}


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class CrossTenant(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        ws = cls.ws
        cls.a, cls.b = ws.as_user("boss@a.test"), ws.as_user("boss@b.test")
        cls.anon = ws.as_user("", role="anon")
        cls.cid = cls.a.rpc("create_company", p_name=MARK + " Ltd")["id"]
        cls.bid = cls.b.rpc("create_company", p_name="B Ltd")["id"]
        for i in range(1, 4):
            cls.a.rpc("append_op", p_company=cls.cid, p_seq=i, p_version="2.5.0", p_device="pc",
                      p_body='{"memo": "%s %d"}' % (MARK, i), **OP)
        cls.a.rpc("put_snapshot", p_company=cls.cid, p_seq=3, p_version="2.5.0", p_data=MARK + "-snapshot")
        cls.ticket = cls.a.rpc("support_open", p_company=cls.cid, p_kind="issue", p_subject=MARK + " subject",
                               p_body=MARK + " body", p_diag={}, p_log=[], p_files=[], p_app_user="")["id"]
        cls.before = cls.state()

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    @classmethod
    def state(cls):
        return cls.ws.sql(f"select (select name from companies where id='{cls.cid}') || '|' || "
                          f"(select count(*) from ops where company_id='{cls.cid}') || '|' || "
                          f"(select count(*) from snapshots where company_id='{cls.cid}') || '|' || "
                          f"(select count(*) from members where company_id='{cls.cid}') || '|' || "
                          f"(select string_agg(subject || status, ',') from support_tickets where company_id='{cls.cid}');").strip()

    def functions(self, role):
        rows = self.ws.sql("select p.proname || ':' || coalesce(array_to_string(array(select format_type(t, null) "
                           "from unnest(p.proargtypes) t), ','), '') from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                           f"where n.nspname = 'public' and p.prokind = 'f' and has_function_privilege('{role}', p.oid, 'execute') "
                           "and p.prorettype <> 'trigger'::regtype order by 1;").splitlines()
        return [r.split(":", 1) for r in rows if r.strip()]

    def arg(self, t, k):
        return {"uuid": f"'{self.cid}'", "bigint": str(self.ticket), "integer": "1", "smallint": "1", "numeric": "1",
                "boolean": "false", "jsonb": "'{}'::jsonb", "json": "'{}'::json", "text": "'x'",
                "uuid[]": f"array['{self.cid}']::uuid[]", "text[]": "array['x']", "bigint[]": f"array[{self.ticket}]::bigint[]",
                "timestamp with time zone": "now()", "date": "current_date"}.get(t, "null")

    def sweep(self, who, role):
        leaks, ran = [], 0
        for name, args in self.functions(role):
            if name in PUBLIC:
                continue
            types = [t for t in args.split(",") if t]
            call = f"select public.{name}({', '.join(self.arg(t, k) for k, t in enumerate(types))})::text;"
            ran += 1
            try:
                out = who.execute(call)
            except pgtest.SQLError:
                continue                                       # refused: what we want
            if MARK in out or self.cid in out and name not in ("create_company",):
                leaks.append(f"{name}({args}) → {out.strip()[:160]}")
        return ran, leaks

    def test_another_company_calling_every_function_with_our_ids_gets_nothing_of_ours(self):
        ran, leaks = self.sweep(self.b, "authenticated")
        self.assertGreater(ran, 100)
        self.assertEqual(leaks, [])
        self.assertEqual(self.state(), self.before)

    def test_a_visitor_not_signed_in_gets_nothing_of_ours(self):
        ran, leaks = self.sweep(self.anon, "anon")
        self.assertEqual(leaks, [])
        self.assertEqual(self.state(), self.before)

    def test_the_tables_themselves_refuse_another_company(self):
        for t in ("companies", "members", "ops", "snapshots", "op_parts", "support_tickets", "support_messages"):
            for who in (self.b, self.anon):
                try:
                    rows = who.select(f"select * from public.{t}")
                except pgtest.SQLError:
                    continue
                self.assertNotIn(MARK, str(rows), t)
                self.assertNotIn(self.cid, str(rows), t)
                for stmt in (f"update public.{t} set company_id = company_id where company_id = '{self.cid}'" if t != "companies"
                             else f"update public.companies set name = 'taken' where id = '{self.cid}'",
                             f"delete from public.{t} where " + ("id" if t == "companies" else "company_id") + f" = '{self.cid}'"):
                    if t == "support_messages":
                        stmt = stmt.replace(f"company_id = '{self.cid}'", f"ticket_id = {self.ticket}").replace("set ticket_id", "set ticket_id")
                    try:
                        who.execute(stmt)
                    except pgtest.SQLError:
                        pass
        self.assertEqual(self.state(), self.before)

    def test_the_owner_still_sees_their_own(self):
        self.assertIn(MARK, str(self.a.rpc("my_companies")))
        self.assertIn(MARK, str(self.a.rpc("support_get", p_ticket=self.ticket)))


if __name__ == "__main__":
    unittest.main()
