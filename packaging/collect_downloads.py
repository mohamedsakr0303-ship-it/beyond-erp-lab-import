"""Put the public download (the e-invoice Signer) into web/downloads/ with a small index the app reads
(downloads/beyond-downloads.json). Files over Cloudflare Pages' 25 MB limit are left out with a warning.

  python packaging/collect_downloads.py <artifacts folder> [site url]

Installers not built in this run (the app code did not change) are copied from the live site, so the download page
keeps offering them."""
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 25 * 1024 * 1024 - 4096
src = Path(sys.argv[1] if len(sys.argv) > 1 else "dl")
site = (sys.argv[2] if len(sys.argv) > 2 else "").rstrip("/")
out = ROOT / "web" / "downloads"
out.mkdir(parents=True, exist_ok=True)
version = re.search(r'__version__ = "([^"]+)"', (ROOT / "erp" / "__init__.py").read_text()).group(1)
UA = {"User-Agent": "Mozilla/5.0 (beyond-erp build)"}  # Cloudflare refuses Python's default user agent


def fetch(url, timeout):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


live = {}
if site:
    try:
        live = json.loads(fetch(f"{site}/downloads/beyond-downloads.json", 30))
    except (OSError, ValueError):
        print("::notice::No downloads on the live site yet")
live_files = {f["file"]: f for f in live.get("files", [])}
reused = False
# The ERP is the browser app (installed from the browser): no desktop app is offered. The one download is the e-invoice
# Signer, for the computer the USB token is plugged into (a browser cannot reach the token). The offline editions carry
# the whole app, so they are never put on the website.
wanted = [("signer-windows", ["BeyondERP-Signer-Setup.exe", "BeyondERP-Signer.exe"])]
files = []


def built(name):
    return next(iter(sorted(src.rglob(name))), None) or (ROOT / name if (ROOT / name).exists() else None)


for kind, names in wanted:
    name, found = next(((n, built(n)) for n in names if built(n)), (names[0], None))
    if not found:
        kept = False
        for n in names:
            if n not in live_files:
                continue
            try:  # not rebuilt this time: keep the one the site already offers
                (out / n).write_bytes(fetch(f"{site}/downloads/{n}", 300))
                files.append({**live_files[n], "kind": kind})
                reused = kept = True
                print(f"{n}: kept from the live site")
                break
            except OSError as e:
                print(f"::warning::{n} could not be copied from the live site ({e})")
        if kept:
            continue
        print(f"::warning::{names[0]} was not built — the download page will say so")
        continue
    if found.stat().st_size > LIMIT:
        print(f"::warning::{name} is {found.stat().st_size / 1048576:.1f} MB — over the 25 MB hosting limit, left out")
        continue
    shutil.copy(found, out / name)
    files.append({"kind": kind, "file": name, "size": found.stat().st_size})
    print(f"{name}: {found.stat().st_size / 1048576:.1f} MB")
# installers are only reused when the app code did not change, so their version is still the current one
(out / "beyond-downloads.json").write_text(json.dumps({"version": live.get("version", version) if reused else version,
                                                       "files": files}, indent=1))
