"""No browser-native dialogs anywhere: every dialog is app-owned (docs/05-frontend/dialogs.md).

The documented exception is one ``beforeunload`` listener per shell, because browsers
do not allow custom UI when a tab closes or refreshes: the public site's is in
``frontend/src/modules/dialogs.js`` and the staff console's in
``frontend/src/admin/js/components/unsaved.js``. Each requests the generic prompt only
while an unsaved-guard form has unsaved edits.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIPPED_PARTS = {"node_modules", "build", "__pycache__", "staticfiles", ".venv"}
BEFOREUNLOAD_HOMES = [
    Path("frontend/src/admin/js/components/unsaved.js"),
    Path("frontend/src/modules/dialogs.js"),
]
# This module replaces htmx's native confirm with the app dialog, so it must name
# the attribute it intercepts.
HX_CONFIRM_ROUTER = Path("frontend/src/admin/js/htmx-setup.js")

NATIVE_CALL = re.compile(r"(?<![\w.$])(?:window\.)?(?:alert|confirm|prompt)\s*\(")
WINDOW_CALL = re.compile(r"\bwindow\.(?:alert|confirm|prompt)\s*\(")
HX_CONFIRM = re.compile(r"hx-confirm")
DATA_CONFIRM = re.compile(r"data-confirm(?![-\w])")
INLINE_HANDLER = re.compile(
    r"""\bon(?:click|submit|change|keydown|keyup|input)\s*=\s*(?:"[^"]*"|'[^']*')""",
    re.IGNORECASE,
)
INLINE_CONFIRM = re.compile(r"\b(?:alert|confirm|prompt)\b")
SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
BEFOREUNLOAD_LISTENER = re.compile(r"""addEventListener\(\s*["']beforeunload["']""")
ONBEFOREUNLOAD = re.compile(r"onbeforeunload", re.IGNORECASE)


def source_files(*suffixes: str) -> Iterator[Path]:
    for base in ("frontend/src", "templates", "apps"):
        for path in (ROOT / base).rglob("*"):
            if (
                path.is_file()
                and path.suffix in suffixes
                and not SKIPPED_PARTS.intersection(path.parts)
            ):
                yield path


def violations_in(text: str, *, markup: bool, router: bool = False) -> list[str]:
    """Every forbidden native-dialog pattern in ``text`` (``router``: it handles hx-confirm)."""
    found: list[str] = []
    if markup:
        scripts = " ".join(SCRIPT_BLOCK.findall(text))
        if NATIVE_CALL.search(scripts):
            found.append("native alert/confirm/prompt call in <script>")
        if WINDOW_CALL.search(text):
            found.append("window.alert/confirm/prompt call")
    elif NATIVE_CALL.search(text):
        found.append("native alert/confirm/prompt call")
    if HX_CONFIRM.search(text) and not router:
        found.append("hx-confirm (uses the browser's native confirm)")
    if DATA_CONFIRM.search(text):
        found.append(
            "data-confirm without the app dialog handler (use data-confirm-dialog)"
        )
    for handler in INLINE_HANDLER.findall(text):
        if INLINE_CONFIRM.search(handler):
            found.append(f"inline handler with a native dialog: {handler[:60]}")
    if ONBEFOREUNLOAD.search(text):
        found.append("onbeforeunload handler")
    return found


def scan() -> list[str]:
    offenders: list[str] = []
    for path in source_files(".js", ".mjs"):
        is_router = path.relative_to(ROOT) == HX_CONFIRM_ROUTER
        text = path.read_text(encoding="utf-8")
        for problem in violations_in(text, markup=False, router=is_router):
            offenders.append(f"{path.relative_to(ROOT)}: {problem}")
    for path in source_files(".html", ".htm"):
        for problem in violations_in(path.read_text(encoding="utf-8"), markup=True):
            offenders.append(f"{path.relative_to(ROOT)}: {problem}")
    for path in source_files(".py"):
        text = path.read_text(encoding="utf-8")
        for problem in violations_in(text, markup=True):
            offenders.append(f"{path.relative_to(ROOT)}: {problem}")
    return offenders


def test_no_native_dialogs_in_frontend_templates_or_apps():
    offenders = scan()
    assert not offenders, "Native browser dialogs found:\n" + "\n".join(offenders)


def test_one_documented_beforeunload_fallback_per_shell():
    listeners = sorted(
        path.relative_to(ROOT)
        for path in source_files(".js", ".mjs", ".html", ".htm", ".py")
        if BEFOREUNLOAD_LISTENER.search(path.read_text(encoding="utf-8"))
    )
    assert listeners == sorted(BEFOREUNLOAD_HOMES)


def test_the_scanner_catches_each_forbidden_pattern():
    assert violations_in("if (confirm('Sure?')) go();", markup=False)
    assert violations_in("window.alert('x')", markup=False)
    assert violations_in("const a = prompt ('x')", markup=False)
    assert violations_in("<script>confirm('x')</script>", markup=True)
    assert violations_in('<button hx-delete="/x/" hx-confirm="Sure?">', markup=True)
    assert violations_in('<button data-confirm="Sure?">', markup=True)
    assert violations_in("<form onsubmit=\"return confirm('Sure?')\">", markup=True)
    assert violations_in("window.onbeforeunload = null", markup=False)


def test_the_scanner_allows_the_app_dialog_api():
    assert not violations_in(
        "const ok = await Dialogs.confirm({ title: 'Sure?' });", markup=False
    )
    assert not violations_in(
        '<button data-confirm-dialog="Sure?" data-confirm-title="x">', markup=True
    )
    assert not violations_in(
        "<p>Please confirm (or cancel) the request</p>", markup=True
    )
    assert not violations_in(
        '<button data-dialog-open="remove-dialog" type="button">', markup=True
    )
