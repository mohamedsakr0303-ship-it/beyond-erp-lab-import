"""The public pages' texts the platform owner can rewrite in the owner console (Website texts).

The ERP's script writes each such text as ST("key", "English", "Arabic") (or STh for a text whose line breaks are
kept); this module finds them all, so the console can list every one with its built-in wording. build_web.py writes the
list to site-texts.json next to the console."""
import json
import re
from pathlib import Path

CALL = re.compile(r'\bSTh?\(\s*"([a-z0-9_]+(?:\.[a-z0-9_]+){1,3})"\s*,\s*("(?:[^"\\\n]|\\.)*")\s*,\s*("(?:[^"\\\n]|\\.)*")\s*\)')
KEY = re.compile(r'^[a-z0-9_]+(\.[a-z0-9_]+){1,3}$')     # what the database accepts (site_texts.key)
# where each text appears, by the first part of its key
PLACES = {"home": ("Home page", "الصفحة الرئيسية"), "auth": ("Sign-in screens", "شاشات تسجيل الدخول"),
          "start": ("Getting started (choose or create a company)", "البدء (اختيار شركة أو إنشاؤها)"),
          "downloads": ("Downloads page", "صفحة التنزيلات"), "public": ("Customers' public pages", "الصفحات العامة للعملاء"),
          "foot": ("Footer", "التذييل")}


def extract(js):
    """Every editable text in the script: [{key, en, ar, place}], in the order they appear (each key once)."""
    out, seen = [], {}
    for m in CALL.finditer(js):
        key, en, ar = m.group(1), json.loads(m.group(2)), json.loads(m.group(3))
        if key in seen:
            if seen[key] != (en, ar):
                raise ValueError(f"{key} is written with two different built-in wordings")
            continue
        seen[key] = (en, ar)
        place = key.split(".")[0]
        out.append({"key": key, "en": en, "ar": ar, "place": place, "multiline": "\n" in en or "\n" in ar})
    return out


def stray_calls(js):
    """ST calls that extract() cannot read (not literal): they would be missing from the console."""
    every = len(re.findall(r'\bSTh?\(\s*"', js))
    return every - len(CALL.findall(js))


SOURCES = ("erp/static/app.js", "web-src/p.html")    # the scripts whose ST calls are listed


def catalogue(root=None):
    root = Path(root or Path(__file__).resolve().parents[1])
    texts = extract("\n".join((root / f).read_text(encoding="utf-8") for f in SOURCES if (root / f).exists()))
    return {"places": [{"key": k, "en": en, "ar": ar} for k, (en, ar) in PLACES.items()], "texts": texts}
