"""Changelist filter bar and bulk-action bar, in a real browser (Chromium via Playwright).

Covers: the filter toolbar sits above the results table at 360/768/1280 (a bottom sheet behind
"Filters (n)" below 768px), choosing a filter updates the URL and results without a reload,
chips remove / clear all, keyboard-only operation of the listbox popover (WAI-ARIA), and the bulk
bar: token surface with AA contrast in light and dark, no pill radius, readable disabled "Run",
"Clear selection" and "Select all N across pages".
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from tests.backend import design_tokens as tokens
from tests.frontend.browser_support import chromium, login_cookie
from tests.frontend.console_browser_support import (
    ConsoleServer,
    Probe,
    mark_no_reload,
    open_console,
    reloaded,
    serialize_requests,
)

pytestmark = pytest.mark.django_db(transaction=True)

PAGE_SIZE = 50
EXTRA_USERS = 53
WIDTHS = (360, 768, 1280)


def _server(live_server) -> ConsoleServer:
    serialize_requests(live_server)
    model = get_user_model()
    admin_user = model.objects.create_superuser(
        username="filters-admin",
        email="filters-admin@example.test",
        email_verified_at=timezone.now(),
    )
    model.objects.bulk_create(
        [
            model(
                username=f"filter-user-{index}",
                email=f"filter-user-{index}@example.test",
                is_staff=index % 5 == 0,
            )
            for index in range(EXTRA_USERS)
        ]
    )
    return ConsoleServer(live_server.url, admin_user, login_cookie(admin_user))


def _changelist_path() -> str:
    model = get_user_model()._meta
    return reverse(f"admin:{model.app_label}_{model.model_name}_changelist")


def _rgb(value: str) -> tuple[int, int, int]:
    channels = [int(part) for part in re.findall(r"\d+", value)[:3]]
    return channels[0], channels[1], channels[2]


def _hex(value: str) -> str:
    return "#%02x%02x%02x" % _rgb(value)


def _open(browser, server: ConsoleServer, width: int, theme: str = "light"):
    context = server.context(browser, width=width)
    context.add_init_script(f"localStorage.setItem('mobofirm-theme', '{theme}')")
    page = context.new_page()
    probe = Probe(origin=server.url)
    probe.attach(page)
    probe.begin()
    problems: list[str] = []
    open_console(page, probe, server, _changelist_path(), problems)
    return context, page, probe, problems


@pytest.mark.parametrize("width", WIDTHS)
def test_filter_bar_is_above_the_results_table(live_server, width):
    server = _server(live_server)
    with chromium() as browser:
        context, page, probe, problems = _open(browser, server, width)
        try:
            toggle = page.locator(".c-fbar-toggle")
            table_top = page.locator("table.c-table").first.bounding_box()["y"]
            if width < 768:
                assert toggle.is_visible(), "narrow screens get one Filters button"
                assert "Filters" in toggle.inner_text()
                assert not page.locator("#c-cl-filters").is_visible()
                toggle.click()
                page.wait_for_function("() => document.querySelector('#c-cl-filters').hasAttribute('data-open')")
                viewport_height = page.viewport_size["height"]
                # The sheet slides up (200 ms transition): wait until it has settled before measuring.
                page.wait_for_function(
                    "(h) => { const r = document.querySelector('#c-cl-filters').getBoundingClientRect(); return r.bottom <= h + 1; }",
                    arg=viewport_height,
                )
                sheet = page.locator("#c-cl-filters").bounding_box()
                assert sheet["y"] + sheet["height"] <= viewport_height + 1, "the sheet is anchored to the bottom"
                assert toggle.get_attribute("aria-expanded") == "true"
            else:
                assert not toggle.is_visible()
                bar = page.locator("#c-cl-filters").bounding_box()
                assert bar["y"] + bar["height"] <= table_top + 1, "filters must sit above the table"
                assert page.locator(".c-fctl").count() >= 5, "one control per list_filter"
            dimensions = page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
            assert dimensions[0] <= dimensions[1] + 1, f"horizontal overflow at {width}px: {dimensions}"
        finally:
            problems.extend(probe.end())
            context.close()
    assert problems == []


def test_choosing_a_filter_updates_url_and_results_without_a_reload(live_server):
    server = _server(live_server)
    with chromium() as browser:
        context, page, probe, problems = _open(browser, server, 1280)
        try:
            mark_no_reload(page)
            rows_before = page.locator("input[data-console-row-select]").count()
            status_filter = page.locator(".c-fctl", has=page.locator(".c-fctl__title", has_text="Is staff"))
            status_filter.locator("[data-ui-trigger]").click()
            status_filter.locator("[role=option]", has_text="Yes").click()
            page.wait_for_url(re.compile(r"is_staff__exact=1"))
            page.wait_for_function(
                "(before) => document.querySelectorAll('input[data-console-row-select]').length < before", arg=rows_before
            )
            assert not reloaded(page), "HTMX must swap the results region, not reload the page"
            chip = page.locator("#c-cl-chips .c-chip", has_text="Is staff")
            assert chip.count() == 1
            assert "result" in page.locator("#c-cl-status").inner_text()
            assert page.locator("#c-cl-status").get_attribute("aria-live") == "polite"
            assert page.locator(".c-fctl[data-active]").count() == 1

            chip.locator(".c-chip__remove").click()
            page.wait_for_function("() => !location.search.includes('is_staff__exact')")
            assert page.locator("#c-cl-chips .c-chip").count() == 0

            status_filter = page.locator(".c-fctl", has=page.locator(".c-fctl__title", has_text="Is staff"))
            status_filter.locator("[data-ui-trigger]").click()
            status_filter.locator("[role=option]", has_text="Yes").click()
            page.wait_for_url(re.compile(r"is_staff__exact=1"))
            page.locator(".c-fctl", has=page.locator(".c-fctl__title", has_text="Is active")).locator("[data-ui-trigger]").click()
            page.locator("[data-ui-popover][data-open] [role=option]", has_text="Yes").click()
            page.wait_for_url(re.compile(r"is_active__exact=1"))
            page.locator("#c-cl-chips .c-chip--add", has_text="Clear all").click()
            page.wait_for_function("() => !location.search.includes('__exact')")
            assert not reloaded(page)
        finally:
            problems.extend(probe.end())
            context.close()
    assert problems == []


def test_listbox_filter_is_keyboard_operable(live_server):
    server = _server(live_server)
    with chromium() as browser:
        context, page, probe, problems = _open(browser, server, 1280)
        try:
            filt = page.locator(".c-fctl", has=page.locator(".c-fctl__title", has_text="Is staff"))
            trigger = filt.locator("[data-ui-trigger]")
            trigger.focus()
            assert trigger.get_attribute("aria-haspopup") == "listbox"
            assert trigger.get_attribute("aria-expanded") == "false"
            page.keyboard.press("ArrowDown")
            page.wait_for_function("() => document.querySelector('.c-fctl[data-open]')")
            assert trigger.get_attribute("aria-expanded") == "true"
            options = filt.locator("[role=option]")
            assert options.count() >= 3
            # The selected (or first) option takes focus and exposes data-active.
            assert page.evaluate("() => document.activeElement.getAttribute('role')") == "option"
            assert filt.locator("[role=option][data-active]").count() == 1
            page.keyboard.press("End")
            last = options.count() - 1
            assert page.evaluate("(i) => document.activeElement === document.querySelectorAll('.c-fctl[data-open] [role=option]')[i]", last)
            page.keyboard.press("Home")
            assert page.evaluate("() => document.activeElement === document.querySelector('.c-fctl[data-open] [role=option]')")
            page.keyboard.press("y")
            assert page.evaluate("() => document.activeElement.textContent.trim().toLowerCase().startsWith('y')"), "type-ahead"
            page.keyboard.press("Escape")
            assert trigger.get_attribute("aria-expanded") == "false"
            assert page.evaluate("() => document.activeElement === document.querySelector('.c-fctl [data-ui-trigger]:focus')")
            assert filt.locator("[data-ui-panel]").is_hidden()
            # Enter on the chosen option follows the filter link.
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            page.wait_for_url(re.compile(r"is_staff__exact="))
        finally:
            problems.extend(probe.end())
            context.close()
    assert problems == []


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_bulk_bar_is_a_token_surface_with_aa_contrast(live_server, theme):
    server = _server(live_server)
    palette = tokens.themes()[theme]
    with chromium() as browser:
        context, page, probe, problems = _open(browser, server, 1280, theme)
        try:
            bar = page.locator(".c-bulkbar")
            assert not bar.is_visible()
            page.locator("input[data-console-row-select]").nth(0).check()
            page.locator("input[data-console-row-select]").nth(1).check()
            assert bar.is_visible()
            assert "2" in bar.locator("[data-console-selected-count]").inner_text()
            assert bar.locator(".c-bulkbar__count").get_attribute("aria-live") == "polite"

            style = bar.evaluate(
                "(el) => { const c = getComputedStyle(el); return {bg: c.backgroundColor, color: c.color, radius: parseFloat(c.borderRadius), height: el.getBoundingClientRect().height}; }"
            )
            assert _hex(style["bg"]) == palette["surface-raised"], "bar surface comes from the token set"
            assert _hex(style["color"]) == palette["text"]
            assert tokens.contrast(_hex(style["color"]), _hex(style["bg"])) >= 4.5
            assert style["radius"] < style["height"] / 2, "no pill shape"
            assert style["radius"] > 0

            run = bar.locator("button[type=submit]")
            assert run.is_disabled(), "Run is disabled until an action is chosen"
            assert run.get_attribute("title"), "the disabled state explains itself"
            run_style = run.evaluate("(el) => { const c = getComputedStyle(el); return {bg: c.backgroundColor, color: c.color}; }")
            assert tokens.contrast(_hex(run_style["color"]), _hex(run_style["bg"])) >= 3.0, "disabled Run stays readable"
            assert tokens.contrast(_hex(run_style["bg"]), _hex(style["bg"])) >= 1.0

            bar.locator("select[name=action]").select_option(index=1)
            assert run.is_enabled()
            bar.locator("[data-console-clear-selection]").click()
            assert not bar.is_visible()
            assert page.locator("input[data-console-row-select]:checked").count() == 0
        finally:
            problems.extend(probe.end())
            context.close()
    assert problems == []


def test_select_all_across_pages(live_server):
    server = _server(live_server)
    with chromium() as browser:
        context, page, probe, problems = _open(browser, server, 1280)
        try:
            assert page.locator("input[data-console-row-select]").count() == PAGE_SIZE
            page.locator("input[data-console-select-all]").first.check()
            across = page.locator("[data-console-select-across]")
            assert across.is_visible()
            assert re.search(r"Select all \d+", across.inner_text())
            across.click()
            assert page.locator("input[name=select_across]").input_value() == "1"
            page.locator("[data-console-clear-selection]").click()
            assert page.locator("input[name=select_across]").input_value() == "0"
        finally:
            problems.extend(probe.end())
            context.close()
    assert problems == []
