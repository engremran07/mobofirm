"""Throwaway Playwright admin audit (deleted after use). Batches hold the lock."""

import json
import os
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
COMBOS = [
    (int(c.split("-")[0]), c.split("-")[1])
    for c in os.environ.get("AUDIT_COMBOS", "1440-light,360-dark").split(",")
]
BATCH = int(os.environ.get("AUDIT_BATCH", "30"))

JS = r"""
() => {
  const out = {overflow: null, contrast: [], icons: 0, iconSamples: [], clipped: []};
  const root = document.documentElement;
  if (root.scrollWidth > window.innerWidth + 1) {
    const bad = [...document.querySelectorAll('body *')]
      .filter(e => e.getBoundingClientRect().right > window.innerWidth + 1 && getComputedStyle(e).position !== 'fixed')
      .slice(0, 3).map(e => e.tagName + '.' + String(e.className).slice(0, 70) + ' r' + Math.round(e.getBoundingClientRect().right));
    out.overflow = {sw: root.scrollWidth, iw: window.innerWidth, bad};
  }
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', {willReadFrequently: true});
  const rgba = (c) => { cx.clearRect(0,0,1,1); cx.fillStyle = '#000'; cx.fillStyle = c; cx.fillRect(0,0,1,1); const d = cx.getImageData(0,0,1,1).data; return [d[0], d[1], d[2], d[3] / 255]; };
  const lum = ([r,g,b]) => { const f = v => { v/=255; return v <= .03928 ? v/12.92 : Math.pow((v+.055)/1.055, 2.4); }; return .2126*f(r)+.7152*f(g)+.0722*f(b); };
  const bgOf = (el) => {
    let layers = [];
    for (let n = el; n; n = n.parentElement) {
      const cs = getComputedStyle(n);
      const c = rgba(cs.backgroundColor);
      if (c[3] > 0) layers.push(c);
      if (c[3] >= 1) break;
    }
    let base = [255,255,255];
    if (!layers.length || layers[layers.length-1][3] < 1) base = root.classList.contains('dark') ? [21,28,36] : [245,242,235];
    for (let i = layers.length - 1; i >= 0; i--) { const [r,g,b,a] = layers[i]; base = [r*a + base[0]*(1-a), g*a + base[1]*(1-a), b*a + base[2]*(1-a)]; }
    return base;
  };
  const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n; let count = 0;
  while ((n = walker.nextNode()) && count < 1500) {
    const t = n.textContent.trim();
    if (!t) continue;
    const el = n.parentElement;
    if (!el || ['SCRIPT','STYLE','NOSCRIPT'].includes(el.tagName)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0) continue;
    if (el.closest('[x-cloak], template, [hidden], dialog:not([open])')) continue;
    count++;
    const fg = rgba(cs.color); const bg = bgOf(el);
    const a = fg[3];
    const fgm = [fg[0]*a + bg[0]*(1-a), fg[1]*a + bg[1]*(1-a), fg[2]*a + bg[2]*(1-a)];
    const L1 = lum(fgm), L2 = lum(bg);
    const ratio = (Math.max(L1,L2)+.05)/(Math.min(L1,L2)+.05);
    const size = parseFloat(cs.fontSize);
    const need = (size >= 24 || (size >= 18.6 && parseInt(cs.fontWeight) >= 700)) ? 3 : 4.5;
    if (ratio < need - 0.01 && el.closest('.material-symbols-outlined') === null) {
      const key = el.tagName + el.className + t.slice(0, 20);
      if (seen.has(key)) continue; seen.add(key);
      if (out.contrast.length < 8) out.contrast.push({ratio: Math.round(ratio*100)/100, need, text: t.slice(0, 40), el: el.tagName + '.' + String(el.className).slice(0, 60)});
    }
  }
  for (const el of document.querySelectorAll('.material-symbols-outlined')) {
    const r = el.getBoundingClientRect();
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (r.width > fs * 2.2 && r.width > 0 && getComputedStyle(el).display !== 'none') {
      out.icons++; if (out.iconSamples.length < 3) out.iconSamples.push(el.textContent.trim() + ' w' + Math.round(r.width));
    }
  }
  const lucide = document.querySelectorAll('[data-lucide]').length;
  out.lucideUnrendered = lucide;
  return out;
}
"""


def take_lock():
    while True:
        try:
            LOCK.mkdir()
            return
        except FileExistsError:
            time.sleep(10)


def run(rows, shot_labels, out_name):
    results = []
    for start in range(0, len(rows), BATCH):
        batch = rows[start:start + BATCH]
        take_lock()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                for width, theme in COMBOS:
                    ctx = browser.new_context(viewport={"width": width, "height": 900})
                    ctx.add_cookies([{"name": DATA["cookie_name"], "value": DATA["cookie"], "url": BASE}])
                    ctx.add_init_script(f"localStorage.setItem('adminTheme', JSON.stringify('{theme}'));")
                    page = ctx.new_page()
                    errors, failed = [], []
                    page.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" else None)
                    page.on("pageerror", lambda e: errors.append("pageerror " + str(e)[:200]))
                    page.on("response", lambda r: failed.append(f"{r.status} {r.url[-80:]}") if r.status >= 400 else None)
                    for row in batch:
                        errors.clear(); failed.clear()
                        rec = {"label": row["label"], "kind": row["kind"], "path": row["path"], "w": width, "theme": theme}
                        try:
                            resp = page.goto(BASE + row["path"], wait_until="load", timeout=45000)
                            rec["status"] = resp.status if resp else None
                            page.wait_for_timeout(500)
                            rec["final"] = page.url.replace(BASE, "")
                            data = page.evaluate(JS)
                            rec.update(data)
                            key = f'{row["label"]}.{row["kind"]}'
                            if key in shot_labels or (key.startswith("staff") and "staff*" in shot_labels):
                                page.screenshot(path=str(SHOTS / f"{key}-{width}-{theme}.png"), full_page=False)
                        except Exception as exc:
                            rec["exception"] = str(exc)[:200]
                        rec["errors"] = list(errors)[:4]
                        rec["failed"] = list(failed)[:4]
                        results.append(rec)
                    ctx.close()
                browser.close()
        finally:
            try:
                LOCK.rmdir()
            except OSError:
                pass
        (ROOT / out_name).write_text(json.dumps(results, indent=1), encoding="utf-8")
        print(f"done {start + len(batch)}/{len(rows)}", flush=True)
    return results


if __name__ == "__main__":
    out_name = sys.argv[1]
    shots = set(sys.argv[2].split(",")) if len(sys.argv) > 2 and sys.argv[2] else set()
    only = sys.argv[3] if len(sys.argv) > 3 else ""
    rows = [r for r in DATA["urls"] if not only or r["path"].startswith(only) or only in r["label"]]
    rows = rows[int(os.environ.get("AUDIT_SKIP", "0")):]
    run(rows, shots, out_name)
