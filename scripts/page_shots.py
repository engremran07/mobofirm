"""Full-page screenshots of local routes, cropped into readable chunks.

Usage (dev server on 127.0.0.1:8000, Playwright installed)::

    python scripts/page_shots.py <width> <light|dark> <path> [<path> ...]

Writes ``%TEMP%/shot_<n>_<width>_<scheme>_part<k>.png`` (900px high chunks) and
prints horizontal overflow, page height and console errors for each path. Hold the
``.browser_lock`` directory while running, like the Playwright tests do.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Literal, cast

from PIL import Image
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
CHUNK = 900
Scheme = Literal["light", "dark"]


def crop_chunks(source: Path) -> int:
    """Split a tall screenshot into ``CHUNK``-pixel parts next to the original."""
    image = Image.open(source)
    width, height = image.size
    count = 0
    for top in range(0, height, CHUNK):
        part = image.crop((0, top, width, min(top + CHUNK, height)))
        part.save(source.with_name(f"{source.stem}_part{count}.png"))
        count += 1
    return count


def shoot(page, path: str, index: int, width: int, scheme: str) -> None:
    """Screenshot one path full-page, crop it, and print the layout metrics."""
    out = Path(os.environ.get("SHOT_DIR", tempfile.gettempdir()))
    page.goto(BASE + path, wait_until="networkidle")
    page.wait_for_timeout(500)
    metrics = page.evaluate(
        "() => [document.documentElement.scrollWidth - window.innerWidth,"
        " document.documentElement.scrollHeight]"
    )
    target = out / f"shot_{index}_{width}_{scheme}.png"
    page.screenshot(path=str(target), full_page=True)
    parts = crop_chunks(target)
    print(f"{path} overflow={metrics[0]} height={metrics[1]} parts={parts} {target}")


def main(argv: list[str]) -> int:
    """Shoot every path and report overflow and console errors."""
    if len(argv) < 4:
        print(__doc__)
        return 2
    width, scheme, paths = int(argv[1]), cast(Scheme, argv[2]), argv[3:]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": width, "height": CHUNK}, color_scheme=scheme
        )
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)[:160]))
        page.on(
            "console",
            lambda msg: errors.append(msg.text[:160]) if msg.type == "error" else None,
        )
        for index, path in enumerate(paths):
            shoot(page, path, index, width, scheme)
        print("console errors:", errors[:8])
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
