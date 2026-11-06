"""Shared Playwright helpers for the ecosystem browser tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone
from playwright.sync_api import (
    Browser,
    BrowserContext,
    ConsoleMessage,
    Dialog,
    Page,
    sync_playwright,
)
from playwright.sync_api import Error as PlaywrightError

WIDTHS = (360, 768, 1365)
PASSWORD = "Pass-12345!"
IGNORED_CONSOLE = ("Failed to load resource:",)


@contextmanager
def chromium() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for frontend checks. Install it with "
                f"`.venv\\Scripts\\python.exe -m playwright install chromium`.\n{error}"
            )
        try:
            yield browser
        finally:
            browser.close()


def watch(page: Page, failures: list[str]) -> None:
    """Collect console errors and warnings (CSP violations included) and page errors."""

    def on_console(message) -> None:
        if message.type in {"error", "warning"} and not message.text.startswith(
            IGNORED_CONSOLE
        ):
            failures.append(f"console {message.type}: {message.text}")

    page.on("console", on_console)
    page.on("pageerror", lambda error: failures.append(f"page error: {error}"))


def collect_console(
    page: Page, errors: list[str], warnings: list[str] | None = None
) -> None:
    """Record raw console error (and optionally warning) texts on ``page``."""

    def on_console(message: ConsoleMessage) -> None:
        if message.type == "error":
            errors.append(message.text)
        elif message.type == "warning" and warnings is not None:
            warnings.append(message.text)

    page.on("console", on_console)


def dismiss_dialogs(page: Page, dialogs: list[str]) -> None:
    """Dismiss browser-native dialogs and record their types."""

    def on_dialog(dialog: Dialog) -> None:
        dialogs.append(dialog.type)
        dialog.dismiss()

    page.on("dialog", on_dialog)


def ready(page: Page) -> None:
    page.wait_for_function("() => Boolean(window.htmx && window.Alpine)", timeout=5_000)


def overflow(page: Page) -> dict[str, int]:
    return page.evaluate(
        """() => ({
            viewport: document.documentElement.clientWidth,
            document: document.documentElement.scrollWidth
        })"""
    )


def make_user(username: str, *, staff: bool = False, verified: bool = True):
    return get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        is_staff=staff,
        email_verified_at=timezone.now() if verified else None,
    )


def login_cookie(user) -> dict[str, str]:
    """Session cookie for a signed-in user (create it before the browser starts:
    Playwright's event loop forbids ORM calls inside its context)."""
    client = Client()
    client.force_login(user)
    session = client.session
    # Logging in registers the test client's "device"; drop the browser prompt so it
    # does not cover the page under test.
    session.pop("device_registration_prompt", None)
    session.save()
    return {
        settings.SESSION_COOKIE_NAME: client.cookies[settings.SESSION_COOKIE_NAME].value
    }


def session_context(
    browser: Browser, live_server, cookies: dict[str, str], *, width: int = 1365
) -> BrowserContext:
    """A browser context that is already signed in with ``cookies``."""
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.add_cookies(
        [
            {"name": name, "value": value, "url": live_server.url}
            for name, value in cookies.items()
        ]
    )
    return context


def visit_all(
    page: Page,
    live_server,
    paths: list[str],
    failures: list[str],
    *,
    widths: tuple[int, ...] = WIDTHS,
    expect_status: int = 200,
) -> None:
    """Open every path at every width; record HTTP, console and overflow problems."""
    for width in widths:
        page.set_viewport_size({"width": width, "height": 900})
        for path in paths:
            label = f"{path} at {width}px"
            response = page.goto(
                f"{live_server.url}{path}", wait_until="load", timeout=15_000
            )
            if response is None or response.status != expect_status:
                failures.append(f"{label}: HTTP {response and response.status}")
                continue
            ready(page)
            dimensions = overflow(page)
            if dimensions["document"] > dimensions["viewport"] + 1:
                failures.append(f"{label}: horizontal overflow {dimensions}")
            if page.locator("h1").count() < 1:
                failures.append(f"{label}: no <h1>")
