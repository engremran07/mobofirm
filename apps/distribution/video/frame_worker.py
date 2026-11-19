"""Frame worker: renders a slice of the frames of one plan in its own Chromium.

Run as ``python -m apps.distribution.video.frame_worker <plan.json> <frames dir> <first>
<step> <total>``. It imports nothing from Django, so several workers can run side by side
(one browser each) and the renderer stays within its time limit. Frame ``n`` is the page at
``n * 1000 / fps`` milliseconds; the file name is the zero-padded frame number.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from apps.distribution.video.plan import page_html

JPEG_QUALITY = 94
OP_TIMEOUT_MS = 20_000


def main(argv: list[str]) -> int:
    plan_path, folder, first, step, total = argv
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    out = Path(folder)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=["--disable-gpu", "--hide-scrollbars"]
        )
        try:
            context = browser.new_context(
                viewport={"width": plan["w"], "height": plan["h"]},
                device_scale_factor=1,
                offline=True,
            )
            context.route("**/*", lambda route: route.abort())
            page = context.new_page()
            page.set_default_timeout(OP_TIMEOUT_MS)
            page.set_content(page_html(plan), wait_until="load")
            page.wait_for_function("window.__ready === true")
            for index in range(int(first), int(total), int(step)):
                page.evaluate("t => window.__seek(t)", index * 1000 / plan["fps"])
                data = page.screenshot(
                    type="jpeg", quality=JPEG_QUALITY, animations="disabled"
                )
                (out / f"{index:05d}.jpg").write_bytes(data)
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
