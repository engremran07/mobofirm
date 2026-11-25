"""Design unity: the public site and the staff console draw from one token set (Chromium via Playwright).

Part 1 - colours. For a representative URL set in both shells and both themes the computed colours of the key
elements (body, card/stat surface, primary button, status badge, link, form control, filter control) must come from the
shared palette (``frontend/src/shared/tokens/_palette.scss``). An element that is missing FAILS the test; nothing is skipped
silently. Nothing may overflow horizontally at 360/768/1280 and the console must stay free of console errors. Screenshots go
to ``$MOBOFIRM_SHOT_DIR`` when set (outside the repository), otherwise to the pytest temp directory.

Part 2 - primitives. Keyboard-only checks of the headless interaction primitives (menu, listbox select, switch, radio group,
combobox, native dialog focus trap and restore) and of the shared button / card / form-control look.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.utils import timezone
from playwright.sync_api import Page

from tests.backend import design_tokens as tokens
from tests.frontend.browser_support import (
    chromium,
    login_cookie,
    overflow,
    record_page_errors,
)
from tests.frontend.console_browser_support import (
    ConsoleServer,
    Probe,
    open_console,
    serialize_requests,
)

pytestmark = pytest.mark.django_db(transaction=True)

WIDTHS = (360, 768, 1280)
THEMES = ("light", "dark")
USERS = "/admin/users/customuser/"
ADD_POST = "/admin/blog/post/add/"

READ_COLOURS = """(selector) => {
    const el = document.querySelector(selector);
    if (!el) return null;
    const style = getComputedStyle(el);
    return {color: style.color, background: style.backgroundColor, border: style.borderTopColor, radius: parseFloat(style.borderTopLeftRadius)};
}"""


def _hex(value: str) -> str:
    channels = [int(part) for part in re.findall(r"\d+", value)[:3]]
    red, green, blue = channels
    return f"#{red:02x}{green:02x}{blue:02x}"


def _shots(tmp_path_factory) -> Path:
    target = os.environ.get("MOBOFIRM_SHOT_DIR")
    folder = Path(target) if target else tmp_path_factory.mktemp("design-unity")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _slug(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") or "root"


def _check(
    page: Page,
    label: str,
    selector: str,
    prop: str,
    allowed: set[str],
    failures: list[str],
) -> None:
    """Fail when ``selector`` is missing or its computed ``prop`` is not one of the token colours."""
    colours = page.evaluate(READ_COLOURS, selector)
    if colours is None:
        failures.append(f"{label}: expected element {selector!r} is missing")
        return
    if _hex(colours[prop]) not in allowed:
        failures.append(
            f"{label}: {selector} {prop} {_hex(colours[prop])} is not a token ({sorted(allowed)})"
        )


def _console_server(live_server, username: str) -> ConsoleServer:
    serialize_requests(live_server)
    model = get_user_model()
    user = model.objects.create_superuser(
        username=username,
        email=f"{username}@example.test",
        email_verified_at=timezone.now(),
    )
    model.objects.bulk_create(
        [
            model(
                username=f"{username}-row-{i}", email=f"{username}-row-{i}@example.test"
            )
            for i in range(3)
        ]
    )
    return ConsoleServer(live_server.url, user, login_cookie(user))


# -- Part 1: colours ----------------------------------------------------------------------------
def test_console_colours_come_from_the_token_set(live_server, tmp_path_factory):
    server = _console_server(live_server, "unity-admin")
    shots = _shots(tmp_path_factory)
    failures: list[str] = []
    with chromium() as browser:
        for theme in THEMES:
            palette = tokens.themes()[theme]
            surfaces = {
                palette["surface"],
                palette["surface-raised"],
                palette["page"],
                palette["surface-soft"],
            }
            accents = {palette["accent"], palette["accent-strong"]}
            links = accents | {
                palette["text"],
                palette["link-hover"],
                palette["text-muted"],
            }
            for width in WIDTHS:
                context = server.context(browser, width=width)
                context.add_init_script(
                    f"localStorage.setItem('mobofirm-theme', '{theme}')"
                )
                page = context.new_page()
                probe = Probe(origin=server.url)
                probe.attach(page)
                probe.begin()
                try:
                    for path, checks in (
                        (
                            "/admin/",
                            (
                                ("body", "background", {palette["page"]}),
                                (".c-stat", "background", surfaces),
                                (".app-content a:not(.btn)", "color", links),
                            ),
                        ),
                        (
                            USERS,
                            (
                                ("body", "color", {palette["text"]}),
                                (".c-fctl__btn", "background", surfaces),
                                (
                                    ".c-table",
                                    "color",
                                    {palette["text"], palette["text-muted"]},
                                ),
                                (".btn-primary", "background", accents),
                            ),
                        ),
                        (
                            ADD_POST,
                            (
                                ("body", "background", {palette["page"]}),
                                (".form-control", "background", surfaces),
                                (".btn-primary", "background", accents),
                            ),
                        ),
                    ):
                        label = f"console {path} {width}px {theme}"
                        open_console(page, probe, server, path, failures)
                        for selector, prop, allowed in checks:
                            _check(page, label, selector, prop, allowed, failures)
                        dimensions = overflow(page)
                        if dimensions["document"] > dimensions["viewport"] + 1:
                            failures.append(
                                f"{label}: horizontal overflow {dimensions}"
                            )
                        page.screenshot(
                            path=str(
                                shots / f"console-{_slug(path)}-{width}-{theme}.png"
                            ),
                            full_page=True,
                        )
                finally:
                    failures.extend(probe.end())
                    context.close()
    assert failures == []


def test_public_site_colours_come_from_the_same_token_set(
    live_server, tmp_path_factory
):
    shots = _shots(tmp_path_factory)
    failures: list[str] = []
    with chromium() as browser:
        for theme in THEMES:
            palette = tokens.themes()[theme]
            for width in WIDTHS:
                context = browser.new_context(viewport={"width": width, "height": 900})
                context.add_init_script(
                    f"localStorage.setItem('mobofirm-theme', '{theme}')"
                )
                page = context.new_page()
                errors: list[str] = []
                record_page_errors(page, errors)
                try:
                    for path, checks in (
                        (
                            "/",
                            (
                                ("body", "color", {palette["text"]}),
                                ("body", "background", {palette["page"]}),
                            ),
                        ),
                        (
                            "/accounts/login/",
                            (
                                ("body", "color", {palette["text"]}),
                                (
                                    ".btn-primary",
                                    "background",
                                    {palette["accent"], palette["accent-strong"]},
                                ),
                            ),
                        ),
                    ):
                        label = f"public {path} {width}px {theme}"
                        response = page.goto(
                            f"{live_server.url}{path}",
                            wait_until="load",
                            timeout=30_000,
                        )
                        if response is None or response.status != 200:
                            failures.append(
                                f"{label}: HTTP {response and response.status}"
                            )
                            continue
                        for selector, prop, allowed in checks:
                            _check(page, label, selector, prop, allowed, failures)
                        dimensions = overflow(page)
                        if dimensions["document"] > dimensions["viewport"] + 1:
                            failures.append(
                                f"{label}: horizontal overflow {dimensions}"
                            )
                        page.screenshot(
                            path=str(
                                shots / f"public-{_slug(path)}-{width}-{theme}.png"
                            ),
                            full_page=True,
                        )
                finally:
                    failures.extend(f"{path}: {error}" for error in errors)
                    context.close()
    assert failures == []


@pytest.mark.parametrize("theme", THEMES)
def test_both_shells_emit_identical_brand_tokens(live_server, theme):
    server = _console_server(live_server, "unity-brand")
    palette = tokens.themes()[theme]
    names = ("page", "text", "accent", "accent-strong", "danger", "success")
    read = (
        "() => Object.fromEntries(__NAMES__.map(n => ['--mf-' + n, getComputedStyle(document.documentElement).getPropertyValue('--mf-' + n).trim().toLowerCase()]))"
    ).replace("__NAMES__", repr(list(names)))
    seen: dict[str, dict[str, str]] = {}
    with chromium() as browser:
        public = browser.new_context(viewport={"width": 1280, "height": 900})
        public.add_init_script(f"localStorage.setItem('mobofirm-theme', '{theme}')")
        page = public.new_page()
        page.goto(f"{live_server.url}/accounts/login/", wait_until="load")
        seen["public"] = page.evaluate(read)
        public.close()
        context = server.context(browser, width=1280)
        context.add_init_script(f"localStorage.setItem('mobofirm-theme', '{theme}')")
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        probe.begin()
        open_console(page, probe, server, "/admin/", [])
        seen["console"] = page.evaluate(read)
        context.close()
    expected = {f"--mf-{name}": palette[name] for name in names}
    assert seen["public"] == expected
    assert seen["console"] == expected


# -- the admin 404 page --------------------------------------------------------------------------
@pytest.mark.parametrize("width", WIDTHS)
def test_admin_404_page_fits_the_viewport_and_has_no_inline_styles(live_server, width):
    server = _console_server(live_server, f"unity-404-{width}")
    errors: list[str] = []
    with chromium() as browser:
        context = server.context(browser, width=width)
        page = context.new_page()
        page.on(
            "console",
            lambda m: errors.append(m.text) if m.type in {"error", "warning"} else None,
        )
        response = page.goto(f"{server.url}/admin/does-not-exist/", wait_until="load")
        assert response is not None and response.status == 404
        assert page.locator("h1").count() == 1
        dimensions = overflow(page)
        assert dimensions["document"] <= dimensions["viewport"] + 1, dimensions
        assert page.locator("[style]").count() == 0, (
            "strict CSP: no inline style attributes"
        )
        context.close()
    assert [e for e in errors if "Content Security Policy" in e] == []


# -- Part 2: primitives, keyboard only ------------------------------------------------------------
def _inject(page: Page, html: str) -> None:
    page.evaluate(
        "(html) => document.querySelector('.app-content').insertAdjacentHTML('beforeend', html)",
        html,
    )


def test_menu_is_keyboard_operable(live_server):
    server = _console_server(live_server, "unity-menu")
    with chromium() as browser:
        context = server.context(browser, width=1280)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        probe.begin()
        problems: list[str] = []
        open_console(page, probe, server, USERS, problems)
        trigger = page.locator(
            "[data-ui-popover] > [data-ui-trigger][aria-haspopup='menu']"
        ).first
        trigger.focus()
        page.keyboard.press("ArrowDown")
        assert trigger.get_attribute("aria-expanded") == "true"
        assert (
            page.evaluate("() => document.activeElement.getAttribute('role')")
            == "menuitem"
        )
        page.keyboard.press("ArrowDown")
        assert page.locator("[data-ui-popover][data-open] [data-active]").count() == 1
        page.keyboard.press("End")
        page.keyboard.press("Home")
        page.keyboard.press("Escape")
        assert trigger.get_attribute("aria-expanded") == "false"
        assert page.evaluate(
            "() => document.activeElement === document.querySelector('[data-ui-trigger][aria-haspopup=menu]:focus')"
        )
        problems.extend(probe.end())
        context.close()
    assert problems == []


def test_switch_radio_group_and_select_are_keyboard_operable(live_server):
    server = _console_server(live_server, "unity-controls")
    switch = render_to_string(
        "console/components/switch.html",
        {"name": "notify", "id": "unity-switch", "label": "Notify me"},
    )
    radios = render_to_string(
        "console/components/radio_group.html",
        {
            "name": "density",
            "label": "Density",
            "value": "a",
            "options": [
                {"value": "a", "label": "One"},
                {"value": "b", "label": "Two"},
                {"value": "c", "label": "Three"},
            ],
        },
    )
    with chromium() as browser:
        context = server.context(browser, width=1280)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        probe.begin()
        problems: list[str] = []
        open_console(page, probe, server, USERS, problems)
        _inject(page, f"<div id='unity-lab'>{switch}{radios}</div>")

        toggle = page.locator("#unity-switch")
        toggle.focus()
        assert toggle.get_attribute("role") == "switch"
        assert toggle.get_attribute("data-focus") is not None
        page.keyboard.press("Space")
        assert toggle.is_checked() and toggle.get_attribute("data-checked") is not None
        page.keyboard.press("Space")
        assert not toggle.is_checked() and toggle.get_attribute("data-checked") is None

        group = page.locator("[data-ui-radiogroup]")
        first = group.locator("[role=radio]").nth(0)
        first.focus()
        assert first.get_attribute("tabindex") == "0"
        page.keyboard.press("ArrowRight")
        assert group.locator("[role=radio][data-selected]").inner_text() == "Two"
        assert group.locator("input[type=hidden]").input_value() == "b"
        page.keyboard.press("End")
        assert group.locator("[role=radio][aria-checked=true]").inner_text() == "Three"
        page.keyboard.press("Home")
        assert group.locator("[role=radio][aria-checked=true]").inner_text() == "One"
        assert group.locator("[role=radio][tabindex='0']").count() == 1

        # The bulk-action listbox (native select kept as the value) works from the keyboard too.
        page.locator("input[data-console-row-select]").nth(0).check()
        select = page.locator(".c-bulkbar [data-ui-select]")
        button = select.locator("[data-ui-trigger]")
        button.focus()
        page.keyboard.press("Enter")
        assert button.get_attribute("aria-expanded") == "true"
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        assert button.get_attribute("aria-expanded") == "false"
        assert select.locator("select").input_value() != ""
        assert page.locator(".c-bulkbar button[type=submit]").is_enabled()
        problems.extend(probe.end())
        context.close()
    assert problems == []


def test_native_dialog_traps_focus_and_restores_it(live_server):
    server = _console_server(live_server, "unity-dialog")
    dialog = render_to_string(
        "console/components/dialog_native.html",
        {
            "id": "unity-dialog",
            "title": "Sure?",
            "content": "<button type='button' id='in-one'>One</button><button type='button' id='in-two'>Two</button>",
        },
    )
    with chromium() as browser:
        context = server.context(browser, width=1280)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        probe.begin()
        problems: list[str] = []
        open_console(page, probe, server, USERS, problems)
        _inject(
            page,
            f"<button type='button' id='opener' data-ui-dialog-open='unity-dialog'>Open</button>{dialog}",
        )
        page.locator("#opener").focus()
        page.keyboard.press("Enter")
        assert page.locator("#unity-dialog[open][data-open]").count() == 1
        for _ in range(8):
            page.keyboard.press("Tab")
            assert page.evaluate(
                "() => document.activeElement === document.body || !!document.activeElement.closest('#unity-dialog')"
            ), "focus escaped the dialog"
        page.keyboard.press("Escape")
        assert page.locator("#unity-dialog[open]").count() == 0
        assert page.evaluate("() => document.activeElement.id") == "opener", (
            "focus returns to the opener"
        )
        problems.extend(probe.end())
        context.close()
    assert problems == []


def test_combobox_exposes_the_shared_data_attributes_from_the_keyboard(live_server):
    server = _console_server(live_server, "unity-combobox")
    with chromium() as browser:
        context = server.context(browser, width=1280)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        probe.begin()
        problems: list[str] = []
        open_console(page, probe, server, ADD_POST, problems)
        root = page.locator("[data-console-combobox]").first
        assert root.count() == 1, "the post form has a related-field combobox"
        field = root.locator("[data-console-combobox-input]")
        field.focus()
        page.keyboard.press("ArrowDown")
        page.wait_for_function(
            "(el) => el.hasAttribute('data-open')", arg=root.element_handle()
        )
        assert field.get_attribute("aria-expanded") == "true"
        page.keyboard.press("Escape")
        assert root.get_attribute("data-open") is None
        problems.extend(probe.end())
        context.close()
    assert problems == []


@pytest.mark.parametrize("theme", THEMES)
def test_shared_button_card_and_form_control_use_the_same_tokens(live_server, theme):
    server = _console_server(live_server, f"unity-shared-{theme}")
    palette = tokens.themes()[theme]
    with chromium() as browser:
        context = server.context(browser, width=1280)
        context.add_init_script(f"localStorage.setItem('mobofirm-theme', '{theme}')")
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        probe.begin()
        failures: list[str] = []
        open_console(page, probe, server, ADD_POST, failures)
        button = page.evaluate(READ_COLOURS, ".btn-primary")
        control = page.evaluate(READ_COLOURS, ".form-control")
        assert button and control, (
            "the post form renders a primary button and form controls"
        )
        assert tokens.contrast(_hex(button["color"]), _hex(button["background"])) >= 4.5
        assert (
            tokens.contrast(_hex(control["color"]), _hex(control["background"])) >= 4.5
        )
        assert _hex(control["background"]) in {
            palette["surface"],
            palette["surface-raised"],
            palette["page"],
        }
        assert control["radius"] > 0 and button["radius"] > 0
        # The shared status-tone mixin: badges and alerts take ink from the same status tokens.
        badge = page.evaluate(
            "() => { const el = document.createElement('span'); el.className = 'c-badge c-badge--danger'; document.body.append(el); const c = getComputedStyle(el).color; el.remove(); return c; }"
        )
        assert _hex(badge) == palette["danger"]
        failures.extend(probe.end())
        context.close()
    assert failures == []
