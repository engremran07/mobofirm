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

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.utils import timezone
from playwright.sync_api import BrowserContext, Page, expect

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
        page.locator("[data-console-sidebar-overlay]").click(position={"x": 350, "y": 400})
        expect(html).to_have_attribute("data-console-sidebar", "closed")
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
    original = model_admin.list_per_page
    model_admin.list_per_page = 3
    yield
    model_admin.list_per_page = original


@pytest.mark.usefixtures("small_pages")
def test_changelist_search_filter_sort_and_pagination_never_reload_the_page(
    live_server,
):
    server = _server(live_server)
    names = ["Alpha tag", "Bravo tag", "Charlie tag", "Delta tag", "Echo tag", "Fox tag"]
    for name in names:
        _tag(name, is_curated=name.startswith(("A", "B")))
    hx_requests: list[str] = []
    with _session(server) as (page, probe, problems, _context):
        page.on(
            "request",
            lambda request: hx_requests.append(request.url)
            if request.headers.get("hx-request") == "true"
            else None,
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
    trigger = page.locator("[data-ui-popover]", has=item).locator("[data-ui-trigger]").first
    return trigger, item


def test_row_action_dialog_can_be_cancelled_then_confirmed(live_server):
    server = _server(live_server)
    tag = _tag("Dialog subject", ai_suggested=True, is_curated=False)
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


def test_dialog_traps_focus_closes_on_escape_and_returns_focus(live_server):
    server = _server(live_server)
    tag = _tag("Focus subject", ai_suggested=True, is_curated=False)
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
        expect(page.locator("#id_name")).to_have_value(
            "Guarded tag, edited"
        )

        page.locator("[data-console-sidebar] a[href='/admin/']").first.click()
        page.locator(HOOKS["confirm_ok"]).click()
        page.wait_for_url(re.compile(r"/admin/$"))
        assert probe.dialogs == [], "a native browser dialog was used"
    _assert_clean(problems)
    assert Tag.objects.get(pk=tag.pk).name == "Guarded tag"
