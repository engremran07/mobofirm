"""Throwaway screenshot helper: python tmp_shot.py name:path[,name:path] [combos]."""

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8011"
LOCK = Path(r"D:\Ninfinity\.browser_lock")
DATA = json.loads((ROOT / "tmp_audit_urls.json").read_text(encoding="utf-8"))
SHOTS = ROOT / "tmp_shots"
SHOTS.mkdir(exist_ok=True)

items = [i.split("=", 1) for i in sys.argv[1].split(",")]
combos = [(int(c.split("-")[0]), c.split("-")[1]) for c in (sys.argv[2] if len(sys.argv) > 2 else "1440-light,1440-dark,360-light,360-dark").split(",")]
full = len(sys.argv) > 3 and sys.argv[3] == "full"

while True:
    try:
        LOCK.mkdir()
        break
    except FileExistsError:
        time.sleep(10)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, theme in combos:
            ctx = browser.new_context(viewport={"width": width, "height": 900})
            ctx.add_cookies([{"name": DATA["cookie_name"], "value": DATA["cookie"], "url": BASE}])
            ctx.add_init_script(f"localStorage.setItem('adminTheme', JSON.stringify('{theme}'));")
            page = ctx.new_page()
            for name, path in items:
                page.goto(BASE + path, wait_until="load", timeout=45000)
                page.wait_for_timeout(600)
                page.screenshot(path=str(SHOTS / f"{name}-{width}-{theme}.png"), full_page=full)
            ctx.close()
        browser.close()
finally:
    try:
        LOCK.rmdir()
    except OSError:
        pass
print("ok")
