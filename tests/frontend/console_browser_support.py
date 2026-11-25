"""Playwright support for the staff-console crawl and interaction tests.

Playwright's sync API runs an asyncio loop in the test thread, and Django refuses ORM
calls while a loop runs. Everything that touches the database (seeding, the signed-in
session, the URL world) therefore happens *before* ``chromium()`` is entered, and any
database assertion happens after it exits. The live server runs in its own thread.

``console_server`` deliberately does not use pytest-django's ``live_server``: that
fixture flushes the database after every test, which would throw the seeded world away
between pages. Callers own one module-scoped server and flush it once on teardown.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test.utils import modify_settings
from django.utils import timezone
from playwright.sync_api import APIResponse, BrowserContext, Dialog, Page
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Request as PlaywrightRequest
from playwright.sync_api import Response as PlaywrightResponse
from pytest_django.live_server_helper import LiveServer

from tests.frontend.browser_support import login_cookie

THEME_KEY = "mobofirm-theme"
# The JSON crawl report is an artifact, never committed: set CONSOLE_CRAWL_RESULTS to
# choose where it goes (default: the system temp directory).
RESULTS_PATH = Path(
    os.environ.get(
        "CONSOLE_CRAWL_RESULTS", Path(tempfile.gettempdir()) / "crawl-results.json"
    )
)
# (width, theme) states every crawled page is measured in, loading the page only once.
STATES: tuple[tuple[int, str], ...] = (
    (1280, "light"),
    (768, "light"),
    (360, "light"),
    (1280, "dark"),
    (768, "dark"),
    (360, "dark"),
)
MIN_LIGHT_LUMINANCE = 0.5
MAX_DARK_LUMINANCE = 0.15
MIN_TEXT_CONTRAST = 4.5
MIN_LARGE_TEXT_CONTRAST = 3.0
LOCAL_SCHEMES = {"data", "blob", "about"}

# Hooks the console behaviour layer publishes (frontend/src/admin/js/**). Tests address
# the console only through these and through accessible roles, never styling classes.
HOOKS = {
    "sidebar": "aside[data-console-sidebar]",
    "sidebar_toggle": "[data-console-sidebar-toggle]",
    "theme_set": "[data-console-theme-set]",
    "dialog_host": "#console-dialog",
    "dialog_close": "[data-console-dialog-close]",
    "confirm_dialog": "[data-console-confirm-dialog]",
    "confirm_ok": "[data-console-confirm-ok]",
    "changelist": "[data-console-changelist]",
    "row_select": "input[data-console-row-select]",
    "select_all": "input[data-console-select-all]",
    "bulkbar": "[data-console-bulkbar]",
    "formset": "[data-console-formset]",
    "formset_add": "[data-console-formset-add]",
    "formset_remove": "[data-console-formset-remove]",
    "unsaved_form": "form[data-console-unsaved-guard]",
    "main": "#console-main",
    "toast": "[data-console-toast], .toast",
}

AUDIT_JS = r"""
({light, dark, minText, minLarge}) => {
  const root = document.documentElement;
  const isVisible = (el) =>
    el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  const text = (el) => (el.textContent || "").replace(/\s+/g, " ").trim();
  const describe = (el) => {
    const bits = [el.tagName.toLowerCase()];
    if (el.id) bits.push("#" + el.id);
    const name = el.getAttribute("name");
    if (name) bits.push(`[name=${name}]`);
    const cls = String(el.className || "").trim().split(/\s+/).slice(0, 2).join(".");
    if (cls) bits.push("." + cls);
    return bits.join("");
  };
  const accessibleName = (el) => {
    const aria = el.getAttribute("aria-label");
    if (aria && aria.trim()) return aria.trim();
    const by = el.getAttribute("aria-labelledby");
    if (by) {
      const joined = by.split(/\s+/).map((id) => document.getElementById(id))
        .filter(Boolean).map(text).join(" ").trim();
      if (joined) return joined;
    }
    if (el.labels && el.labels.length) {
      const joined = [...el.labels].map(text).join(" ").trim();
      if (joined) return joined;
    }
    if (el.matches("input[type=submit],input[type=button],input[type=reset]") && el.value) {
      return el.value;
    }
    if (el.matches("input[type=image]") && el.alt) return el.alt;
    if (!el.matches("input,select,textarea")) {
      const inner = text(el);
      if (inner) return inner;
      const img = el.querySelector("img[alt]:not([alt=''])");
      if (img) return img.alt;
    }
    const title = el.getAttribute("title");
    return title && title.trim() ? title.trim() : "";
  };

  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const context = canvas.getContext("2d", {willReadFrequently: true});
  const rgba = (css) => {
    context.clearRect(0, 0, 1, 1);
    context.fillStyle = "#000";
    context.fillStyle = css;
    context.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = context.getImageData(0, 0, 1, 1).data;
    return {r, g, b, a: a / 255};
  };
  const over = (top, under) => ({
    r: top.r * top.a + under.r * (1 - top.a),
    g: top.g * top.a + under.g * (1 - top.a),
    b: top.b * top.a + under.b * (1 - top.a),
    a: 1,
  });
  const backdrop = (el) => {
    const chain = [];
    for (let node = el; node; node = node.parentElement) chain.unshift(node);
    return chain.reduce(
      (below, node) => over(rgba(getComputedStyle(node).backgroundColor), below),
      {r: 255, g: 255, b: 255, a: 1},
    );
  };
  const channel = (value) => {
    const v = value / 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  };
  const luminance = (c) => 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
  const contrast = (el) => {
    const background = backdrop(el);
    const foreground = over(rgba(getComputedStyle(el).color), background);
    const [hi, lo] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
    return (hi + 0.05) / (lo + 0.05);
  };

  const problems = [];
  const metrics = {};

  const theme = root.dataset.bsTheme || "";
  const surface = luminance(backdrop(document.body));
  metrics.theme = theme;
  metrics.surface = Math.round(surface * 1000) / 1000;
  if (light && !(surface >= light)) {
    problems.push(`light theme surface too dark (luminance ${metrics.surface}, theme "${theme}")`);
  }
  if (dark !== null && !(surface <= dark)) {
    problems.push(`dark theme surface too light (luminance ${metrics.surface}, theme "${theme}")`);
  }

  const dims = {viewport: root.clientWidth, document: root.scrollWidth};
  metrics.overflow = dims;
  if (dims.document > dims.viewport + 1) {
    const scrolls = (node) => {
      for (let p = node.parentElement; p && p !== document.body; p = p.parentElement) {
        if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(p).overflowX)) return true;
      }
      return false;
    };
    const culprits = [...document.body.querySelectorAll("*")]
      .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1 && isVisible(el))
      .filter((el) => !scrolls(el)).slice(0, 3).map(describe);
    problems.push(`horizontal overflow ${dims.document}>${dims.viewport}: ${culprits.join(", ")}`);
  }

  const h1 = [...document.querySelectorAll("h1")].filter(isVisible);
  metrics.h1 = h1.length;
  if (h1.length === 0) problems.push("no visible <h1>");
  if (h1.length > 1) metrics.warnings = [`${h1.length} visible <h1>`];
  if (!document.title.trim()) problems.push("empty <title>");
  if (!root.getAttribute("lang")) problems.push("<html> has no lang");
  if (!document.querySelector("main, [role=main]")) problems.push("no <main> landmark");
  if (!document.querySelector("nav, [role=navigation]")) problems.push("no navigation landmark");
  if (!document.querySelector("header, [role=banner]")) problems.push("no banner landmark");
  if (document.querySelectorAll("main, [role=main]").length > 1) problems.push("more than one <main>");

  const unlabeled = [...document.querySelectorAll(
    "input:not([type=hidden]), select, textarea")]
    .filter(isVisible).filter((el) => !accessibleName(el)).slice(0, 5).map(describe);
  if (unlabeled.length) problems.push(`inputs without an accessible name: ${unlabeled.join(", ")}`);
  const unnamed = [...document.querySelectorAll(
    "button, [role=button], input[type=submit], input[type=button], input[type=reset]")]
    .filter(isVisible).filter((el) => !accessibleName(el)).slice(0, 5).map(describe);
  if (unnamed.length) problems.push(`buttons without an accessible name: ${unnamed.join(", ")}`);
  const unnamedLinks = [...document.querySelectorAll("a[href]")]
    .filter(isVisible).filter((el) => !accessibleName(el)).slice(0, 5).map(describe);
  if (unnamedLinks.length) problems.push(`links without an accessible name: ${unnamedLinks.join(", ")}`);
  const noAlt = [...document.querySelectorAll("img:not([alt])")].slice(0, 3).map(describe);
  if (noAlt.length) problems.push(`images without alt: ${noAlt.join(", ")}`);

  const seen = new Map();
  for (const el of document.querySelectorAll("[id]")) seen.set(el.id, (seen.get(el.id) || 0) + 1);
  const duplicates = [...seen].filter(([, n]) => n > 1).map(([id]) => id).slice(0, 5);
  if (duplicates.length) problems.push(`duplicate ids: ${duplicates.join(", ")}`);

  const placeholders = document.querySelectorAll("[data-lucide]:not(svg), [data-icon]:not(svg)");
  const icons = document.querySelectorAll("svg[data-icon-name], svg.lucide");
  metrics.icons = icons.length;
  if (placeholders.length) {
    problems.push(`${placeholders.length} Lucide placeholder(s) never rendered: ${
      [...placeholders].slice(0, 3).map(describe).join(", ")}`);
  }
  if (icons.length === 0) problems.push("no Lucide icon rendered on the page");

  const samples = [
    ["h1", h1[0], minLarge],
    ["main text", document.querySelector("main, [role=main]"), minText],
    ["nav link", [...document.querySelectorAll("nav a[href], aside a[href]")].find(isVisible), minText],
    ["primary button", [...document.querySelectorAll(".btn-primary")].find(isVisible), minText],
  ];
  metrics.contrast = {};
  for (const [label, el, threshold] of samples) {
    if (!el) continue;
    const ratio = Math.round(contrast(el) * 100) / 100;
    metrics.contrast[label] = ratio;
    if (ratio < threshold) problems.push(`${label} contrast ${ratio}:1 is below ${threshold}:1`);
  }
  return {problems, metrics};
}
"""

THEME_JS = """
(mode) => {
  window.localStorage.setItem('__KEY__', mode);
  window.dispatchEvent(new StorageEvent('storage', {key: '__KEY__', newValue: mode}));
}
""".replace("__KEY__", THEME_KEY)


@dataclass
class ConsoleServer:
    """A running live server plus the signed-in superuser's session cookie."""

    url: str
    user: Any
    cookies: dict[str, str]

    def context(self, browser, *, width: int = 1280, height: int = 900, **options):
        """A signed-in context that starts light, offline-checkable and motion-free."""
        context = browser.new_context(
            viewport={"width": width, "height": height},
            color_scheme="light",
            reduced_motion="reduce",
            **options,
        )
        context.add_init_script(
            f"window.localStorage.setItem('{THEME_KEY}', 'light');"
            "window.__cspViolations = [];"
            "document.addEventListener('securitypolicyviolation', (event) =>"
            " window.__cspViolations.push(event.violatedDirective + ' ' + event.blockedURI));"
        )
        context.add_cookies(
            [
                {"name": name, "value": value, "url": self.url}
                for name, value in self.cookies.items()
            ]
        )
        return context


def serialize_requests(live_server: LiveServer) -> None:
    """Handle one request at a time on a pytest-django ``live_server``.

    The server threads share the test's in-memory SQLite connection, and the dashboard
    fires four widget requests at once: concurrent cursors on that one connection raise
    ``InterfaceError`` (a test-database artefact, not an application bug).
    """
    httpd = live_server.thread.httpd
    app = httpd.get_app()
    assert app is not None, "the live server has no WSGI application yet"
    lock = threading.Lock()

    def serialized(environ, start_response):
        with lock:
            return list(app(environ, start_response))

    httpd.set_app(serialized)


@contextmanager
def console_server(django_db_blocker, *, username: str) -> Iterator[ConsoleServer]:
    """Module-scoped live server on the test database; flushed once on exit."""
    with django_db_blocker.unblock():
        user = get_user_model().objects.create_superuser(
            username=username,
            email=f"{username}@example.test",
            email_verified_at=timezone.now(),
        )
        hosts = modify_settings(ALLOWED_HOSTS={"append": "localhost"})
        hosts.enable()
        server = LiveServer("localhost")
        serialize_requests(server)
        try:
            yield ConsoleServer(server.url, user, login_cookie(user))
        finally:
            server.stop()
            hosts.disable()
            call_command("flush", verbosity=0, interactive=False)


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


@dataclass
class Probe:
    """Everything one page emits besides its DOM: console, errors, network, CSP."""

    origin: str
    expected: tuple[int, ...] = (200,)
    active: bool = False
    console: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    http: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    external: list[str] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)

    def attach(self, page: Page) -> None:
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_failed)
        page.on("request", self._on_request)
        page.on("dialog", self._on_dialog)

    def begin(self, expected: tuple[int, ...] = (200,)) -> None:
        self.expected = expected
        self.console.clear()
        self.page_errors.clear()
        self.http.clear()
        self.failed.clear()
        self.external.clear()
        self.dialogs.clear()
        self.active = True

    def end(self) -> list[str]:
        self.active = False
        problems = [f"console: {text}" for text in self.console]
        problems += [f"page error: {text}" for text in self.page_errors]
        problems += [f"HTTP {text}" for text in self.http]
        problems += [f"request failed: {text}" for text in self.failed]
        problems += [f"external-origin request: {url}" for url in self.external]
        problems += [f"native browser dialog: {kind}" for kind in self.dialogs]
        return problems

    def _on_console(self, message) -> None:
        if not self.active or message.type not in {"error", "warning"}:
            return
        if self.expected != (200,) and message.text.startswith(
            "Failed to load resource:"
        ):
            return
        self.console.append(f"{message.type}: {message.text}")

    def _on_page_error(self, error) -> None:
        if self.active:
            self.page_errors.append(str(error))

    def _on_response(self, response: PlaywrightResponse) -> None:
        if not self.active or response.status < 400:
            return
        is_document = response.request.resource_type == "document"
        if is_document and response.status in self.expected:
            return
        self.http.append(f"{response.status} {response.request.method} {response.url}")

    def _on_failed(self, request: PlaywrightRequest) -> None:
        failure = request.failure or ""
        if self.active and "ERR_ABORTED" not in failure:
            self.failed.append(f"{request.method} {request.url} ({failure})")

    def _on_request(self, request: PlaywrightRequest) -> None:
        if not self.active:
            return
        scheme = urlsplit(request.url).scheme
        if scheme in LOCAL_SCHEMES:
            return
        if origin_of(request.url) != self.origin:
            self.external.append(request.url)

    def _on_dialog(self, dialog: Dialog) -> None:
        if dialog.type == "beforeunload":
            dialog.accept()
            return
        if self.active:
            self.dialogs.append(dialog.type)
        dialog.dismiss()


@dataclass
class PageResult:
    """The outcome of one crawled URL, serialised to ``crawl-results.json``."""

    group: str
    label: str
    path: str
    mode: str
    expected: list[int]
    status: int | None = None
    problems: list[str] = field(default_factory=list)
    states: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.problems

    def as_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["passed"] = self.passed
        return data


def ready(page: Page, problems: list[str]) -> None:
    """Wait until the console runtime booted and its icons are drawn."""
    try:
        page.wait_for_function(
            "() => Boolean(window.htmx && window.Alpine)", timeout=8_000
        )
    except PlaywrightError:
        problems.append("console runtime (htmx + Alpine) did not start")
        return
    try:
        page.wait_for_function(
            "() => !document.querySelector('[data-lucide]:not(svg), [data-icon]:not(svg)')",
            timeout=4_000,
        )
    except PlaywrightError:
        pass  # the audit reports any placeholder that is still unrendered
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightError:
        problems.append("the page never went network-idle (a request keeps running)")


def set_theme(page: Page, mode: str, problems: list[str]) -> None:
    page.evaluate(THEME_JS, mode)
    try:
        page.wait_for_function(
            "(mode) => document.documentElement.dataset.bsTheme === mode",
            arg=mode,
            timeout=3_000,
        )
        # The attribute flips first; the painted colours follow on the next style recalculation
        # (the theme switch can run inside a view transition), so wait until the body shows the new page colour.
        page.wait_for_function(
            """() => {
                const probe = document.createElement("i");
                probe.style.backgroundColor = "var(--mf-page)";
                document.body.append(probe);
                const expected = getComputedStyle(probe).backgroundColor;
                probe.remove();
                return getComputedStyle(document.body).backgroundColor === expected;
            }""",
            timeout=3_000,
        )
    except PlaywrightError:
        problems.append(f"the console did not switch to the {mode} theme")


def audit(page: Page, mode: str) -> dict[str, Any]:
    return page.evaluate(
        AUDIT_JS,
        {
            "light": MIN_LIGHT_LUMINANCE if mode == "light" else 0,
            "dark": MAX_DARK_LUMINANCE if mode == "dark" else None,
            "minText": MIN_TEXT_CONTRAST,
            "minLarge": MIN_LARGE_TEXT_CONTRAST,
        },
    )


def crawl_page(
    page: Page,
    probe: Probe,
    base: str,
    group: str,
    label: str,
    path: str,
    expected: tuple[int, ...],
) -> PageResult:
    """Load ``path`` once, then audit it at every width in light and dark."""
    result = PageResult(group, label, path, "page", list(expected))
    probe.begin(expected)
    problems: list[str] = []
    try:
        page.set_viewport_size({"width": STATES[0][0], "height": 900})
        response = page.goto(f"{base}{path}", wait_until="load", timeout=30_000)
    except PlaywrightError as error:
        result.problems.append(f"navigation failed: {error}")
        probe.end()
        return result
    result.status = response.status if response else None
    if result.status not in expected:
        problems.append(f"HTTP {result.status}, expected {list(expected)}")
    ready(page, problems)
    current_theme = "light"
    for width, mode in STATES:
        state = f"{width}px {mode}"
        if mode != current_theme:
            set_theme(page, mode, problems)
            current_theme = mode
        page.set_viewport_size({"width": width, "height": 900})
        report = audit(page, mode)
        result.states[state] = report["metrics"]
        problems.extend(f"[{state}] {problem}" for problem in report["problems"])
    violations = page.evaluate("() => window.__cspViolations || []")
    problems.extend(f"CSP violation: {violation}" for violation in violations)
    problems.extend(probe.end())
    result.problems = _unique(problems)
    return result


def check_status(
    context: BrowserContext,
    base: str,
    group: str,
    label: str,
    path: str,
    expected: tuple[int, ...],
) -> PageResult:
    """Routes that are not a page to look at (POST-only, redirects, JSON)."""
    result = PageResult(group, label, path, "status", list(expected))
    response = context.request.get(
        f"{base}{path}", max_redirects=0, fail_on_status_code=False
    )
    result.status = response.status
    if response.status not in expected:
        result.problems.append(f"HTTP {response.status}, expected {list(expected)}")
    if "autocomplete" in label and response.status == 200:
        try:
            payload = response.json()
        except (ValueError, PlaywrightError):
            payload = None
        if not isinstance(payload, dict) or "results" not in payload:
            result.problems.append("autocomplete did not answer with JSON results")
    return result


def check_fragment(
    context: BrowserContext,
    base: str,
    group: str,
    label: str,
    path: str,
    needs_dialog_markup: bool,
) -> PageResult:
    """An HX-Request GET answers with a partial, never the whole page shell."""
    result = PageResult(group, label, path, "fragment", [200])
    response: APIResponse = context.request.get(
        f"{base}{path}",
        headers={"HX-Request": "true", "HX-Request-Type": "partial"},
        max_redirects=0,
        fail_on_status_code=False,
    )
    result.status = response.status
    if response.status != 200:
        result.problems.append(f"HTTP {response.status} for an HX-Request GET")
        return result
    body = response.text()
    lowered = body.lower()
    if "<html" in lowered or "<!doctype" in lowered:
        result.problems.append("HX-Request GET returned the full page shell")
    if not body.strip():
        result.problems.append("HX-Request GET returned an empty body")
    if needs_dialog_markup and not any(
        marker in lowered for marker in ("<form", "dialog", "modal")
    ):
        result.problems.append("dialog fragment has no form/dialog/modal markup")
    if "<script" in lowered and not re.search(r"<script[^>]*application/json", lowered):
        result.problems.append("fragment carries an executable <script>")
    if re.search(r"<[a-z][^>]*\son[a-z]+\s*=", body, re.IGNORECASE):
        result.problems.append("fragment carries an inline event handler")
    return result


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def write_results(
    results: list[PageResult], extra: dict[str, Any], *, partial: bool = False
) -> Path:
    """Write the JSON report; a subset run never overwrites the full-crawl report."""
    path = (
        RESULTS_PATH.with_name("crawl-results-partial.json")
        if partial
        else RESULTS_PATH
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **extra,
        "summary": {
            "pages": len(results),
            "passed": sum(1 for result in results if result.passed),
            "failed": sum(1 for result in results if not result.passed),
        },
        "results": [result.as_json() for result in results],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


# -- helpers for the interaction tests ----------------------------------------------
def open_console(
    page: Page,
    _probe: Probe,
    server: ConsoleServer,
    path: str,
    problems: list[str],
) -> None:
    """Navigate to a console page and wait for its runtime; fail loudly on non-200."""
    response = page.goto(f"{server.url}{path}", wait_until="load", timeout=30_000)
    if response is None or response.status != 200:
        problems.append(f"{path}: HTTP {response and response.status}")
    ready(page, problems)


def mark_no_reload(page: Page) -> None:
    """Tag the current document so a later full reload is detectable."""
    page.evaluate("() => { window.__noFullReload = true; }")


def reloaded(page: Page) -> bool:
    return not page.evaluate("() => window.__noFullReload === true")


def visible_first(page: Page, selector: str):
    """The first *visible* match (sidebar/palette hooks exist twice: desktop + mobile)."""
    locator = page.locator(selector)
    for index in range(locator.count()):
        candidate = locator.nth(index)
        if candidate.is_visible():
            return candidate
    return locator.first
