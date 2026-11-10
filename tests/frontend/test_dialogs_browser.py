"""Browser checks for the app-owned dialog shell (docs/05-frontend/dialogs.md)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from django.http import HttpRequest, HttpResponse
from django.template import engines
from django.urls import clear_url_caches, path
from playwright.sync_api import Browser, Page

from app import urls as root_urls
from apps.core.dialogs import DialogError, DialogForm, DialogView
from tests.frontend.browser_support import (
    chromium,
    dismiss_dialogs,
    login_cookie,
    make_user,
    overflow,
    ready,
    session_context,
    watch,
)

BROWSER_LOCK = Path(__file__).resolve().parents[2] / ".browser_lock"
LOCK_WAIT_SECONDS = 600
LOCK_RETRY_SECONDS = 10
STATE = {"counter": 0}

PAGE = """{% extends "base.html" %}
{% block title %}Dialog lab{% endblock %}
{% block content %}
<div class="site-container py-4">
  <h1>Dialog lab</h1>
  <p id="counter" hx-get="/test-dialogs/counter/" hx-trigger="dialog:done from:body" hx-swap="innerHTML">count {{ counter }}</p>
  <a id="open-delete" class="btn btn-danger" href="/test-dialogs/delete/" hx-get="/test-dialogs/delete/" hx-target="#dialog-slot" hx-swap="innerHTML">Delete</a>
  <a id="open-bump" class="btn btn-primary" href="/test-dialogs/bump/" hx-get="/test-dialogs/bump/" hx-target="#dialog-slot" hx-swap="innerHTML">Bump</a>
  <button id="open-inline" type="button" class="btn btn-primary" data-dialog-open="inline-confirm">Publish</button>
  {% include "components/confirm_dialog.html" with dialog_id="inline-confirm" dialog_title="Publish it?" dialog_message="Readers will see it." dialog_action="/test-dialogs/echo/" confirm_label="Publish" %}
  <form id="confirm-form" method="post" action="/test-dialogs/echo/">{% csrf_token %}
    <button id="confirm-submit" class="btn btn-outline-danger" type="submit" data-confirm-dialog="Really send it?" data-confirm-title="Send it?" data-confirm-danger>Send</button>
  </form>
  <button id="htmx-confirm" class="btn btn-outline-secondary" hx-get="/test-dialogs/fragment/" hx-target="#htmx-result" data-confirm-dialog="Load the fragment?" data-confirm-title="Load it?">Load</button>
  <div id="htmx-result" aria-live="polite"></div>
  <form id="guarded" data-unsaved-guard method="post" action="/test-dialogs/echo/">{% csrf_token %}
    <label class="form-label" for="draft">Draft</label>
    <input class="form-control" id="draft" name="draft">
    <button id="save-draft" type="submit" class="btn btn-primary mt-2">Save</button>
  </form>
  <a id="leave" href="/test-dialogs/echo/">Leave</a>
  <button id="open-note" type="button" class="btn btn-primary" hx-get="/test-dialogs/note/" hx-target="#dialog-slot" hx-swap="innerHTML">Note</button>
  <button id="open-long" type="button" class="btn btn-outline-secondary" data-dialog-open="long-confirm">Terms</button>
  {% include "components/confirm_dialog.html" with dialog_id="long-confirm" dialog_title=long_title dialog_message=long_text dialog_action="/test-dialogs/echo/" confirm_label="Accept" dialog_size="md" %}
  <button id="open-sm" type="button" data-dialog-open="size-sm">sm</button>
  <button id="open-md" type="button" data-dialog-open="size-md">md</button>
  <button id="open-lg" type="button" data-dialog-open="size-lg">lg</button>
  {% include "components/confirm_dialog.html" with dialog_id="size-sm" dialog_title="Small" dialog_action="/test-dialogs/echo/" confirm_label="OK" %}
  {% include "components/confirm_dialog.html" with dialog_id="size-md" dialog_title="Medium" dialog_action="/test-dialogs/echo/" confirm_label="OK" dialog_size="md" %}
  {% include "components/confirm_dialog.html" with dialog_id="size-lg" dialog_title="Large" dialog_action="/test-dialogs/echo/" confirm_label="OK" dialog_size="lg" %}
  {% for number in filler %}<p>Filler paragraph {{ number }} keeps the page scrollable.</p>{% endfor %}
  <button id="open-plain" type="button" class="btn btn-outline-secondary" data-dialog-open="plain-dialog">Plain</button>
  <dialog class="app-dialog" id="plain-dialog" aria-labelledby="plain-title" data-app-dialog>
    <form method="dialog" class="app-dialog__body">
      <h2 id="plain-title" class="h4">Plain dialog</h2>
      <label class="form-label" for="plain-input">Name</label>
      <input class="form-control" id="plain-input" name="name">
      <button type="button" class="btn btn-outline-secondary" data-dialog-close>Cancel</button>
      <button type="submit" class="btn btn-primary">Go</button>
    </form>
  </dialog>
</div>
{% endblock %}
"""


class ReasonForm(DialogForm):
    reason_required = True


class DeleteDialog(DialogView):
    form_class = ReasonForm
    dialog_title = "Delete the record"
    dialog_message = "This cannot be undone."
    confirm_label = "Delete"
    danger = True
    success_url = "/test-dialogs/"
    success_message = "Deleted."

    def has_permission(self, request: HttpRequest) -> bool:
        return bool(request.user.is_staff)

    def perform(self, form: DialogForm) -> None:
        if form.reason == "boom":
            raise DialogError("A rule rejected this.")


class BumpDialog(DialogView):
    dialog_title = "Bump the counter"
    confirm_label = "Bump"
    refresh_on_success = False

    def perform(self, form: DialogForm) -> None:
        STATE["counter"] += 1


class NoteDialog(DialogView):
    """A slow dialog that stays on the page and confirms with a toast."""

    dialog_title = "Add a note"
    confirm_label = "Save note"
    refresh_on_success = False
    success_message = "Note saved."

    def perform(self, form: DialogForm) -> None:
        time.sleep(0.8)


def page(request: HttpRequest) -> HttpResponse:
    template = engines["django"].from_string(PAGE)
    context = {
        "counter": STATE["counter"],
        "long_title": "Terms " + "X" * 120,
        "long_text": "Every paragraph of these terms is long. " * 120,
        "filler": range(60),
    }
    return HttpResponse(template.render(context, request))


def counter(request: HttpRequest) -> HttpResponse:
    return HttpResponse(f"count {STATE['counter']}")


def echo(request: HttpRequest) -> HttpResponse:
    return HttpResponse("<!doctype html><title>Echo</title><h1>echoed</h1>")


def fragment(request: HttpRequest) -> HttpResponse:
    return HttpResponse("<p>fragment loaded</p>")


@pytest.fixture(autouse=True)
def dialog_lab_urls() -> Iterator[None]:
    STATE["counter"] = 0
    root = root_urls
    original = list(root.urlpatterns)
    root.urlpatterns = [
        path("test-dialogs/", page),
        path("test-dialogs/counter/", counter),
        path("test-dialogs/echo/", echo),
        path("test-dialogs/fragment/", fragment),
        path("test-dialogs/delete/", DeleteDialog.as_view()),
        path("test-dialogs/bump/", BumpDialog.as_view()),
        path("test-dialogs/note/", NoteDialog.as_view()),
        *original,
    ]
    clear_url_caches()
    yield
    root.urlpatterns = original
    clear_url_caches()


@contextmanager
def locked_chromium() -> Iterator[Browser]:
    """One Chromium at a time on this machine: hold the shared lock directory."""
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            BROWSER_LOCK.mkdir()
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                pytest.fail("The browser lock stayed busy for 10 minutes.")
            time.sleep(LOCK_RETRY_SECONDS)
    try:
        with chromium() as browser:
            yield browser
    finally:
        BROWSER_LOCK.rmdir()


def inside_dialog(page_: Page) -> bool:
    return page_.evaluate(
        "() => Boolean(document.activeElement && document.activeElement.closest('dialog[open]'))"
    )


def open_delete(page_: Page) -> None:
    page_.click("#open-delete")
    page_.wait_for_selector("#dialog-slot dialog[open]")


@pytest.mark.django_db(transaction=True)
def test_dialog_lifecycle_focus_and_server_validation(live_server):
    cookies = login_cookie(make_user("dialog_lab_staff", staff=True))
    failures: list[str] = []
    natives: list[str] = []
    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page_ = context.new_page()
        watch(page_, failures)
        dismiss_dialogs(page_, natives)
        page_.goto(f"{live_server.url}/test-dialogs/")
        ready(page_)

        open_delete(page_)
        dialog = page_.locator("#dialog-slot dialog")
        assert dialog.get_attribute("aria-labelledby") == "app-dialog-title"
        assert page_.locator("#app-dialog-title").inner_text() == "Delete the record"
        assert (
            page_.locator("#app-dialog-message").inner_text()
            == "This cannot be undone."
        )
        # The first field takes focus and Tab never leaves the modal.
        assert page_.evaluate("() => document.activeElement.name") == "reason"
        for _ in range(8):
            page_.keyboard.press("Tab")
            assert inside_dialog(page_)
        for _ in range(4):
            page_.keyboard.press("Shift+Tab")
            assert inside_dialog(page_)
        assert (
            page_.evaluate("() => getComputedStyle(document.documentElement).overflow")
            == "hidden"
        )

        # Server-side validation errors are re-rendered inside the open dialog.
        page_.click("#dialog-slot button[type=submit]")
        page_.wait_for_selector("#dialog-slot .invalid-feedback")
        assert page_.locator("#dialog-slot dialog[open]").count() == 1
        page_.fill("#dialog-slot textarea[name=reason]", "boom")
        page_.click("#dialog-slot button[type=submit]")
        page_.wait_for_selector("text=A rule rejected this.")
        assert page_.locator("#dialog-slot dialog[open]").count() == 1

        # Esc closes it and focus returns to the control that opened it.
        page_.keyboard.press("Escape")
        page_.wait_for_function("() => !document.querySelector('#dialog-slot dialog')")
        assert page_.evaluate("() => document.activeElement.id") == "open-delete"
        assert (
            page_.evaluate("() => getComputedStyle(document.documentElement).overflow")
            != "hidden"
        )

        # A destructive dialog ignores backdrop clicks but the close button works.
        open_delete(page_)
        page_.mouse.click(4, 4)
        assert page_.locator("#dialog-slot dialog[open]").count() == 1
        page_.click("#dialog-slot .btn-close")
        page_.wait_for_function("() => !document.querySelector('#dialog-slot dialog')")
        assert page_.evaluate("() => document.activeElement.id") == "open-delete"

        # Success closes the dialog and reloads with the flash message.
        open_delete(page_)
        page_.fill("#dialog-slot textarea[name=reason]", "Duplicate record")
        page_.click("#dialog-slot button[type=submit]")
        page_.wait_for_selector("text=Deleted.")
        assert page_.locator("dialog[open]").count() == 0
        context.close()
    assert failures == []
    assert natives == []


@pytest.mark.django_db(transaction=True)
def test_done_event_updates_the_page_without_a_reload(live_server):
    cookies = login_cookie(make_user("dialog_lab_member"))
    failures: list[str] = []
    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page_ = context.new_page()
        watch(page_, failures)
        page_.goto(f"{live_server.url}/test-dialogs/")
        ready(page_)
        page_.evaluate("() => { window.__marker = 'same-page'; }")
        page_.click("#open-bump")
        page_.wait_for_selector("#dialog-slot dialog[open]")
        page_.click("#dialog-slot button[type=submit]")
        page_.wait_for_function("() => !document.querySelector('#dialog-slot dialog')")
        page_.wait_for_function(
            "() => document.getElementById('counter').textContent.trim() === 'count 1'"
        )
        assert page_.evaluate("() => window.__marker") == "same-page"
        assert page_.evaluate("() => document.activeElement.id") == "open-bump"
        context.close()
    assert failures == []


@pytest.mark.django_db(transaction=True)
def test_confirmations_replace_native_dialogs(live_server):
    cookies = login_cookie(make_user("dialog_lab_confirm"))
    failures: list[str] = []
    natives: list[str] = []
    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page_ = context.new_page()
        watch(page_, failures)
        dismiss_dialogs(page_, natives)
        page_.goto(f"{live_server.url}/test-dialogs/")
        ready(page_)

        # components/confirm_dialog.html: dismissible, focus on the primary action.
        page_.click("#open-inline")
        assert page_.locator("#inline-confirm").evaluate("el => el.open")
        assert (
            page_.evaluate("() => document.activeElement.textContent.trim()")
            == "Publish"
        )
        page_.mouse.click(4, 4)
        page_.wait_for_function("() => !document.getElementById('inline-confirm').open")
        assert page_.evaluate("() => document.activeElement.id") == "open-inline"
        page_.click("#open-inline")
        page_.click("#inline-confirm button[type=submit]")
        page_.wait_for_selector("text=echoed")

        # data-confirm-dialog on a submit button.
        page_.goto(f"{live_server.url}/test-dialogs/")
        ready(page_)
        page_.click("#confirm-submit")
        page_.wait_for_selector("dialog[data-dialog-js][open]")
        assert (
            page_.locator("dialog[data-dialog-js] [data-dialog-title]").inner_text()
            == "Send it?"
        )
        assert (
            page_.evaluate("() => document.activeElement.textContent.trim()")
            == "Cancel"
        )
        page_.click("dialog[data-dialog-js] [data-dialog-cancel]")
        page_.wait_for_function(
            "() => !document.querySelector('dialog[data-dialog-js]')"
        )
        assert page_.url.endswith("/test-dialogs/")
        assert page_.evaluate("() => document.activeElement.id") == "confirm-submit"
        page_.click("#confirm-submit")
        page_.click("dialog[data-dialog-js] [data-dialog-confirm]")
        page_.wait_for_selector("text=echoed")

        # htmx:confirm interception: confirmed requests go through, cancelled ones do not.
        page_.goto(f"{live_server.url}/test-dialogs/")
        ready(page_)
        page_.click("#htmx-confirm")
        page_.click("dialog[data-dialog-js] [data-dialog-cancel]")
        page_.wait_for_function(
            "() => !document.querySelector('dialog[data-dialog-js]')"
        )
        assert page_.locator("#htmx-result").inner_text() == ""
        page_.click("#htmx-confirm")
        page_.click("dialog[data-dialog-js] [data-dialog-confirm]")
        page_.wait_for_selector("text=fragment loaded")

        # Dialogs.confirm replaces confirm() for JavaScript callers.
        page_.evaluate(
            "() => { window.__answer = undefined;"
            " window.Dialogs.confirm({ title: 'Remove it?', message: 'Gone for good.', danger: true })"
            ".then((value) => { window.__answer = value; }); }"
        )
        page_.wait_for_selector("dialog[data-dialog-js][open]")
        assert (
            page_.evaluate("() => document.activeElement.textContent.trim()")
            == "Cancel"
        )
        page_.keyboard.press("Escape")
        page_.wait_for_function("() => window.__answer === false")
        page_.evaluate(
            "() => { window.__answer = undefined;"
            " window.Dialogs.confirm({ title: 'Remove it?' })"
            ".then((value) => { window.__answer = value; }); }"
        )
        page_.click("dialog[data-dialog-js] [data-dialog-confirm]")
        page_.wait_for_function("() => window.__answer === true")

        # Reason and typed confirmation.
        page_.evaluate(
            "() => { window.__answer = undefined;"
            " window.Dialogs.confirmWithReason({ title: 'Why?', reason: { required: true },"
            " phrase: 'DELETE' }).then((value) => { window.__answer = value; }); }"
        )
        confirm = page_.locator("dialog[data-dialog-js] [data-dialog-confirm]")
        page_.wait_for_selector("dialog[data-dialog-js][open]")
        assert confirm.is_disabled()
        page_.fill("dialog[data-dialog-js] input[name=confirmation]", "DELETE")
        confirm.click()
        page_.wait_for_selector(
            "dialog[data-dialog-js] [data-dialog-reason-error]:not([hidden])"
        )
        assert page_.evaluate("() => document.activeElement.name") == "reason"
        assert page_.locator("dialog[data-dialog-js][open]").count() == 1
        page_.fill("dialog[data-dialog-js] input[name=confirmation]", "")
        page_.fill("dialog[data-dialog-js] textarea", "  cleanup  ")
        page_.fill("dialog[data-dialog-js] input[name=confirmation]", "DELETE")
        assert confirm.is_enabled()
        confirm.click()
        page_.wait_for_function("() => window.__answer === 'cleanup'")
        context.close()
    assert failures == []
    assert natives == []


@pytest.mark.django_db(transaction=True)
def test_unsaved_changes_guard_uses_the_app_dialog(live_server):
    cookies = login_cookie(make_user("dialog_lab_guard"))
    failures: list[str] = []
    natives: list[str] = []
    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page_ = context.new_page()
        watch(page_, failures)
        dismiss_dialogs(page_, natives)
        page_.goto(f"{live_server.url}/test-dialogs/")
        ready(page_)
        browser_prompt = "() => !window.dispatchEvent(new Event('beforeunload', { cancelable: true }))"
        assert page_.evaluate(browser_prompt) is False

        page_.fill("#draft", "work in progress")
        # Tab close / refresh keeps the browser-mandated generic fallback.
        assert page_.evaluate(browser_prompt) is True
        page_.click("#leave")
        page_.wait_for_selector("dialog[data-dialog-js][open]")
        title = page_.locator("dialog[data-dialog-js] [data-dialog-title]")
        assert title.inner_text() == "Discard unsaved changes?"
        page_.click("dialog[data-dialog-js] [data-dialog-cancel]")
        page_.wait_for_function(
            "() => !document.querySelector('dialog[data-dialog-js]')"
        )
        assert page_.url.endswith("/test-dialogs/")
        assert page_.input_value("#draft") == "work in progress"
        page_.click("#leave")
        page_.click("dialog[data-dialog-js] [data-dialog-confirm]")
        page_.wait_for_selector("text=echoed")

        # Saving the form is not "leaving": no prompt of any kind.
        page_.goto(f"{live_server.url}/test-dialogs/")
        ready(page_)
        page_.fill("#draft", "saved text")
        page_.click("#save-draft")
        page_.wait_for_selector("text=echoed")
        context.close()
    assert failures == []
    assert natives == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("width", [360, 1440])
def test_responsive_layout_dark_theme_and_reduced_motion(live_server, width):
    cookies = login_cookie(make_user(f"dialog_lab_layout_{width}", staff=True))
    failures: list[str] = []
    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies, width=width)
        page_ = context.new_page()
        watch(page_, failures)
        page_.goto(f"{live_server.url}/test-dialogs/")
        ready(page_)
        open_delete(page_)
        page_.evaluate(
            "() => Promise.all(document.querySelector('#dialog-slot dialog')"
            ".getAnimations().map((animation) => animation.finished))"
        )
        box = page_.locator("#dialog-slot dialog").bounding_box()
        viewport = page_.viewport_size
        assert box is not None and viewport is not None
        dimensions = overflow(page_)
        assert dimensions["document"] <= dimensions["viewport"] + 1
        if width < 576:
            # Bottom sheet: full width, anchored to the bottom edge.
            assert box["x"] <= 1 and box["width"] >= viewport["width"] - 2
            assert abs(box["y"] + box["height"] - viewport["height"]) <= 2
        else:
            assert box["width"] <= 31 * 16
            assert box["x"] > 100 and box["y"] > 20
        light = page_.evaluate(
            "() => getComputedStyle(document.querySelector('#dialog-slot dialog')).backgroundColor"
        )
        page_.evaluate(
            "() => document.documentElement.setAttribute('data-bs-theme', 'dark')"
        )
        dark = page_.evaluate(
            "() => getComputedStyle(document.querySelector('#dialog-slot dialog')).backgroundColor"
        )
        assert light != dark
        page_.emulate_media(reduced_motion="reduce")
        assert (
            page_.evaluate(
                "() => getComputedStyle(document.querySelector('#dialog-slot dialog')).animationName"
            )
            == "none"
        )
        context.close()
    assert failures == []


@pytest.mark.django_db(transaction=True)
def test_dialog_works_as_a_plain_page_without_javascript(live_server):
    cookies = login_cookie(make_user("dialog_lab_nojs", staff=True))
    with locked_chromium() as browser:
        context = browser.new_context(java_script_enabled=False)
        context.add_cookies(
            [
                {"name": name, "value": value, "url": live_server.url}
                for name, value in cookies.items()
            ]
        )
        page_ = context.new_page()
        page_.goto(f"{live_server.url}/test-dialogs/delete/")
        assert page_.locator("dialog.app-dialog[open]").is_visible()
        page_.fill("textarea[name=reason]", "Duplicate record")
        page_.click("dialog button[type=submit]")
        page_.wait_for_url("**/test-dialogs/")
        assert page_.locator("text=Deleted.").is_visible()
        context.close()


def _scroll_state(page_: Page, selector: str) -> dict[str, float]:
    return page_.evaluate(
        """selector => {
            const element = document.querySelector(selector);
            return {
                scrollHeight: element.scrollHeight,
                clientHeight: element.clientHeight,
                scrollWidth: element.scrollWidth,
                clientWidth: element.clientWidth,
                scrollTop: element.scrollTop,
            };
        }""",
        selector,
    )


@pytest.mark.django_db(transaction=True)
def test_long_content_scrolls_in_the_body_with_a_sticky_footer(live_server):
    """Long text scrolls inside the dialog, the footer stays, and nothing shifts."""
    failures: list[str] = []
    cookies = login_cookie(make_user("dialog_lab_long"))
    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page_ = context.new_page()
        watch(page_, failures)
        page_.goto(f"{live_server.url}/test-dialogs/")
        ready(page_)
        page_.evaluate("() => window.scrollTo(0, 300)")
        heading_before = page_.locator("h1").bounding_box()

        page_.click("#open-long")
        dialog = page_.locator("#long-confirm")
        dialog.wait_for(state="visible")
        body = "#long-confirm .app-dialog__body"
        state = _scroll_state(page_, body)
        assert state["scrollHeight"] > state["clientHeight"] + 100
        # The dialog never grows past the viewport; the long title wraps.
        box = dialog.bounding_box()
        assert box is not None and box["height"] <= 900 - 2 * 8
        assert (
            _scroll_state(page_, "#long-confirm")["scrollWidth"]
            <= (_scroll_state(page_, "#long-confirm")["clientWidth"])
        )
        accept = dialog.get_by_role("button", name="Accept")
        assert accept.is_visible()

        page_.evaluate(
            "selector => { const e = document.querySelector(selector);"
            " e.scrollTop = e.scrollHeight; }",
            body,
        )
        assert _scroll_state(page_, body)["scrollTop"] > 0
        footer = dialog.locator(".app-dialog__footer").bounding_box()
        assert footer is not None and footer["y"] + footer["height"] <= 900
        assert accept.is_visible()
        assert (
            page_.evaluate("() => getComputedStyle(document.documentElement).overflow")
            == "hidden"
        )
        # Scroll lock reserves the scrollbar gutter, so the page does not shift.
        heading_open = page_.locator("h1").bounding_box()
        assert heading_before and heading_open
        assert abs(heading_open["x"] - heading_before["x"]) < 0.5

        page_.keyboard.press("Escape")
        dialog.wait_for(state="hidden")
        assert page_.evaluate("() => document.activeElement.id") == "open-long"
        assert page_.evaluate("() => window.scrollY") == 300
        context.close()
    assert not failures


@pytest.mark.django_db(transaction=True)
def test_size_variants_set_the_dialog_width(live_server):
    """sm, md and lg dialogs open at 30, 40 and 56rem on a wide screen."""
    cookies = login_cookie(make_user("dialog_lab_sizes"))
    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page_ = context.new_page()
        page_.goto(f"{live_server.url}/test-dialogs/")
        ready(page_)
        for name, rem in (("sm", 30), ("md", 40), ("lg", 56)):
            page_.click(f"#open-{name}")
            dialog = page_.locator(f"#size-{name}")
            dialog.wait_for(state="visible")
            page_.evaluate(
                "selector => Promise.all(document.querySelector(selector)"
                ".getAnimations().map((animation) => animation.finished))",
                f"#size-{name}",
            )
            box = dialog.bounding_box()
            assert box is not None and abs(box["width"] - rem * 16) <= 1
            page_.keyboard.press("Escape")
            dialog.wait_for(state="hidden")
        context.close()


@pytest.mark.django_db(transaction=True)
def test_submit_shows_loading_then_a_success_toast_and_restores_focus(live_server):
    """A slow submit shows the loading state, then closes with a success toast."""
    failures: list[str] = []
    cookies = login_cookie(make_user("dialog_lab_note"))
    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page_ = context.new_page()
        watch(page_, failures)
        page_.goto(f"{live_server.url}/test-dialogs/")
        ready(page_)
        page_.click("#open-note")
        page_.wait_for_selector("#dialog-slot dialog[open]")
        form = page_.locator("#dialog-slot .app-dialog__form")
        page_.click("#dialog-slot button[type=submit]")
        page_.wait_for_selector("#dialog-slot form[data-loading]")
        assert form.get_attribute("data-loading") is not None
        page_.wait_for_function("() => !document.querySelector('#dialog-slot dialog')")
        toast = page_.locator("#toast-region .toast-ui--success")
        toast.wait_for(state="visible")
        assert "Note saved." in toast.inner_text()
        assert page_.evaluate("() => document.activeElement.id") == "open-note"
        context.close()
    assert not failures


@pytest.mark.django_db(transaction=True)
def test_a_confirm_over_a_dialog_is_nested_safely(live_server):
    """Esc closes only the top dialog; focus, scroll lock and the lower one stay."""
    failures: list[str] = []
    cookies = login_cookie(make_user("dialog_lab_nested", staff=True))
    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page_ = context.new_page()
        watch(page_, failures)
        page_.goto(f"{live_server.url}/test-dialogs/")
        ready(page_)
        open_delete(page_)
        page_.evaluate(
            """() => {
                window.__nested = "pending";
                window.Dialogs.confirm({title: "Nested?", message: "Over another"})
                    .then((value) => { window.__nested = value; });
            }"""
        )
        page_.wait_for_selector("dialog[data-dialog-js][open]")
        assert page_.evaluate(
            "() => document.activeElement.closest('dialog').dataset.dialogJs"
            " !== undefined"
        )
        for _ in range(6):
            page_.keyboard.press("Tab")
            assert page_.evaluate(
                "() => document.activeElement.closest('dialog').dataset.dialogJs"
                " !== undefined"
            )

        page_.keyboard.press("Escape")
        page_.wait_for_function("() => window.__nested === false")
        assert page_.locator("dialog[data-dialog-js]").count() == 0
        assert page_.locator("#dialog-slot dialog[open]").count() == 1
        assert inside_dialog(page_)
        assert (
            page_.evaluate("() => getComputedStyle(document.documentElement).overflow")
            == "hidden"
        )

        page_.keyboard.press("Escape")
        page_.wait_for_function("() => !document.querySelector('#dialog-slot dialog')")
        assert page_.evaluate("() => document.activeElement.id") == "open-delete"
        context.close()
    assert not failures


@pytest.mark.django_db(transaction=True)
def test_hand_written_dialogs_get_close_trap_and_focus_restore(live_server):
    """A data-app-dialog without the Alpine component still closes, traps and restores."""
    failures: list[str] = []
    cookies = login_cookie(make_user("dialog_lab_plain"))
    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page_ = context.new_page()
        watch(page_, failures)
        page_.goto(f"{live_server.url}/test-dialogs/")
        ready(page_)
        dialog = page_.locator("#plain-dialog")

        page_.click("#open-plain")
        dialog.wait_for(state="visible")
        assert page_.evaluate("() => document.activeElement.id") == "plain-input"
        for _ in range(6):
            page_.keyboard.press("Tab")
            assert inside_dialog(page_)

        dialog.get_by_role("button", name="Cancel").click()
        dialog.wait_for(state="hidden")
        assert page_.evaluate("() => document.activeElement.id") == "open-plain"

        page_.click("#open-plain")
        dialog.wait_for(state="visible")
        page_.keyboard.press("Escape")
        dialog.wait_for(state="hidden")
        assert page_.evaluate("() => document.activeElement.id") == "open-plain"

        page_.click("#open-plain")
        dialog.wait_for(state="visible")
        page_.mouse.click(4, 4)
        dialog.wait_for(state="hidden")
        context.close()
    assert not failures


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", ["inline", "htmx", "javascript"])
def test_every_dialog_kind_is_a_bottom_sheet_on_a_phone(live_server, kind):
    """Shared-shell, HTMX-loaded and JavaScript dialogs sheet up at 360px."""
    failures: list[str] = []
    cookies = login_cookie(make_user(f"dialog_lab_phone_{kind}", staff=True))
    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies, width=360)
        page_ = context.new_page()
        watch(page_, failures)
        page_.goto(f"{live_server.url}/test-dialogs/")
        ready(page_)
        if kind == "inline":
            page_.click("#open-inline")
        elif kind == "htmx":
            open_delete(page_)
        else:
            page_.evaluate(
                "() => { window.Dialogs.confirm({title: 'Sheet', message: 'Hi'}); }"
            )
        dialog = page_.locator("dialog[open]")
        dialog.wait_for(state="visible")
        page_.evaluate(
            "() => Promise.all(document.querySelector('dialog[open]')"
            ".getAnimations().map((animation) => animation.finished))"
        )
        box = dialog.bounding_box()
        assert box is not None
        assert box["x"] <= 1 and box["width"] >= 358
        assert abs(box["y"] + box["height"] - 900) <= 2
        assert overflow(page_)["document"] <= 361
        footer = dialog.locator(".app-dialog__footer .btn").first.bounding_box()
        assert footer is not None and footer["width"] >= 300

        page_.keyboard.press("Escape")
        dialog.wait_for(state="hidden")
        context.close()
    assert not failures
