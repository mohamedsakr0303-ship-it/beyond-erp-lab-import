"""The audit log's actions read as words in the reader's language, never as codes."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erp import auditlabels, auth, server  # noqa: E402
from erp.db import Database  # noqa: E402


class App:
    pass


class Labels(unittest.TestCase):
    def test_verb_and_object(self):
        self.assertEqual(auditlabels.label("account.add"), "Add account")
        self.assertEqual(auditlabels.label("account.add", "ar"), "إضافة حساب")
        self.assertEqual(auditlabels.label("payroll.run.approve", "ar"), "اعتماد مسير الرواتب")
        self.assertEqual(auditlabels.label("login"), "Sign-in")
        self.assertEqual(auditlabels.label("rent.lot_adjust", "ar"), "تسوية دفعة معدات")

    def test_unknown_reads_as_words(self):
        self.assertEqual(auditlabels.label("new_thing.do_it"), "New thing do it")
        self.assertEqual(auditlabels.label(""), "")

    def test_the_log_carries_labels_in_arabic(self):
        db = Database(":memory:")
        auth.create_user(db, {"username": "boss", "password": "pw123456"}, first=True)
        app = App()
        app.db, app.backup = db, (lambda *a, **k: None)
        tok = auth.login(db, "boss", "pw123456")[0]
        db.log("boss", "account.add", "first administrator created")
        r = server.handle(app, "GET", "/api/audit", {"x-session": tok, "x-requested-with": "BeyondERP", "x-lang": "ar"}, b"")
        rows = json.loads(r.body)
        rows = rows.get("rows", rows) if isinstance(rows, dict) else rows
        self.assertTrue(rows)
        self.assertTrue(all(x.get("action_label") for x in rows))
        self.assertFalse(any("." in x["action_label"] for x in rows))
        self.assertIn("إضافة حساب", [x["action_label"] for x in rows])
        self.assertIn("تم إنشاء أول مسؤول", [x.get("detail_text") for x in rows])


if __name__ == "__main__":
    unittest.main()
