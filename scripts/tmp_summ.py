"""Throwaway: print only problem rows of an audit JSON."""

import json
import sys
from pathlib import Path

rows = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
mode = sys.argv[2] if len(sys.argv) > 2 else "all"
for r in rows:
    probs = []
    if r.get("status") not in (200, None) and not (r["kind"] == "add" and r.get("status") == 403):
        probs.append(f"HTTP{r.get('status')}")
    if r.get("exception"):
        probs.append("EXC " + r["exception"][:60])
    if r.get("overflow"):
        probs.append(f"OVERFLOW {r['overflow']['sw']}>{r['overflow']['iw']} {r['overflow']['bad'][:2]}")
    if mode in ("all", "contrast") and r.get("contrast"):
        probs.append("CT " + "; ".join(f"{c['ratio']}/{c['need']} '{c['text']}' {c['el']}" for c in r["contrast"][:3]))
    if r.get("icons"):
        probs.append(f"ICONS {r['icons']} {r['iconSamples']}")
    if r.get("lucideUnrendered"):
        probs.append(f"LUCIDE {r['lucideUnrendered']}")
    errs = [e for e in r.get("errors", []) if not e.startswith("Failed to load resource")]
    if errs:
        probs.append("ERR " + " | ".join(errs)[:200])
    if r.get("failed") and not (r["kind"] == "add" and r.get("status") == 403):
        probs.append("FAIL " + " | ".join(r["failed"])[:160])
    if probs:
        print(f"{r['label'][:40]} {r['kind']} {r['w']}{r['theme'][0]}: " + " ## ".join(probs))
