"""Browser checks for the public design system (tokens, theme, palette, toasts)."""

from __future__ import annotations

import json
import re

import pytest
from django.urls import reverse
from playwright.sync_api import Page, Route, expect

from apps.firmwares.models import Brand, Model
from tests.frontend.browser_support import chromium, overflow, ready, watch

WIDTHS = (360, 768, 1440)
SHOWCASE = """
<section id="showcase" class="site-container">
  <div class="tabs-ui" role="tablist" data-tabs aria-label="Demo">
    <button role="tab" id="t1" aria-selected="true" aria-controls="p1">One</button>
    <button role="tab" id="t2" aria-selected="false" tabindex="-1"
            aria-controls="p2">Two</button>
  </div>
  <div role="tabpanel" id="p1" aria-labelledby="t1">First</div>
  <div role="tabpanel" id="p2" aria-labelledby="t2" hidden>Second</div>
  <div class="accordion-ui"><details><summary>More</summary>
    <div class="accordion-ui__body">Body</div></details></div>
  <button id="copy" class="btn btn-soft" data-copy="ABC-123">Copy</button>
  <div id="host" class="skeleton-host"><p>Result</p></div>
  <span class="badge-ui badge-ui--free">Free</span>
  <span class="badge-ui badge-ui--featured">Featured</span>
  <div class="card-grid" id="cards">
    <article class="card-ui card-ui--firmware">
      <div class="card-ui__body"><h3 class="card-ui__title">
        <a class="card-ui__link" href="/firmwares/browse/">ZX-5 build</a></h3></div>
    </article>
  </div>
</section>
"""


def _prepare(page: Page, live_server, path: str = "/") -> None:
    page.goto(f"{live_server.url}{path}", wait_until="load")
    _settle(page)


def _settle(page: Page) -> None:
    ready(page)
    # The first-visit consent card floats over the footer controls under test.
    page.evaluate("document.querySelector('.consent-banner')?.remove()")


def _reload(page: Page) -> None:
    page.reload()
    _settle(page)


def _root_value(page: Page, name: str) -> str:
    return page.evaluate(
        "name => getComputedStyle(document.documentElement)"
        ".getPropertyValue(name).trim()",
        name,
    )


@pytest.mark.django_db(transaction=True)
def test_design_tokens_are_defined_and_follow_the_theme(live_server):
    """Scale tokens exist and the colour tokens change between themes."""
    with chromium() as browser:
        page = browser.new_page()
        _prepare(page, live_server)
        for name in (
            "--mf-text-xl",
            "--mf-space-4",
            "--mf-radius-xl",
            "--mf-elev-3",
            "--mf-glass-blur",
            "--mf-gradient-accent",
            "--mf-duration-base",
            "--mf-ease-out",
        ):
            assert _root_value(page, name), f"{name} is not defined"
        light = _root_value(page, "--mf-page")
        page.locator("[data-theme-set='dark']").click()
        assert page.locator("html").get_attribute("data-bs-theme") == "dark"
        assert _root_value(page, "--mf-page") != light
        assert page.evaluate("getComputedStyle(document.body).fontFamily")


@pytest.mark.django_db(transaction=True)
def test_theme_mode_and_contrast_persist_across_reloads(live_server):
    """Auto/light/dark and high contrast are stored and restored before paint."""
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page()
        watch(page, failures)
        _prepare(page, live_server)
        root = page.locator("html")
        assert root.get_attribute("data-theme-mode") == "auto"

        page.locator("[data-theme-set='dark']").click()
        _reload(page)
        assert root.get_attribute("data-bs-theme") == "dark"
        assert root.get_attribute("data-theme-mode") == "dark"
        assert page.locator("[data-theme-set='dark']").get_attribute(
            "aria-pressed"
        ) == ("true")

        page.locator("[data-contrast-toggle]").first.click()
        _reload(page)
        assert root.get_attribute("data-contrast") == "high"
        assert _root_value(page, "--mf-text") in {"#ffffff", "#fff"}

        page.locator("[data-theme-set='auto']").click()
        assert page.evaluate("localStorage.getItem('mobofirm-theme')") is None
        assert root.get_attribute("data-theme-mode") == "auto"
    assert not failures, failures


@pytest.mark.django_db(transaction=True)
def test_command_palette_opens_with_the_shortcut_and_reuses_site_search(live_server):
    """Ctrl+K opens a modal palette whose search is the shared combobox."""
    brand = Brand.objects.create(name="Zephyrix", slug="zephyrix")
    Model.objects.create(
        brand=brand, name="Zephyrix Nova 5", slug="zephyrix-nova-5", model_code="ZX-5"
    )
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        watch(page, failures)
        _prepare(page, live_server)
        dialog = page.locator("#command-palette")
        assert not dialog.is_visible()

        page.keyboard.press("Control+k")
        expect(dialog).to_be_visible()
        assert dialog.evaluate("element => element.matches(':modal')")
        assert page.evaluate("document.activeElement.id") == "palette-search"
        assert dialog.locator("[role='combobox']").count() == 1

        page.keyboard.type("Zephyrix", delay=30)
        options = page.locator("#palette-search-results [role='option']")
        expect(options.first).to_be_visible(timeout=5_000)

        # Escape closes the suggestions, then clears the query, then the palette.
        for _ in range(3):
            page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()

        page.locator("button[data-command-palette-open]:visible").first.click()
        expect(dialog).to_be_visible()
        dialog.locator("a.cmdk__item", has_text="Brands").click()
        page.wait_for_url(f"**{reverse('firmwares:brand_list')}")
    assert not failures, failures


@pytest.mark.django_db(transaction=True)
def test_palette_is_contained_on_a_phone(live_server):
    """The palette fits a 360px viewport and closes from its backdrop."""
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 360, "height": 640})
        _prepare(page, live_server)
        page.locator("button[data-command-palette-open]:visible").first.click()
        box = page.locator("#command-palette").bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 360
        page.mouse.click(5, 635)
        expect(page.locator("#command-palette")).not_to_be_visible()


@pytest.mark.django_db(transaction=True)
def test_mega_menu_opens_on_hover_and_by_keyboard(live_server):
    """Panels open from hover or the toggle button and Escape returns focus."""
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _prepare(page, live_server)
        panel = page.locator("#mega-brands")
        assert not panel.is_visible()

        page.locator("#primary-navigation").get_by_role("link", name="Brands").hover()
        expect(panel).to_be_visible()
        page.mouse.move(700, 600)
        expect(panel).not_to_be_visible()

        toggle = page.get_by_role("button", name="Brands menu")
        toggle.focus()
        page.keyboard.press("Enter")
        expect(panel).to_be_visible()
        assert toggle.get_attribute("aria-expanded") == "true"
        page.keyboard.press("Escape")
        expect(panel).not_to_be_visible()
        assert page.evaluate("document.activeElement.getAttribute('aria-label')") == (
            "Brands menu"
        )
        assert overflow(page)["document"] <= 1440 + 1


@pytest.mark.django_db(transaction=True)
def test_htmx_events_become_toasts(live_server):
    """Server HX-Trigger toasts, Django messages and failures show as toasts."""
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        watch(page, failures)
        trigger = json.dumps({"toast": {"message": "Saved it", "level": "success"}})
        flash = (
            '<div data-flash-messages><div class="alert alert-info" '
            'data-flash="info">Heads up</div></div>'
        )
        page.route(
            "**/__toast/ok/",
            lambda route: route.fulfill(
                status=200, headers={"HX-Trigger": trigger}, body="<p>ok</p>"
            ),
        )
        page.route(
            "**/__toast/flash/",
            lambda route: route.fulfill(status=200, body=flash),
        )
        page.route(
            "**/__toast/fail/", lambda route: route.fulfill(status=403, body="no")
        )
        _prepare(page, live_server)
        page.evaluate("document.querySelector('main').id = 'main-content'")

        page.evaluate(
            "() => { htmx.ajax('GET', '/__toast/ok/', "
            "{target: '#main-content', swap: 'none'}); }"
        )
        success = page.locator("#toast-region .toast-ui--success")
        expect(success).to_be_visible()
        assert success.get_attribute("role") == "status"
        assert "Saved it" in success.inner_text()

        page.evaluate(
            "() => { htmx.ajax('GET', '/__toast/flash/', "
            "{target: '#main-content', swap: 'beforeend'}); }"
        )
        expect(page.locator("#toast-region .toast-ui--info")).to_be_visible()
        assert page.locator("[data-flash-messages]").count() == 0

        page.evaluate(
            "() => { htmx.ajax('GET', '/__toast/fail/', "
            "{target: '#main-content', swap: 'none'}); }"
        )
        danger = page.locator("#toast-region .toast-ui--danger")
        expect(danger).to_be_visible()
        assert danger.get_attribute("role") == "alert"
        danger.get_by_role("button", name="Dismiss notification").click()
        expect(danger).to_have_count(0)
    assert [item for item in failures if "Response Status Error" not in item] == []


def _duration(value: str) -> float:
    """Parse a CSS time such as ``200ms`` or the minified ``.2s`` in milliseconds."""
    if value.endswith("ms"):
        return float(value.removesuffix("ms"))
    return float(value.removesuffix("s")) * 1000


@pytest.mark.django_db(transaction=True)
def test_htmx_requests_show_the_progress_bar_and_skeleton_state(live_server):
    """While a request runs the global bar and skeleton host show loading state."""
    held: list = []
    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        def hold(route: Route) -> None:
            """Keep the request pending until the test releases it."""
            held.append(route)

        page.route("**/__slow/", hold)
        _prepare(page, live_server)
        page.evaluate(
            "html => document.querySelector('main').insertAdjacentHTML("
            "'beforeend', html)",
            SHOWCASE,
        )
        progress = page.locator("#global-progress")
        assert progress.evaluate("e => getComputedStyle(e).opacity") == "0"

        page.evaluate(
            "() => { htmx.ajax('GET', '/__slow/', "
            "{source: '#host', target: '#host', swap: 'innerHTML'}); }"
        )
        host = page.locator("#host")
        expect(host).to_have_attribute("data-loading", "")
        expect(progress).to_have_class(re.compile(r"htmx-request"))
        expect(progress).to_have_css("opacity", "1")
        expect(host).not_to_have_css("opacity", "1")

        held[0].fulfill(status=200, body="<p>Loaded</p>")
        expect(host).not_to_have_attribute("data-loading", "")
        expect(host).to_contain_text("Loaded")
        expect(progress).not_to_have_class(re.compile(r"htmx-request"))
        expect(progress).to_have_css("opacity", "0")


@pytest.mark.django_db(transaction=True)
def test_reduced_motion_collapses_durations_and_removes_animation(live_server):
    """prefers-reduced-motion zeroes the motion tokens and disables skeleton shimmer."""
    with chromium() as browser:
        context = browser.new_context(reduced_motion="reduce")
        page = context.new_page()
        _prepare(page, live_server)
        assert _duration(_root_value(page, "--mf-duration-base")) == 0.01
        page.evaluate(
            "html => document.querySelector('main').insertAdjacentHTML("
            "'beforeend', html)",
            SHOWCASE + '<span id="sk" class="skeleton skeleton--text"></span>',
        )
        assert (
            page.locator("#sk").evaluate(
                "element => getComputedStyle(element).animationName"
            )
            == "none"
        )

        normal = browser.new_context()
        other = normal.new_page()
        _prepare(other, live_server)
        assert _duration(_root_value(other, "--mf-duration-base")) == 200
        other.evaluate("document.documentElement.dataset.motion = 'reduce'")
        assert _duration(_root_value(other, "--mf-duration-base")) == 0.01


@pytest.mark.django_db(transaction=True)
def test_component_behaviours_tabs_copy_and_infinite_scroll(live_server):
    """Tabs use roving focus, copy gives feedback and revealed rows load more."""
    failures: list[str] = []
    with chromium() as browser:
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()
        watch(page, failures)
        page.route(
            "**/__rows/",
            lambda route: route.fulfill(
                status=200,
                body='<article class="card-ui" id="row-loaded">Loaded row</article>',
            ),
        )
        _prepare(page, live_server)
        page.evaluate(
            "html => document.querySelector('main').insertAdjacentHTML("
            "'beforeend', html)",
            SHOWCASE + '<div id="more" class="load-more" hx-get="/__rows/" '
            'hx-trigger="revealed" hx-swap="outerHTML"></div>',
        )
        page.evaluate("htmx.process(document.querySelector('#more'))")

        page.locator("#t1").focus()
        page.keyboard.press("ArrowRight")
        assert page.locator("#t2").get_attribute("aria-selected") == "true"
        assert page.locator("#p2").is_visible() and not page.locator("#p1").is_visible()
        assert page.evaluate("document.activeElement.id") == "t2"

        page.locator("#copy").click()
        expect(page.locator("#toast-region .toast-ui--success")).to_be_visible()
        assert page.evaluate("navigator.clipboard.readText()") == "ABC-123"
        assert page.locator("#copy").get_attribute("data-copied") == "true"

        expect(page.locator("#row-loaded")).to_be_visible(timeout=5_000)
    assert not failures, failures


@pytest.mark.django_db(transaction=True)
def test_shell_and_components_do_not_overflow_at_key_widths(live_server):
    """The shell, footer and showcased components stay inside 360/768/1440."""
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page()
        watch(page, failures)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            for route in ("home", "firmwares:brand_list", "blog:post_list"):
                _prepare(page, live_server, reverse(route))
                page.evaluate(
                    "html => document.querySelector('main').insertAdjacentHTML("
                    "'beforeend', html)",
                    SHOWCASE,
                )
                dimensions = overflow(page)
                if dimensions["document"] > dimensions["viewport"] + 1:
                    failures.append(f"{route} at {width}px: {dimensions}")
            footer = page.locator(".site-footer").bounding_box()
            assert footer and footer["x"] >= 0
            assert footer["x"] + footer["width"] <= width + 1
    assert not failures, failures
