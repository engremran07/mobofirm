"""Retractable navigation and HTMX-boosted navigation of the staff console, in a real browser.

Covers the owner requirements for the sidebar (groups collapse/expand with aria-expanded/aria-controls, per-user state
that survives reloads and HTMX navigation, collapse/expand all, filter, icon rail with tooltips, mobile drawer), the
boosted content swap (no full reload, title/breadcrumbs/active link follow, preload on hover) and the dashboard
widgets (error state with retry, automatic refresh paused in a hidden tab).
Every test fails on console errors/warnings, CSP violations, external requests and native dialogs.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from playwright.sync_api import BrowserContext, Page, expect

from tests.frontend.browser_support import chromium, login_cookie
from tests.frontend.console_browser_support import (
    HOOKS,
    ConsoleServer,
    Probe,
    mark_no_reload,
    open_console,
    reloaded,
    serialize_requests,
    visible_first,
)

pytestmark = pytest.mark.django_db(transaction=True)

GROUPS = "aside[data-console-sidebar] [data-console-nav-group]"
FILTER = "aside[data-console-sidebar] [data-console-nav-filter]"
WIDGET_URL = "**/admin/console/widgets/health/"


def _server(live_server) -> ConsoleServer:
    serialize_requests(live_server)
    user = get_user_model().objects.create_superuser(
        username="console-nav",
        email="console-nav@example.test",
        email_verified_at=timezone.now(),
    )
    return ConsoleServer(live_server.url, user, login_cookie(user))


@contextmanager
def _session(
    server: ConsoleServer, *, width: int = 1365, **options
) -> Iterator[tuple[Page, Probe, list[str], BrowserContext]]:
    problems: list[str] = []
    with chromium() as browser:
        context = server.context(browser, width=width, **options)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        probe.begin()
        try:
            yield page, probe, problems, context
        finally:
            problems.extend(probe.end())
            problems.extend(
                f"CSP violation: {violation}"
                for violation in page.evaluate("() => window.__cspViolations || []")
            )
            context.close()


def _assert_clean(problems: list[str]) -> None:
    assert not problems, "\n".join(problems)


def _group(page: Page, name: str):
    """The group li whose disclosure button is labelled ``name``."""
    return page.locator(f"{GROUPS}:has(> button[data-nav-label='{name}'])")


def _toggle(page: Page, name: str):
    return page.locator(f"{GROUPS} > button[data-nav-label='{name}']")

def _expanded(page: Page) -> list[str]:
    return page.eval_on_selector_all(
        f"{GROUPS} > button",
        "buttons => buttons.map(b => b.getAttribute('aria-expanded'))",
    )


# -- groups ------------------------------------------------------------------------------
def test_nav_groups_collapse_expand_with_keyboard_and_persist_across_reloads(live_server):
    server = _server(live_server)
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/", problems)
        catalog = _toggle(page, "Catalog")
        panel = _group(page, "Catalog").locator(".c-nav-panel")
        brands = _group(page, "Catalog").get_by_role("link", name="Brands")

        expect(catalog).to_have_attribute("aria-expanded", "false")
        expect(brands).to_be_hidden()
        catalog.click()
        expect(catalog).to_have_attribute("aria-expanded", "true")
        expect(brands).to_be_visible()
        assert catalog.get_attribute("aria-controls") == panel.get_attribute("id")

        catalog.focus()
        page.keyboard.press("Enter")
        expect(catalog).to_have_attribute("aria-expanded", "false")
        expect(brands).to_be_hidden()
        page.keyboard.press("Space")
        expect(catalog).to_have_attribute("aria-expanded", "true")

        page.reload(wait_until="load")
        expect(_toggle(page, "Catalog")).to_have_attribute("aria-expanded", "true")
        expect(_toggle(page, "Firmware")).to_have_attribute("aria-expanded", "false")

        page.get_by_role("button", name="Collapse all groups").click()
        assert set(_expanded(page)) == {"false"}
        page.reload(wait_until="load")
        assert set(_expanded(page)) == {"false"}
        page.get_by_role("button", name="Expand all groups").click()
        assert set(_expanded(page)) == {"true"}
        page.reload(wait_until="load")
        assert set(_expanded(page)) == {"true"}
    _assert_clean(problems)


def test_the_group_holding_the_current_page_is_open_and_only_one_group_stays_open(live_server):
    server = _server(live_server)
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/firmwares/brand/", problems)
        expect(_toggle(page, "Catalog")).to_have_attribute("aria-expanded", "true")
        current = _group(page, "Catalog").get_by_role("link", name="Brands")
        expect(current).to_have_attribute("aria-current", "page")
        assert page.locator("aside[data-console-sidebar] [aria-current='page']").count() == 1

        # Accordion is the default: opening another group retracts the previous one.
        accordion = page.get_by_role("button", name="Keep only one group open")
        expect(accordion).to_have_attribute("aria-pressed", "true")
        _toggle(page, "Firmware").click()
        expect(_toggle(page, "Firmware")).to_have_attribute("aria-expanded", "true")
        expect(_toggle(page, "Catalog")).to_have_attribute("aria-expanded", "false")
        _toggle(page, "Tools & guides").click()
        expect(_toggle(page, "Firmware")).to_have_attribute("aria-expanded", "false")
        assert _expanded(page).count("true") == 1

        # Switching it off lets several groups stay open, and the choice persists.
        accordion.click()
        expect(accordion).to_have_attribute("aria-pressed", "false")
        _toggle(page, "Firmware").click()
        assert _expanded(page).count("true") == 2
        page.reload(wait_until="load")
        expect(accordion).to_have_attribute("aria-pressed", "false")
    _assert_clean(problems)


def test_nav_state_is_stored_per_user(live_server):
    server = _server(live_server)
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/", problems)
        _toggle(page, "Catalog").click()
        keys = page.evaluate(
            "() => Object.keys(localStorage).filter(k => k.startsWith('mobofirm-console-nav-groups:'))"
        )
        assert keys == [f"mobofirm-console-nav-groups:{server.user.pk}"]
    _assert_clean(problems)


# -- filter ------------------------------------------------------------------------------
def test_filter_narrows_the_navigation_and_escape_restores_it(live_server):
    server = _server(live_server)
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/", problems)
        box = page.locator(FILTER)
        box.fill("chipsets")
        chipsets = page.locator("aside[data-console-sidebar]").get_by_role("link", name="Chipsets")
        expect(chipsets).to_be_visible()
        expect(page.locator("aside[data-console-sidebar]").get_by_role("link", name="Brands")).to_be_hidden()
        expect(_toggle(page, "Catalog")).to_have_attribute("aria-expanded", "true")
        assert page.locator(f"{GROUPS}:visible").count() == 1

        box.fill("zzzz-no-such-page")
        expect(page.get_by_text("No pages match your filter.")).to_be_visible()
        box.press("Escape")
        expect(box).to_have_value("")
        assert page.locator(f"{GROUPS}:visible").count() > 5
        expect(_toggle(page, "Catalog")).to_have_attribute("aria-expanded", "false")

        box.fill("fir")
        box.press("ArrowDown")
        expect(page.locator("aside[data-console-sidebar] a:focus, aside[data-console-sidebar] button:focus")).to_have_count(1)
    _assert_clean(problems)


# -- boosted navigation -------------------------------------------------------------------
def test_boosted_navigation_swaps_only_the_content_and_keeps_the_sidebar_state(live_server):
    server = _server(live_server)
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/", problems)
        mark_no_reload(page)
        page.locator("aside[data-console-sidebar]").evaluate("el => { el.dataset.survivor = 'yes'; }")
        _toggle(page, "Catalog").click()
        _toggle(page, "Firmware").click()
        page.locator(FILTER).fill("brand")
        dashboard_title = page.title()

        brands = _group(page, "Catalog").get_by_role("link", name="Brands")
        brands.hover()
        # the preload extension fetches the page on hover; the click reuses that response
        fetched: list[str] = []
        page.on("request", lambda request: fetched.append(request.url) if request.url.endswith("/firmwares/brand/") else None)
        brands.click()
        page.wait_for_url(re.compile(r"/admin/firmwares/brand/$"))
        expect(page.locator(HOOKS["main"]).get_by_role("heading", level=1)).to_be_visible()

        assert not reloaded(page), "a sidebar click must swap the content, not reload the document"
        assert page.title() != dashboard_title
        assert page.locator("aside[data-console-sidebar]").get_attribute("data-survivor") == "yes"
        assert len(fetched) <= 1
        expect(brands).to_have_attribute("aria-current", "page")
        assert page.locator("aside[data-console-sidebar] [aria-current='page']").count() == 1
        expect(page.locator(FILTER)).to_have_value("brand")
        expect(_toggle(page, "Catalog")).to_have_attribute("aria-expanded", "true")
        expect(page.locator(HOOKS["main"])).to_be_focused()
        assert page.locator("#console-main nav[aria-label]").count() >= 1  # breadcrumbs came with the swap

        page.go_back()
        page.wait_for_url(re.compile(r"/admin/$"))
        expect(page.get_by_role("heading", level=1)).to_be_visible()
        expect(page).to_have_title(dashboard_title)
        assert not reloaded(page)
        _toggle(page, "Catalog").click()  # the restored sidebar still works
        expect(page.locator("html")).to_have_attribute("data-console-sidebar", "expanded")
    _assert_clean(problems)


def test_only_console_links_are_boosted(live_server):
    server = _server(live_server)
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/", problems)
        page.locator("body").evaluate(
            """body => {
              for (const [id, href] of [['outside', '/'], ['file', '/admin/export.csv'], ['inside', '/admin/firmwares/brand/']]) {
                const a = document.createElement('a'); a.id = 'probe-' + id; a.href = href; a.textContent = id;
                body.append(a);
              }
              window.htmx.process(body);
            }"""
        )
        powered = page.evaluate(
            "() => ['outside', 'file', 'inside'].map(id => document.getElementById('probe-' + id).hasAttribute('data-htmx-powered'))"
        )
        assert powered == [False, False, True]
        assert not page.locator("form[method='post' i][data-htmx-powered]").count()
    _assert_clean(problems)


def test_navigation_works_without_javascript(live_server):
    server = _server(live_server)
    with chromium() as browser:
        context = server.context(browser, java_script_enabled=False)
        page = context.new_page()
        page.goto(f"{server.url}/admin/", wait_until="load")
        expect(page.get_by_role("link", name="Brands")).to_be_visible()
        page.get_by_role("link", name="Brands").click()
        page.wait_for_url(re.compile(r"/admin/firmwares/brand/$"))
        expect(page.get_by_role("link", name="Chipsets")).to_be_visible()
        expect(page.get_by_role("button", name="Collapse all groups")).to_be_hidden()
        context.close()

# -- icon rail -----------------------------------------------------------------------------
def test_icon_rail_shows_tooltips_and_a_group_icon_reopens_the_sidebar(live_server):
    server = _server(live_server)
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/", problems)
        html = page.locator("html")
        visible_first(page, HOOKS["sidebar_toggle"]).click()
        expect(html).to_have_attribute("data-console-sidebar", "mini")
        sidebar = page.locator(HOOKS["sidebar"])
        assert sidebar.bounding_box()["width"] < 100
        expect(page.locator("aside[data-console-sidebar] .c-nav-panel").first).to_be_hidden()

        catalog = _toggle(page, "Catalog")
        catalog.hover()
        expect(page.get_by_role("tooltip")).to_have_text("Catalog")
        page.keyboard.press("Escape")
        expect(page.get_by_role("tooltip")).to_have_count(0)

        mark_no_reload(page)
        page.get_by_role("link", name="Dashboard").hover()
        expect(page.get_by_role("tooltip")).to_have_text("Dashboard")
        catalog.click()
        expect(html).to_have_attribute("data-console-sidebar", "expanded")
        expect(_toggle(page, "Catalog")).to_have_attribute("aria-expanded", "true")
        assert sidebar.bounding_box()["width"] > 200
    _assert_clean(problems)


def test_rail_survives_boosted_navigation_and_reload(live_server):
    server = _server(live_server)
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/firmwares/brand/", problems)
        html = page.locator("html")
        visible_first(page, HOOKS["sidebar_toggle"]).click()
        expect(html).to_have_attribute("data-console-sidebar", "mini")
        mark_no_reload(page)
        page.locator("aside[data-console-sidebar]").get_by_role("link", name="Dashboard").click()
        page.wait_for_url(re.compile(r"/admin/$"))
        assert not reloaded(page)
        expect(html).to_have_attribute("data-console-sidebar", "mini")
        expect(page.locator("body")).to_have_class(re.compile(r"\bsidebar-collapse\b"))
        page.reload(wait_until="load")
        expect(html).to_have_attribute("data-console-sidebar", "mini")
    _assert_clean(problems)


# -- mobile drawer --------------------------------------------------------------------------
def test_mobile_drawer_groups_work_and_following_a_link_closes_the_drawer(live_server):
    server = _server(live_server)
    with _session(server, width=390) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/", problems)
        html = page.locator("html")
        toggle = visible_first(page, HOOKS["sidebar_toggle"])
        expect(page.locator(HOOKS["sidebar"])).to_be_hidden()
        toggle.click()
        expect(html).to_have_attribute("data-console-sidebar", "open")
        _toggle(page, "Catalog").click()
        expect(_toggle(page, "Catalog")).to_have_attribute("aria-expanded", "true")
        mark_no_reload(page)
        _group(page, "Catalog").get_by_role("link", name="Models").click()
        page.wait_for_url(re.compile(r"/admin/firmwares/model/$"))
        expect(html).to_have_attribute("data-console-sidebar", "closed")
        assert not reloaded(page)
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        toggle.click()
        expect(_toggle(page, "Catalog")).to_have_attribute("aria-expanded", "true")
    _assert_clean(problems)


# -- dashboard widgets -----------------------------------------------------------------------
def test_a_failing_widget_shows_an_error_with_retry_instead_of_a_skeleton(live_server):
    server = _server(live_server)
    with _session(server) as (page, probe, problems, _context):
        failing = {"on": True}

        def fail_then_pass(route):
            if failing["on"]:
                route.fulfill(status=500, content_type="text/html", body="<h1>boom</h1>")
            else:
                route.continue_()

        page.route(WIDGET_URL, fail_then_pass)
        open_console(page, probe, server, "/admin/", problems)
        widget = page.locator("[data-console-widget='health']")
        expect(widget).to_have_attribute("data-state", "error")
        expect(widget).to_have_attribute("aria-busy", "false")
        expect(widget.locator(".c-skeleton-group")).to_be_hidden()
        expect(widget.get_by_role("alert")).to_contain_text("couldn't load")
        failing["on"] = False
        widget.get_by_role("button", name="Try again").click()
        expect(page.locator("#widget-health .c-queue, #widget-health .c-empty")).to_be_visible()
        expect(page.locator("#widget-health")).not_to_have_attribute("data-state", "error")
        # the 500 is the point of this test: it is the only expected problem
        problems.extend(probe.end())
        probe.begin()
        problems[:] = [
            line for line in problems if "/console/widgets/health/" not in line and "500" not in line
        ]
    _assert_clean(problems)


def test_widgets_refresh_on_their_own_and_pause_while_the_tab_is_hidden(live_server):
    server = _server(live_server)
    with _session(server) as (page, probe, problems, _context):
        calls: list[str] = []
        page.on("request", lambda request: calls.append(request.url) if "/console/widgets/health/" in request.url else None)
        page.clock.install()
        open_console(page, probe, server, "/admin/", problems)
        page.clock.resume()
        expect(page.locator("#widget-health[data-console-refresh]")).to_be_visible()
        initial = len(calls)
        assert initial >= 1

        page.evaluate(
            """() => {
              Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
              document.dispatchEvent(new Event('visibilitychange'));
            }"""
        )
        page.clock.fast_forward(130_000)
        page.wait_for_timeout(500)
        assert len(calls) == initial, "a hidden tab must not poll"

        page.evaluate(
            """() => {
              Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
              document.dispatchEvent(new Event('visibilitychange'));
            }"""
        )
        page.clock.fast_forward(15_000)
        for _ in range(40):
            if len(calls) > initial:
                break
            page.wait_for_timeout(250)
        assert len(calls) > initial
    _assert_clean(problems)
