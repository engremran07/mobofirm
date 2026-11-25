"""Browser interactions of the staff console, driven through the published hooks.

Every test starts a signed-in superuser session on the live server, collects console,
CSP, network and native-dialog problems for the whole run, and fails if any occurred.
Database rows are created before the browser starts and asserted after it closes
(Playwright's event loop forbids ORM calls while it runs).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.utils import timezone
from playwright.sync_api import BrowserContext, Page, expect

from apps.ads.models import AdPlacement, Campaign
from apps.firmwares.models import ChangelogEntry
from apps.tags.models import Tag
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


def _server(live_server) -> ConsoleServer:
    serialize_requests(live_server)
    user = get_user_model().objects.create_superuser(
        username="console-interactions",
        email="console-interactions@example.test",
        email_verified_at=timezone.now(),
    )
    return ConsoleServer(live_server.url, user, login_cookie(user))


@contextmanager
def _session(
    server: ConsoleServer, *, width: int = 1365
) -> Iterator[tuple[Page, Probe, list[str], BrowserContext]]:
    """A page whose console/network/dialog problems are collected into ``problems``."""
    problems: list[str] = []
    with chromium() as browser:
        context = server.context(browser, width=width)
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


def _tag(name: str, **extra) -> Tag:
    return Tag.objects.create(name=name, **extra)


# -- sidebar ---------------------------------------------------------------------------
def test_sidebar_collapses_to_a_rail_and_the_choice_survives_a_reload(live_server):
    server = _server(live_server)
    with _session(server) as (page, _probe, problems, _context):
        open_console(page, _probe, server, "/admin/", problems)
        html = page.locator("html")
        toggle = visible_first(page, HOOKS["sidebar_toggle"])

        expect(html).to_have_attribute("data-console-sidebar", "expanded")
        toggle.click()
        expect(html).to_have_attribute("data-console-sidebar", "mini")
        expect(page.locator("body")).to_have_class(re.compile(r"\bsidebar-collapse\b"))
        expect(toggle).to_have_attribute("aria-expanded", "false")

        page.reload(wait_until="load")
        expect(html).to_have_attribute("data-console-sidebar", "mini")
        visible_first(page, HOOKS["sidebar_toggle"]).click()
        expect(html).to_have_attribute("data-console-sidebar", "expanded")
    _assert_clean(problems)


def test_sidebar_is_an_off_canvas_drawer_on_a_phone(live_server):
    server = _server(live_server)
    with _session(server, width=390) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/", problems)
        html = page.locator("html")
        toggle = visible_first(page, HOOKS["sidebar_toggle"])

        expect(html).to_have_attribute("data-console-sidebar", "closed")
        toggle.click()
        expect(html).to_have_attribute("data-console-sidebar", "open")
        assert page.locator(HOOKS["sidebar"]).is_visible()
        page.keyboard.press("Escape")
        expect(html).to_have_attribute("data-console-sidebar", "closed")
        expect(toggle).to_be_focused()

        toggle.click()
        page.locator("[data-console-sidebar-overlay]").click(
            position={"x": 350, "y": 400}
        )
        expect(html).to_have_attribute("data-console-sidebar", "closed")
    _assert_clean(problems)


def test_ads_dashboard_hides_mutations_from_staff_without_model_permissions(
    live_server,
):
    """Hide ad mutations and explain the missing permission to read-only staff."""
    viewer = get_user_model().objects.create_user(
        username="ads-dashboard-viewer",
        email="ads-dashboard-viewer@example.test",
        email_verified_at=timezone.now(),
        is_staff=True,
    )
    AdPlacement.objects.create(
        name="Viewer placement",
        code="viewer-placement",
        slug="viewer-placement",
    )
    Campaign.objects.create(name="Viewer campaign")
    server = ConsoleServer(live_server.url, viewer, login_cookie(viewer))

    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/ads/dashboard/", problems)
        expect(page.get_by_role("button", name="Save serving settings")).to_have_count(
            0
        )
        expect(page.get_by_role("button", name="Disable")).to_have_count(0)
        expect(page.get_by_role("button", name="Pause")).to_have_count(0)
        expect(page.get_by_role("link", name="New campaign")).to_have_count(0)
        expect(
            page.get_by_text(
                "You need the change Ads settings permission to update serving controls."
            )
        ).to_be_visible()
    _assert_clean(problems)


def test_account_menu_exposes_personal_links_and_confirms_logout(live_server):
    """Keep account links within the viewport and confirm the logout action."""
    server = _server(live_server)
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/", problems)
        menu = page.locator(".app-header [data-ui-popover]")
        trigger = menu.locator("[data-ui-trigger]")
        trigger.click()
        panel = page.locator("[data-ui-panel][data-ui-open][role='menu']")
        expect(panel).to_be_visible()
        trigger_box = trigger.bounding_box()
        panel_box = panel.bounding_box()
        viewport_size = page.viewport_size
        assert (
            trigger_box is not None
            and panel_box is not None
            and viewport_size is not None
        )
        assert panel_box["x"] >= 8
        assert panel_box["x"] + panel_box["width"] <= viewport_size["width"] - 8
        assert 0 <= panel_box["y"] - (trigger_box["y"] + trigger_box["height"]) <= 12
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= window.innerWidth"
        )

        for label in (
            "My dashboard",
            "My profile",
            "My wallet",
            "My contributions",
            "Notifications",
        ):
            expect(panel.get_by_role("menuitem", name=label)).to_be_visible()

        sign_out = panel.get_by_role("menuitem", name="Sign out")
        expect(sign_out).to_have_attribute(
            "data-console-confirm", "Sign out of your account?"
        )
        sign_out.click()
        confirmation = page.locator("[data-console-confirm-dialog]")
        expect(confirmation).to_be_visible()
        expect(confirmation.get_by_role("heading", name="Sign out?")).to_be_visible()
        confirmation.get_by_role("button", name="Cancel").click()
        expect(confirmation).to_have_count(0)
        assert menu.get_attribute("data-open") is None
        expect(menu.locator("[data-ui-trigger]")).to_be_visible()
    _assert_clean(problems)


# -- command palette -------------------------------------------------------------------
def test_command_palette_opens_with_ctrl_k_searches_and_jumps(live_server):
    server = _server(live_server)
    _tag("Palette findable tag")
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/", problems)
        palette = page.locator("#console-palette")

        page.keyboard.press("Control+k")
        expect(palette).to_be_visible()
        search = palette.get_by_role("searchbox")
        expect(search).to_be_focused()

        search.fill("tags")
        results = palette.locator("#console-palette-results")
        expect(results.get_by_role("link").first).to_be_visible()
        search.press("Escape")
        expect(palette).to_be_hidden()

        page.keyboard.press("Control+k")
        palette.get_by_role("searchbox").fill("Palette findable")
        expect(
            palette.locator("#console-palette-results").get_by_text(
                "Palette findable tag"
            )
        ).to_be_visible()
        palette.locator("#console-palette-results").get_by_role("link").first.click()
        page.wait_for_url(re.compile(r"/admin/tags/tag/\d+/change/"))
    _assert_clean(problems)


# -- changelist over HTMX --------------------------------------------------------------
@pytest.fixture(name="small_pages")
def small_pages_fixture():
    """Show 3 rows per page so a handful of rows exercises pagination."""
    model_admin = admin.site.get_model_admin(Tag)
    with patch.object(model_admin, "list_per_page", 3):
        yield


@pytest.mark.usefixtures("small_pages")
def test_changelist_search_filter_sort_and_pagination_never_reload_the_page(
    live_server,
):
    server = _server(live_server)
    names = [
        "Alpha tag",
        "Bravo tag",
        "Charlie tag",
        "Delta tag",
        "Echo tag",
        "Fox tag",
    ]
    for name in names:
        _tag(name, is_curated=name.startswith(("A", "B")))
    hx_requests: list[str] = []
    with _session(server) as (page, probe, problems, _context):
        page.on(
            "request",
            lambda request: (
                hx_requests.append(request.url)
                if request.headers.get("hx-request") == "true"
                else None
            ),
        )
        open_console(page, probe, server, "/admin/tags/tag/", problems)
        mark_no_reload(page)
        rows = page.locator("#result_list tbody tr")
        expect(rows).to_have_count(3)

        # Pagination swaps the results region and pushes the URL.
        page.get_by_role("link", name="Page 2").click()
        expect(page).to_have_url(re.compile(r"[?&]p=2"))
        expect(rows).to_have_count(3)
        assert not reloaded(page)

        # Search: debounced, narrows the rows and the status line, and is undoable.
        search = page.locator("#c-cl-q")
        search.fill("Echo")
        expect(page.locator("#c-cl-status")).to_contain_text("1 result")
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text("Echo tag")
        page.get_by_role("button", name="Clear search").click()
        expect(page.locator("#c-cl-status")).to_contain_text("6 results")
        assert not reloaded(page)

        # Sorting by a column header re-renders in place and marks the header.
        sort = page.locator("#result_list thead a.c-table__sort").first
        sort.click()
        expect(page.locator("#result_list thead th[aria-sort]").first).to_be_visible()
        assert not reloaded(page)

        # A filter chip appears (and can be removed) without a reload.
        page.goto(f"{server.url}/admin/tags/tag/?is_curated__exact=1")
        page.wait_for_load_state("networkidle")
        mark_no_reload(page)
        expect(page.locator("#c-cl-chips .c-chip").first).to_be_visible()
        page.locator("#c-cl-chips a.c-chip__remove").first.click()
        expect(page.locator("#c-cl-status")).to_contain_text("6 results")
        assert not reloaded(page)
    assert hx_requests, "no HX-Request was sent: the changelist reloaded the page"
    _assert_clean(problems)


def test_row_selection_shows_the_bulk_bar_with_a_live_count(live_server):
    server = _server(live_server)
    for name in ("Select one", "Select two", "Select three"):
        _tag(name)
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/tags/tag/", problems)
        bar = page.locator(HOOKS["bulkbar"])
        expect(bar).to_be_hidden()

        page.locator(HOOKS["row_select"]).nth(0).check()
        page.locator(HOOKS["row_select"]).nth(1).check()
        expect(bar).to_be_visible()
        expect(page.locator("[data-console-selected-count]").first).to_have_text("2")

        page.locator(HOOKS["select_all"]).check()
        expect(page.locator("[data-console-selected-count]").first).to_have_text("3")
        page.locator(HOOKS["select_all"]).uncheck()
        expect(bar).to_be_hidden()
    _assert_clean(problems)


# -- dialogs: row action and bulk delete ----------------------------------------------------
def _row_menu(page, pk, action):
    """The row's action-menu button and one of its items (row actions live in a menu, closed until opened)."""
    item = page.locator(f"a[hx-get$='/{pk}/{action}/']").first
    trigger_name = item.evaluate(
        "(item) => item.closest('tr').querySelector('[data-ui-trigger]').getAttribute('aria-label')"
    )
    trigger = page.get_by_role("button", name=trigger_name, exact=True)
    return trigger, item


def test_row_action_dialog_can_be_cancelled_then_confirmed(live_server):
    server = _server(live_server)
    tag = _tag("Dialog subject", is_suggested=True, is_curated=False)
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/tags/tag/", problems)
        dialog = page.locator(HOOKS["dialog_host"])
        trigger, opener = _row_menu(page, tag.pk, "approve")

        trigger.click()
        opener.click()
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role("heading").first).to_be_visible()
        dialog.get_by_role("button", name="Cancel").click()
        expect(dialog).to_be_hidden()

        trigger.click()
        opener.click()
        expect(dialog).to_be_visible()
        dialog.locator("button[type=submit]").click()
        expect(dialog).to_be_hidden()
        expect(page.locator("#c-cl-results")).to_be_visible()
    _assert_clean(problems)
    tag.refresh_from_db()
    assert tag.is_curated


def test_row_dropdown_is_clamped_inside_a_short_mobile_viewport(live_server):
    server = _server(live_server)
    for index in range(24):
        _tag(f"Viewport menu filler {index:02d}")
    tag = _tag("ZZ viewport menu", is_suggested=True, is_curated=False)
    with _session(server, width=390) as (page, probe, problems, _context):
        page.set_viewport_size({"width": 390, "height": 420})
        open_console(page, probe, server, "/admin/tags/tag/", problems)
        row = page.locator("#result_list tbody tr").filter(has_text=tag.name)
        expect(row).to_be_visible()
        page.evaluate(
            """row => window.scrollTo(
              0,
              row.getBoundingClientRect().top + window.scrollY - window.innerHeight + 20
            )""",
            row.element_handle(),
        )
        trigger, _item = _row_menu(page, tag.pk, "approve")
        trigger.click()
        panel = page.locator("[data-ui-panel][data-ui-open]")
        expect(panel).to_be_visible()
        trigger_box = trigger.bounding_box()
        panel_box = panel.bounding_box()
        assert trigger_box is not None and panel_box is not None
        below_gap = abs(panel_box["y"] - (trigger_box["y"] + trigger_box["height"]))
        above_gap = abs(panel_box["y"] + panel_box["height"] - trigger_box["y"])
        assert min(below_gap, above_gap) <= 12, (
            f"trigger={trigger_box}, panel={panel_box}, "
            f"inline_style={panel.get_attribute('style')}, "
            f"scroll_y={page.evaluate('window.scrollY')}"
        )

        box = panel.bounding_box()
        assert box is not None
        assert box["x"] >= 8
        assert box["y"] >= 8
        assert box["x"] + box["width"] <= 390 - 8
        assert box["y"] + box["height"] <= 420 - 8
    _assert_clean(problems)


def test_fixed_listbox_stays_anchored_inside_a_transformed_container(live_server):
    server = _server(live_server)
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/", problems)
        page.evaluate(
            """() => {
              const host = document.createElement("div");
              host.className = "c-card";
              host.style.transform = "translateX(96px)";
              const root = document.createElement("div");
              root.className = "c-select";
              root.dataset.uiSelect = "";
              root.dataset.uiPopover = "";
              const select = document.createElement("select");
              const option = new Option("Second choice", "second");
              select.add(new Option("First choice", "first"));
              select.add(option);
              const trigger = document.createElement("button");
              trigger.type = "button";
              trigger.className = "c-select__btn";
              trigger.dataset.uiTrigger = "";
              trigger.setAttribute("aria-haspopup", "listbox");
              trigger.setAttribute("aria-expanded", "false");
              const label = document.createElement("span");
              label.dataset.uiSelectValue = "";
              label.textContent = "First choice";
              trigger.append(label);
              const panel = document.createElement("div");
              panel.className = "c-fpanel";
              panel.dataset.uiPanel = "";
              panel.dataset.uiFixed = "";
              panel.id = "transformed-listbox";
              panel.hidden = true;
              const list = document.createElement("ul");
              list.setAttribute("role", "listbox");
              for (const item of [["first", "First choice"], ["second", "Second choice"]]) {
                const row = document.createElement("li");
                row.setAttribute("role", "presentation");
                const choice = document.createElement("button");
                choice.type = "button";
                choice.setAttribute("role", "option");
                choice.dataset.value = item[0];
                choice.textContent = item[1];
                row.append(choice);
                list.append(row);
              }
              panel.append(list);
              root.append(select, trigger, panel);
              host.append(root);
              document.querySelector("#console-main").prepend(host);
            }"""
        )
        trigger = page.locator(".c-select__btn").last
        trigger.click()
        panel = page.locator("#transformed-listbox")
        expect(panel).to_be_visible()
        trigger_box = trigger.bounding_box()
        panel_box = panel.bounding_box()
        assert trigger_box is not None and panel_box is not None
        assert abs(panel_box["x"] - trigger_box["x"]) <= 8
        assert abs(panel_box["y"] - (trigger_box["y"] + trigger_box["height"])) <= 12
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= window.innerWidth"
        )

        panel.get_by_role("option", name="Second choice").click()
        expect(trigger).to_contain_text("Second choice")
        expect(page.locator(".c-select select").last).to_have_value("second")
        expect(panel).to_be_hidden()
    _assert_clean(problems)


def test_dialog_traps_focus_closes_on_escape_and_returns_focus(live_server):
    server = _server(live_server)
    tag = _tag("Focus subject", is_suggested=True, is_curated=False)
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/tags/tag/", problems)
        dialog = page.locator(HOOKS["dialog_host"])
        trigger, opener = _row_menu(page, tag.pk, "approve")
        # The row action lives in the row's menu: open it from the keyboard, move to the action, press Enter.
        trigger.focus()
        trigger.press("ArrowDown")
        expect(trigger).to_have_attribute("aria-expanded", "true")
        opener.focus()
        opener.press("Enter")
        expect(dialog).to_be_visible()

        for _ in range(8):
            page.keyboard.press("Tab")
            assert page.evaluate(
                "() => document.querySelector('#console-dialog').contains(document.activeElement)"
            ), "focus escaped the open dialog"
        for _ in range(8):
            page.keyboard.press("Shift+Tab")
            assert page.evaluate(
                "() => document.querySelector('#console-dialog').contains(document.activeElement)"
            ), "focus escaped the open dialog (backwards)"

        page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        # The menu closed with the action, so focus returns to the menu button that opened it.
        expect(trigger).to_be_focused()
    _assert_clean(problems)
    tag.refresh_from_db()
    assert not tag.is_curated


def test_bulk_delete_asks_first_and_only_deletes_after_confirmation(live_server):
    server = _server(live_server)
    keep = _tag("Bulk keeper")
    doomed = [_tag("Bulk doomed one"), _tag("Bulk doomed two")]
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/tags/tag/", problems)
        for tag in doomed:
            page.locator(f"input[data-console-row-select][value='{tag.pk}']").check()
        page.locator("select[name=action]").first.select_option("delete_selected")
        page.get_by_role("button", name="Run").first.click()

        # The confirmation page lists what goes; backing out deletes nothing.
        expect(page.get_by_text("This cannot be undone.")).to_be_visible()
        page.get_by_role("link", name="No, take me back").click()
        page.wait_for_url(re.compile(r"/admin/tags/tag/$"))
    # Browser closed: nothing may have been deleted by cancelling.
    assert Tag.objects.filter(pk__in=[t.pk for t in doomed]).count() == len(doomed)

    with _session(server) as (page, probe, problems2, _context):
        open_console(page, probe, server, "/admin/tags/tag/", problems2)
        for tag in doomed:
            page.locator(f"input[data-console-row-select][value='{tag.pk}']").check()
        page.locator("select[name=action]").first.select_option("delete_selected")
        page.get_by_role("button", name="Run").first.click()
        page.get_by_role("button", name="Yes, I’m sure").click()
        page.wait_for_url(re.compile(r"/admin/tags/tag/$"))
        expect(page.locator(HOOKS["toast"]).first).to_be_visible()
    _assert_clean([*problems, *problems2])
    assert Tag.objects.filter(pk=keep.pk).exists()
    assert not Tag.objects.filter(pk__in=[t.pk for t in doomed]).exists()


# -- add / change / delete round trip -------------------------------------------------------
def test_add_change_and_delete_round_trip_through_the_forms(live_server):
    server = _server(live_server)
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, "/admin/tags/tag/add/", problems)
        page.locator("#id_name").fill("Round trip tag")
        page.get_by_role("button", name="Save", exact=True).click()
        page.wait_for_url(re.compile(r"/admin/tags/tag/$"))
        expect(page.locator(HOOKS["toast"]).first).to_be_visible()
    created = Tag.objects.get(name="Round trip tag")

    with _session(server) as (page, probe, problems2, _context):
        open_console(
            page, probe, server, f"/admin/tags/tag/{created.pk}/change/", problems2
        )
        page.locator("#id_name").fill("Round trip tag renamed")
        page.get_by_role("button", name="Save", exact=True).click()
        page.wait_for_url(re.compile(r"/admin/tags/tag/$"))
    assert Tag.objects.get(pk=created.pk).name == "Round trip tag renamed"

    with _session(server) as (page, probe, problems3, _context):
        open_console(
            page, probe, server, f"/admin/tags/tag/{created.pk}/delete/", problems3
        )
        page.get_by_role("link", name="No, take me back").click()
        page.wait_for_load_state("load")
    assert Tag.objects.filter(pk=created.pk).exists()

    with _session(server) as (page, probe, problems4, _context):
        open_console(
            page, probe, server, f"/admin/tags/tag/{created.pk}/delete/", problems4
        )
        page.get_by_role("button", name="Yes, I’m sure").click()
        page.wait_for_url(re.compile(r"/admin/tags/tag/$"))
    _assert_clean([*problems, *problems2, *problems3, *problems4])
    assert not Tag.objects.filter(pk=created.pk).exists()


# -- inline formset ----------------------------------------------------------------------
def test_inline_formset_adds_and_removes_rows_and_saves_the_result(live_server):
    server = _server(live_server)
    entry = ChangelogEntry.objects.create(
        version="9.9.9", title="Formset entry", release_date=timezone.localdate()
    )
    with _session(server) as (page, probe, problems, _context):
        open_console(
            page,
            probe,
            server,
            f"/admin/firmwares/changelogentry/{entry.pk}/change/",
            problems,
        )
        formset = page.locator(HOOKS["formset"]).first
        total = formset.locator("input[name$='-TOTAL_FORMS']")
        rows = formset.locator("[data-console-formset-row], .console-inline__row")
        before = int(total.input_value())

        formset.locator(HOOKS["formset_add"]).first.click()
        expect(total).to_have_value(str(before + 1))
        formset.locator(HOOKS["formset_add"]).first.click()
        expect(total).to_have_value(str(before + 2))
        added = rows.count()

        formset.locator(HOOKS["formset_remove"]).last.click()
        expect(total).to_have_value(str(before + 1))
        assert rows.count() == added - 1

        row = rows.last
        row.locator("textarea, input[type=text]").first.fill("A new release note")
        row.locator("select").first.select_option("feature")
        page.get_by_role("button", name="Save", exact=True).first.click()
        page.wait_for_url(re.compile(r"/admin/firmwares/changelogentry/$"))
    _assert_clean(problems)
    assert list(entry.release_notes.values_list("description", flat=True)) == [
        "A new release note"
    ]


# -- unsaved-changes guard -----------------------------------------------------------------
def test_leaving_a_dirty_form_asks_in_an_app_dialog_not_a_browser_one(live_server):
    server = _server(live_server)
    tag = _tag("Guarded tag")
    with _session(server) as (page, probe, problems, _context):
        open_console(page, probe, server, f"/admin/tags/tag/{tag.pk}/change/", problems)
        assert page.locator(HOOKS["unsaved_form"]).count() == 1

        # A pristine form lets you leave without any question.
        page.locator("[data-console-sidebar] a[href='/admin/']").first.click()
        page.wait_for_url(re.compile(r"/admin/$"))

        page.goto(f"{server.url}/admin/tags/tag/{tag.pk}/change/", wait_until="load")
        page.locator("#id_name").fill("Guarded tag, edited")
        page.locator("[data-console-sidebar] a[href='/admin/']").first.click()
        confirm = page.locator(HOOKS["confirm_dialog"])
        expect(confirm).to_be_visible()
        expect(confirm.get_by_role("heading")).to_contain_text("unsaved")

        confirm.get_by_role("button", name="Keep editing").click()
        expect(confirm).to_be_hidden()
        assert page.url.endswith(f"/admin/tags/tag/{tag.pk}/change/")
        expect(page.locator("#id_name")).to_have_value("Guarded tag, edited")

        page.locator("[data-console-sidebar] a[href='/admin/']").first.click()
        page.locator(HOOKS["confirm_ok"]).click()
        page.wait_for_url(re.compile(r"/admin/$"))
        assert probe.dialogs == [], "a native browser dialog was used"
    _assert_clean(problems)
    assert Tag.objects.get(pk=tag.pk).name == "Guarded tag"
