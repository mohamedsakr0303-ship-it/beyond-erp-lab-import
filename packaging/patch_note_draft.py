"""A draft of the patch notes for one release, from the commit messages since the previous release.

usage: python3 packaging/patch_note_draft.py <previous release sha or ""> <this release sha>
Prints the SQL that adds the draft (the deploy runs it through Supabase). The text travels as base64, so nothing in a
commit message can change the SQL. Attribution lines (Co-Authored-By, Signed-off-by, session links) are left out.
"""
import base64
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAILER = re.compile(r"^\s*(co-authored-by|signed-off-by|claude-session|reviewed-by|helped-by)\s*:", re.I)


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def version():
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)', (ROOT / "erp" / "__init__.py").read_text(encoding="utf-8"))
    return m.group(1) if m else ""


def draft(before, sha):
    rng = f"{before}..{sha}" if before else f"{sha}~20..{sha}"
    commits = [c for c in git("log", "--no-merges", "--format=%B%x1e", rng).split("\x1e") if c.strip()]
    parts, subjects = [], []
    for c in reversed(commits):                       # oldest first, the order things happened
        lines = [ln.rstrip() for ln in c.strip().splitlines() if not TRAILER.match(ln) and "claude.ai/code" not in ln]
        if not lines:
            continue
        subjects.append(lines[0])
        rest = "\n".join(lines[1:]).strip()
        parts.append(f"## {lines[0]}" + (f"\n{rest}" if rest else ""))
    v = version()
    title = subjects[0] if len(subjects) == 1 else f"What's new in {v or datetime.date.today().isoformat()}"
    return {"sha": sha, "version": v, "title": title[:200], "body": "\n\n".join(parts)[:20000]}


if __name__ == "__main__":
    d = draft(sys.argv[1].strip(), sys.argv[2].strip())
    b64 = base64.b64encode(json.dumps(d, ensure_ascii=False).encode("utf-8")).decode("ascii")
    print(f"select public.patch_note_draft(convert_from(decode('{b64}', 'base64'), 'UTF8')::jsonb);")
