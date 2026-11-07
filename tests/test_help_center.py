"""Help & FAQ, bug reports and shared solved issues on a real PostgreSQL with every migration applied (tests/pg):
everyone reads and searches the FAQ and the shared issues; the team answers every report before closing it and
decides whether to share it; sharing publishes only the title and the resolution the team writes."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

OWNER = "owner@platform.test"
AGENT = "agent@platform.test"


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class HelpCenter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.ws.sql(f"insert into public.platform_admins(email, name) values ('{OWNER}', 'Owner');")
        cls.owner = cls.ws.as_user(OWNER)
        cls.owner.rpc("console_staff_save", p={"email": AGENT, "name": "Sara Adel", "perms": {"support": True}})
        cls.agent = cls.ws.as_user(AGENT)

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def test_the_guides_are_published_in_both_languages_with_their_pictures(self):
        """The guides migration: every guide in English and Arabic, under the answer limit, each picture and clip it shows
        is in erp/static/help (in the guide's own language), and running the migration again adds nothing."""
        import re
        guides = [f for f in self.ws.as_user("visitor@x.test").rpc("help_center")["faq"] if f["q"].startswith((
            "Getting started", "Opening balances", "Correcting a posted", "Hard entries", "Fixed assets", "How stock is valued",
            "Progress bills", "Subcontractor certificates", "Partners: how", "Importing from Excel"))]
        self.assertEqual(len(guides), 10)
        static = Path(__file__).resolve().parent.parent / "erp" / "static"
        for g in guides:
            for lang, k in (("en", "a"), ("ar", "a_ar")):
                text = g[k]
                self.assertTrue(text and len(text) <= 20000, g["q"])
                self.assertTrue(g["q_ar"] and re.search("[\u0600-\u06FF]", g["q_ar"]), g["q"])
                if lang == "ar":
                    self.assertTrue(re.search("[\u0600-\u06FF]", text), g["q"])
                for src in re.findall(r"!\[[^\]]*\]\((help/[^)]+)\)", text):
                    self.assertTrue((static / src).is_file(), src)
                    if re.search(r"-(en|ar)\.\w+$", src):
                        self.assertTrue(src.rsplit(".", 1)[0].endswith("-" + lang), f"{g['q']}: {src} in the {lang} answer")
        cats = {c["key"] for c in self.ws.as_user("visitor@x.test").rpc("help_center")["categories"]}
        self.assertTrue({"projects", "partners"} <= cats)
        mig = Path(__file__).resolve().parent.parent / "supabase" / "migrations" / "20261107000000_help_guides.sql"
        self.ws.sql(mig.read_text(encoding="utf-8"))                       # again: nothing is added twice
        self.assertEqual(len([f for f in self.agent.rpc("console_help")["faq"] if f["updated_by"] == "BeyondERP"]), 10)

    def test_faq_by_category_and_search_in_both_languages(self):
        h = self.agent.rpc("console_help_faq", p={"category": "sales", "q": "How do I print an invoice?", "q_ar": "كيف أطبع فاتورة؟",
                                                   "a": "Open the invoice and choose Print.", "a_ar": "افتح الفاتورة واختر طباعة."})
        self.agent.rpc("console_help_faq", p={"category": "stock", "q": "Hidden draft", "a": "Not yet", "published": False})
        self.assertEqual(len([f for f in h["faq"] if f["updated_by"] != "BeyondERP"]), 1)       # besides the published guides
        anyone = self.ws.as_user("visitor@x.test")
        c = anyone.rpc("help_center")
        self.assertIn("sales", [x["key"] for x in c["categories"]])
        self.assertEqual([f["q"] for f in c["faq"] if f["q"] in ("How do I print an invoice?", "Hidden draft")],
                         ["How do I print an invoice?"])             # drafts stay hidden
        self.assertEqual(anyone.rpc("help_search", p_q="print invoice")[0]["title"], "How do I print an invoice?")
        self.assertEqual(anyone.rpc("help_search", p_q="فاتورة")[0]["src"], "faq")
        self.assertEqual(anyone.rpc("help_search", p_q="draft"), [])
        self.assertEqual(anyone.rpc("help_search", p_q="  "), [])
        with self.assertRaises(pgtest.SQLError):
            anyone.rpc("console_help_faq", p={"q": "x", "a": "y"})

    def test_a_bug_is_answered_closed_and_shared_without_anything_about_the_customer(self):
        cust = self.ws.as_user("reporter@x.test")
        cid = cust.rpc("create_company", p_name="Secret Trading LLC")["id"]
        t = cust.rpc("support_open", p_company=cid, p_kind="bug", p_subject="Ahmed's VAT return shows 0 for Secret Trading",
                     p_body="My client Ahmed, phone 0100..., the VAT return is empty", p_diag={"version": "1.0"},
                     p_log=[{"t": "error", "m": "boom"}], p_files=[], p_app_user="")
        bugs = self.agent.rpc("console_bugs")
        self.assertEqual((bugs[0]["ref"], bugs[0]["errors"]), (t["ref"], 1))
        with self.assertRaisesRegex(pgtest.SQLError, "answer the customer before closing"):
            self.agent.rpc("console_ticket_close", p_ticket=t["id"], p={"share": False})
        with self.assertRaisesRegex(pgtest.SQLError, "write a short title"):
            self.agent.rpc("console_ticket_close", p_ticket=t["id"], p={"share": True, "reply": "Fixed in 1.1", "title": ""})
        self.agent.rpc("console_ticket_close", p_ticket=t["id"], p={"share": True, "reply": "Fixed in version 1.1 — please refresh.",
            "category": "accounts", "title": "VAT return shows zero after a refund", "title_ar": "إقرار الضريبة يظهر صفراً بعد المرتجع",
            "resolution": "Fixed in version 1.1. Refresh the page.", "resolution_ar": "تم الإصلاح في الإصدار 1.1. حدّث الصفحة."})
        tk = cust.rpc("support_get", p_ticket=t["id"])
        self.assertEqual((tk["status"], tk["messages"][-1]["body"]), ("closed", "Fixed in version 1.1 — please refresh."))
        shared = self.ws.as_user("other@x.test").rpc("help_center")["solved"]
        self.assertEqual(len(shared), 1)
        text = json.dumps(shared[0], ensure_ascii=False)
        for private in ("Ahmed", "Secret", "reporter", "0100", t["ref"], "ticket"):
            self.assertNotIn(private, text)                                          # nothing about the customer
        self.assertEqual(set(shared[0]), {"id", "category", "kind", "title", "title_ar", "resolution", "resolution_ar", "date"})
        self.assertEqual(self.ws.as_user("other@x.test").rpc("help_search", p_q="vat zero refund")[0]["src"], "solved")
        # hidden again: gone from the list and the search
        sid = shared[0]["id"]
        self.agent.rpc("console_help_solved", p={"id": sid, "title": "VAT return shows zero after a refund", "resolution": "Fixed.", "published": False})
        self.assertEqual(self.ws.as_user("other@x.test").rpc("help_center")["solved"], [])
        self.assertEqual([x for x in self.ws.as_user("other@x.test").rpc("help_search", p_q="vat zero") if x["src"] == "solved"], [])

    def test_closing_without_sharing(self):
        cust = self.ws.as_user("quiet@x.test")
        cid = cust.rpc("create_company", p_name="Quiet Co")["id"]
        t = cust.rpc("support_open", p_company=cid, p_kind="issue", p_subject="Private question", p_body="", p_diag={}, p_log=[],
                     p_files=[], p_app_user="")
        self.agent.rpc("support_reply", p_ticket=t["id"], p_body="Answered privately", p_files=[], p_internal=False)
        before = len(self.agent.rpc("console_help")["solved"])
        self.agent.rpc("console_ticket_close", p_ticket=t["id"], p={"share": False})
        self.assertEqual(len(self.agent.rpc("console_help")["solved"]), before)
        self.assertEqual(cust.rpc("support_get", p_ticket=t["id"])["shared"], False)


if __name__ == "__main__":
    unittest.main()
