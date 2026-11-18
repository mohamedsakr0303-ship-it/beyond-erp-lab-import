"""The "you were added to a company" notices through the real edge functions (supabase/functions/manage-user and push),
under Deno with the database, the email service and a device's push endpoint stood in for: an invitation sends one push
(encrypted for the device) and one email, a second save sends nothing, an outsider cannot send it, an internal address
gets no email. Skipped where Deno is not installed (set DENO to its path to run it)."""
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DENO = os.environ.get("DENO") or shutil.which("deno")


@unittest.skipUnless(DENO, "Deno is not installed")
class EdgeNotices(unittest.TestCase):
    def test_invite_notices(self):
        r = subprocess.run([DENO, "run", "--allow-env", "--allow-read", "--no-prompt", "tests/edge/notices_test.ts"],
                           cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["fails"], [])
        self.assertEqual(out["pushes"], 1)
        self.assertEqual([m["to"] for m in out["mails"]], [["sama@x.com"], ["nada@n.test"]])


if __name__ == "__main__":
    unittest.main()
