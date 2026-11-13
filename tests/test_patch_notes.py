"""Patch notes on a real PostgreSQL with every migration applied (tests/pg): a release adds a draft once; the owner and
the support team with the Help & FAQ right edit and publish; customers see only what was published."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "pg"))
import pgtest  # noqa: E402

OWNER = "owner@platform.test"
AGENT = "agent@platform.test"
SHADOW = "shadow@platform.test"


@unittest.skipUnless(pgtest.available(), "PostgreSQL is not installed")
class PatchNotes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = pgtest.Workspace().__enter__()
        cls.ws.sql(f"insert into public.platform_admins(email, name) values ('{OWNER}', 'Owner');")
        cls.owner = cls.ws.as_user(OWNER)
        cls.owner.rpc("console_staff_save", p={"email": AGENT, "name": "Agent", "perms": {"support": True}})
        cls.owner.rpc("console_staff_save", p={"email": SHADOW, "name": "New", "perms": {"support": True, "desk": {"notes": True}}})
        cls.agent, cls.shadow = cls.ws.as_user(AGENT), cls.ws.as_user(SHADOW)
        cls.visitor = cls.ws.as_user("customer@x.test")

    @classmethod
    def tearDownClass(cls):
        cls.ws.__exit__(None, None, None)

    def draft(self, sha, body):
        p = json.dumps({"sha": sha, "version": "2.1", "title": "New release", "body": body}).replace("'", "''")
        self.ws.sql(f"select public.patch_note_draft('{p}'::jsonb);")

    def test_a_release_adds_one_draft_that_customers_do_not_see(self):
        self.draft("abc1", "- Faster reports")
        self.draft("abc1", "- something else")                       # the same release again: unchanged
        notes = [n for n in self.owner.rpc("console_patch_notes") if n["release_sha"] == "abc1"]
        self.assertEqual(len(notes), 1)
        self.assertEqual((notes[0]["status"], notes[0]["body"]), ("draft", "- Faster reports"))
        self.assertEqual([n for n in self.visitor.rpc("patch_notes_published") if n["id"] == notes[0]["id"]], [])

    def test_the_team_edits_and_publishes(self):
        self.draft("abc2", "- raw text")
        nid = next(n["id"] for n in self.agent.rpc("console_patch_notes") if n["release_sha"] == "abc2")
        self.agent.rpc("console_patch_note", p={"id": nid, "title": "Better invoices", "title_ar": "فواتير أفضل",
                                                "body": "- Clearer totals", "body_ar": "- إجماليات أوضح", "version": "2.1"})
        self.agent.rpc("console_patch_note", p={"id": nid, "action": "publish"})
        pub = [n for n in self.visitor.rpc("patch_notes_published") if n["id"] == nid]
        self.assertEqual((pub[0]["title"], pub[0]["body_ar"]), ("Better invoices", "- إجماليات أوضح"))
        self.draft("abc2", "- raw text again")                         # a later run of the same release keeps the edit
        self.assertEqual([n for n in self.visitor.rpc("patch_notes_published") if n["id"] == nid][0]["body"], "- Clearer totals")
        self.agent.rpc("console_patch_note", p={"id": nid, "action": "unpublish"})
        self.assertEqual([n for n in self.visitor.rpc("patch_notes_published") if n["id"] == nid], [])

    def test_only_the_team_with_the_help_right_edits(self):
        with self.assertRaises(pgtest.SQLError):
            self.visitor.rpc("console_patch_notes")
        with self.assertRaises(pgtest.SQLError):
            self.shadow.rpc("console_patch_note", p={"title": "x", "body": "y"})   # a support account without Help & FAQ
        with self.assertRaises(pgtest.SQLError):
            self.visitor.rpc("patch_note_draft", p={"sha": "evil", "body": "x"})  # only the deploy writes drafts

    def test_a_note_needs_its_text_before_publishing(self):
        nid = [n for n in self.owner.rpc("console_patch_note", p={"title": "Hand-written", "body": ""}) if n["title"] == "Hand-written"][0]["id"]
        with self.assertRaises(pgtest.SQLError):
            self.owner.rpc("console_patch_note", p={"id": nid, "action": "publish"})


if __name__ == "__main__":
    unittest.main()
