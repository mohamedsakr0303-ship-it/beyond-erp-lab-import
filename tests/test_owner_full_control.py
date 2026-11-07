"""Full control from the owner console, on a real PostgreSQL with every migration applied (tests/pg): companies per
plan, user and notification permissions per plan (and per company), the trial's own plan, and the website's texts."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

OWNER = "owner@platform.test"
OP = dict(p_seq=1, p_version="t", p_app_user="", p_device="d", p_method="PUT", p_path="/api/users/2", p_body="{}",
          p_content_type="application/json", p_clock="2026-11-08 10:00:00")


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class OwnerFullControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.ws.sql(f"insert into public.platform_admins(email, name) values ('{OWNER}', 'Owner');")
        cls.owner = cls.ws.as_user(OWNER)
        cfg = cls.owner.rpc("billing_admin_overview")["config"]
        cls.base = cfg["plans"]
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def setUp(self):
        self.owner.rpc("billing_set_config", p={"enabled": True, "trial_full": False, "trial_plan": "business", "plans": self.base,
                                                "max_companies": 0, "accept_new": True})

    def plans(self, code="business", **changes):
        self.set_plans({code: changes})

    def set_plans(self, by_code):
        self.owner.rpc("billing_set_config", p={"plans": [dict(p, **by_code.get(p["code"], {})) for p in self.base]})

    def person(self):
        OwnerFullControl.n += 1
        return self.ws.as_user(f"p{self.n}@x.test")

    def company(self, who, name="Co"):
        return who.rpc("create_company", p_name=f"{name} {self.n}")["id"]

    def pay(self, cid, plan="business"):
        return self.owner.rpc("console_record_payment", p={"company": cid, "plan": plan, "months": 1, "amount": 0})

    def op(self, who, cid, seq, path, body, method="PUT"):
        return who.rpc("append_op", p_company=cid, **dict(OP, p_seq=seq, p_method=method, p_path=path, p_body=json.dumps(body)))

    # ------------------------------------------------------------------ companies per plan
    def test_a_plan_sets_how_many_companies_its_customers_may_own(self):
        self.set_plans({"business": {"companies": 2}, "starter": {"companies": 1}})
        who = self.person()
        first = self.company(who)                                  # on its trial: the trial's plan (business) allows 2
        self.company(who, "Second")
        with self.assertRaisesRegex(pgtest.SQLError, "up to 2 companies on your plan"):
            self.company(who, "Third")
        # a more generous plan on any of their companies lifts it
        self.set_plans({"business": {"companies": 2}, "pro": {"companies": 3}})
        self.pay(first, "pro")
        self.company(who, "Third")
        with self.assertRaisesRegex(pgtest.SQLError, "up to 3 companies"):
            self.company(who, "Fourth")

    def test_the_persons_own_number_beats_the_plan_and_billing_off_ignores_plans(self):
        self.plans("business", companies=1)
        who = self.person()
        self.company(who)
        with self.assertRaisesRegex(pgtest.SQLError, "up to 1 company"):
            self.company(who, "Second")
        self.owner.rpc("console_person_save", p={"email": who.email, "max_companies": 5})
        self.company(who, "Second")                                 # the person's number wins
        other = self.person()
        self.company(other)
        self.owner.rpc("billing_set_config", p={"enabled": False})
        self.company(other, "Second")                               # billing off: plans do not limit companies

    def test_plan_without_a_number_follows_the_general_setting(self):
        self.owner.rpc("billing_set_config", p={"max_companies": 1})
        who = self.person()
        self.company(who)
        with self.assertRaisesRegex(pgtest.SQLError, "up to 1 company"):
            self.company(who, "Second")

    def test_bad_values_are_refused(self):
        for bad in ({"companies": 0}, {"companies": 1.5}, {"companies": "two"}, {"perms": "yes"}, {"notify_perms": 1}):
            with self.assertRaises(pgtest.SQLError, msg=bad):
                self.plans("business", **bad)
        with self.assertRaisesRegex(pgtest.SQLError, "trial follows a plan"):
            self.owner.rpc("billing_set_config", p={"trial_plan": "nope"})

    # ------------------------------------------------------------------ user and notification permissions
    def test_a_plan_without_permissions_locks_restricting_users_and_notification_rules(self):
        self.plans("starter", perms=False, notify_perms=False)
        who = self.person()
        cid = self.company(who)
        st = who.rpc("billing_status", p_company=cid)
        self.assertEqual((st["perms"], st["notify_perms"]), (True, True))          # on its trial (business): included
        self.pay(cid, "starter")
        st = who.rpc("billing_status", p_company=cid)
        self.assertEqual((st["perms"], st["notify_perms"]), (False, False))
        seq = 1
        # full use for everyone, names and the rest go through
        self.op(who, cid, seq, "/api/users/2", {"full_name": "Sara", "perms": {"sales": "edit", "purchasing": "edit"}}); seq += 1
        self.op(who, cid, seq, "/api/users", {"username": "omar", "perms": {"sales": "edit"}}, method="POST"); seq += 1
        self.op(who, cid, seq, "/api/users/2", {"active": False}); seq += 1
        for body in ({"perms": {"sales": "view"}}, {"perms": {"sales": "edit", "sales.offers!export": "no"}},
                     {"perms": {"sales": "edit", "_bus": ["P1"]}}, {"projects": ["P1"]}):
            with self.assertRaisesRegex(pgtest.SQLError, "does not include user permissions", msg=body):
                self.op(who, cid, seq, "/api/users/2", body)
        with self.assertRaisesRegex(pgtest.SQLError, "does not include notification permissions"):
            self.op(who, cid, seq, "/api/alert-access", {"rules": {}})
        self.op(who, cid, seq, "/api/my-alerts", {"name": "mine"}, method="POST"); seq += 1   # a person's own alerts are theirs
        # one company can be given them back (or have them taken) on its own page
        self.owner.rpc("billing_set_company", p_company=cid, p={"overrides": {"perms": True, "notify_perms": True}})
        self.op(who, cid, seq, "/api/users/2", {"perms": {"sales": "view"}}); seq += 1
        self.op(who, cid, seq, "/api/alert-access", {"rules": {}}); seq += 1
        self.owner.rpc("billing_set_company", p_company=cid, p={"overrides": {}})
        self.plans("starter")                                                         # back on for the plan
        self.op(who, cid, seq, "/api/users/2", {"perms": {"sales": "view"}})

    def test_full_use_includes_permissions(self):
        self.plans("business", perms=False, notify_perms=False)
        self.owner.rpc("billing_set_config", p={"trial_full": True})
        who = self.person()
        cid = self.company(who)
        st = who.rpc("billing_status", p_company=cid)
        self.assertEqual((st["status"], st["perms"], st["notify_perms"]), ("trial", True, True))
        self.owner.rpc("billing_set_config", p={"trial_full": False})
        st = who.rpc("billing_status", p_company=cid)
        self.assertEqual((st["perms"], st["notify_perms"]), (False, False))         # the trial follows the plan now
        self.owner.rpc("billing_set_company", p_company=cid, p={"overrides": {"perms": True}})
        self.assertTrue(who.rpc("billing_status", p_company=cid)["perms"])

    def test_a_plan_made_for_the_trial_sets_what_the_trial_includes(self):
        trial = {"code": "trial", "name": "Trial", "price": 0, "users": 2, "companies": 1, "hidden": True, "modules": ["sales"],
                 "deny": ["sales.offers"], "ai": False, "perms": False, "notify_perms": True}
        self.owner.rpc("billing_set_config", p={"plans": self.base + [trial], "trial_plan": "trial", "trial_full": False})
        who = self.person()
        cid = self.company(who)
        st = who.rpc("billing_status", p_company=cid)
        self.assertEqual((st["status"], st["plan"], st["users_allowed"], st["ai"], st["perms"], st["notify_perms"]),
                         ("trial", "trial", 2, False, False, True))
        self.assertIn("sales", st["modules"])
        self.assertIn("sales.offers", st["deny"])
        with self.assertRaisesRegex(pgtest.SQLError, "up to 1 company"):
            self.company(who, "Second")
        pub = self.ws.as_user("", role="anon").rpc("billing_public")
        self.assertNotIn("trial", [p["code"] for p in pub["plans"]])                 # hidden: not offered to customers
        self.owner.rpc("billing_set_config", p={"trial_plan": "business"})

    # ------------------------------------------------------------------ website texts
    def test_website_texts_are_public_to_read_and_edited_by_the_owner_or_a_team_member_allowed(self):
        anon = self.ws.as_user("", role="anon")
        self.assertEqual(anon.rpc("site_texts"), {})
        self.owner.rpc("site_texts_save", p={"home.hero.tag": {"en": "  RUN IT ALL  ", "ar": "أدر كل شيء"}, "auth.welcome": {"en": "Hello.", "ar": ""}})
        self.assertEqual(anon.rpc("site_texts"), {"home.hero.tag": {"en": "RUN IT ALL", "ar": "أدر كل شيء"}, "auth.welcome": {"en": "Hello.", "ar": ""}})
        adm = self.owner.rpc("site_texts_admin")
        self.assertEqual(adm["home.hero.tag"]["updated_by"], OWNER)
        self.owner.rpc("site_texts_save", p={"auth.welcome": {"en": "", "ar": ""}})        # both blank: the built-in wording again
        self.assertNotIn("auth.welcome", anon.rpc("site_texts"))
        for bad in ({"Bad Key": {"en": "x"}}, {"home": {"en": "x"}}, {"home.x": "plain"}, {"home.x": {"en": "x" * 4001}}):
            with self.assertRaises(pgtest.SQLError, msg=bad):
                self.owner.rpc("site_texts_save", p=bad)
        with self.assertRaises(pgtest.SQLError):
            anon.rpc("site_texts_save", p={"home.x": {"en": "x"}})
        with self.assertRaises(pgtest.SQLError):
            self.ws.as_user("customer@x.test").rpc("site_texts_save", p={"home.x": {"en": "x"}})
        with self.assertRaises(pgtest.SQLError):
            self.ws.as_user("customer@x.test").select("select * from public.site_texts")
        # a support team member: only once the owner gives them the website texts
        self.owner.rpc("console_staff_save", p={"email": "help@platform.test", "name": "Help", "full": False, "perms": {"support": True}, "active": True})
        helper = self.ws.as_user("help@platform.test")
        with self.assertRaisesRegex(pgtest.SQLError, "site"):
            helper.rpc("site_texts_save", p={"home.x": {"en": "x"}})
        self.owner.rpc("console_staff_save", p={"email": "help@platform.test", "name": "Help", "full": False, "perms": {"support": True, "site": True}, "active": True})
        helper.rpc("site_texts_save", p={"home.x": {"en": "From support", "ar": ""}})
        self.assertEqual(anon.rpc("site_texts")["home.x"]["en"], "From support")
        self.assertIn("site.texts", self.ws.sql("select action from public.platform_log where action = 'site.texts' limit 1;"))


class WebsiteTextsCatalogue(unittest.TestCase):
    """The texts the console lists come straight from the ERP's script, so every one it shows really appears."""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        from erp import sitetexts
        self.st = sitetexts
        self.js = (ROOT / "erp" / "static" / "app.js").read_text(encoding="utf-8")
        self.cat = sitetexts.catalogue(ROOT)

    def test_every_editable_text_is_listed_with_both_languages(self):
        import re
        self.assertEqual(self.st.stray_calls(self.js), 0)                    # none written in a way the list cannot read
        keys = [t["key"] for t in self.cat["texts"]]
        self.assertEqual(len(keys), len(set(keys)))
        places = {p["key"] for p in self.cat["places"]}
        for t in self.cat["texts"]:
            self.assertRegex(t["key"], r"^[a-z0-9_]+(\.[a-z0-9_]+){1,3}$")    # what the database accepts
            self.assertIn(t["place"], places, t["key"])
            self.assertTrue(t["en"].strip() and t["ar"].strip(), t["key"])
        for k in ("home.hero.tag", "home.hero.sub", "home.books.title", "home.band.title", "home.chip.accounting", "home.btn.start",
                  "auth.welcome", "auth.signup", "auth.trial", "foot.email", "foot.linkedin", "foot.rights"):
            self.assertIn(k, keys)
        self.assertTrue(next(t for t in self.cat["texts"] if t["key"] == "home.books.title")["multiline"])
        self.assertIn("{days}", next(t for t in self.cat["texts"] if t["key"] == "auth.trial")["en"])

    def test_the_home_page_has_no_fixed_marketing_wording_left(self):
        i = self.js.index("function renderLanding(opt = {}) {")
        body = self.js[i:self.js.index("\n}\n", i)]
        import re
        fixed = re.findall(r'L2\(\s*"([^"]+)"', body)
        # only the controls of the page itself stay fixed (animation switch, the way back into the app, labels for screen readers)
        self.assertTrue(set(fixed) <= {"Back to BeyondERP", "Welcome screen", "Turn welcome screen animation on or off", "✦ Animation: off",
                                       "✦ Animation: on", "Signed in", "Return to the application", "Sections"}, set(fixed))

    def test_two_wordings_for_one_key_are_refused(self):
        with self.assertRaises(ValueError):
            self.st.extract('ST("home.a", "One", "واحد") + ST("home.a", "Two", "اثنان")')
        self.assertEqual(len(self.st.extract('ST("home.a", "One", "واحد") + STh("home.a", "One", "واحد")')), 1)

    def test_the_build_publishes_the_list_and_the_console_and_team_offer_it(self):
        self.assertIn("site-texts.json", (ROOT / "build_web.py").read_text(encoding="utf-8"))
        own = (ROOT / "web-src" / "owner.js").read_text(encoding="utf-8")
        self.assertIn("PAGES.texts = ", own)
        self.assertIn('["texts", "✎", L("Website texts", "نصوص الموقع"), "site"]', own)        # menu item, for the area "site"
        self.assertIn('["site", L("Website texts', own)                                          # a team member can be given it
        self.assertIn("async siteTexts()", (ROOT / "web-src" / "bridge.js").read_text(encoding="utf-8"))

    def test_the_plan_editor_and_company_page_carry_the_new_switches(self):
        own = (ROOT / "web-src" / "owner.js").read_text(encoding="utf-8")
        for k in ('{ k: "companies"', '{ k: "perms"', '{ k: "notify_perms"', 'tri("perms"', 'tri("notify_perms"', 'data-a="trialplan"'):
            self.assertIn(k, own)
        js = self.js
        for k in ("planHasPerms()", "planHasNotifyPerms()", 'S.billing.perms === false', 'S.billing.notify_perms === false'):
            self.assertIn(k, js)


if __name__ == "__main__":
    unittest.main()
