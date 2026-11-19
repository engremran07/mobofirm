"""Switch and radio-group adoption on real console forms (Chromium via Playwright).

Boolean options on the settings singleton pages and the list_editable boolean columns render as switches
(`role=switch` on the SAME native checkbox: same name, same submitted value, so no backend change), and
RadioSelect widgets render as a segmented radio group over the SAME native radios. These tests drive them
from the keyboard only and check what the server stores after submitting the form.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from playwright.sync_api import expect

from apps.consent.models import ConsentCategory
from apps.seo.models import SEOSettings
from tests.frontend.browser_support import chromium, login_cookie
from tests.frontend.console_browser_support import (
    ConsoleServer,
    Probe,
    open_console,
    serialize_requests,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _server(live_server) -> ConsoleServer:
    serialize_requests(live_server)
    user = get_user_model().objects.create_superuser(
        username="switch-admin",
        email="switch-admin@example.test",
        email_verified_at=timezone.now(),
    )
    return ConsoleServer(live_server.url, user, login_cookie(user))


def test_settings_page_boolean_is_a_keyboard_switch_that_saves_the_same_value(live_server):
    server = _server(live_server)
    settings = SEOSettings.get_solo()
    settings.sitemap_images_enabled = True
    settings.save()
    problems: list[str] = []
    with chromium() as browser:
        context = server.context(browser, width=1280)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        probe.begin()
        open_console(page, probe, server, "/admin/seo/seosettings/", problems)
        switch = page.locator("input[name=sitemap_images_enabled]")
        assert switch.get_attribute("type") == "checkbox"
        assert switch.get_attribute("role") == "switch"
        assert "c-switch-input" in (switch.get_attribute("class") or "")
        assert switch.is_checked() and switch.get_attribute("data-checked") is not None
        # The field sits in a tab panel: open that tab first, then use the keyboard on the focused switch.
        panel = switch.evaluate("(el) => el.closest('[role=tabpanel]')?.id")
        if panel:
            page.locator(f"#{panel}-tab").click()
        switch.focus()
        page.keyboard.press("Space")
        assert not switch.is_checked()
        assert switch.get_attribute("data-checked") is None
        with page.expect_navigation():
            page.locator("button[name=_save]").first.click()
        problems.extend(probe.end())
        context.close()
    assert problems == []
    assert SEOSettings.get_solo().sitemap_images_enabled is False


def test_list_editable_boolean_column_is_a_switch_and_posts_unchanged(live_server):
    server = _server(live_server)
    category = ConsentCategory.objects.create(slug="analytics-x", name="Analytics X", description="d", is_active=True)
    problems: list[str] = []
    with chromium() as browser:
        context = server.context(browser, width=1280)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        probe.begin()
        open_console(page, probe, server, "/admin/consent/consentcategory/", problems)
        cell = page.locator("tr", has_text="Analytics X").locator("input[type=checkbox][name$='-is_active']")
        expect(cell).to_have_attribute("role", "switch")
        assert cell.is_checked()
        cell.focus()
        page.keyboard.press("Space")
        assert not cell.is_checked()
        with page.expect_navigation():
            page.locator("button[name=_save]").first.click()
        problems.extend(probe.end())
        context.close()
    assert problems == []
    category.refresh_from_db()
    assert category.is_active is False


def test_radio_select_widget_is_a_segmented_group_over_native_radios(live_server):
    from django import forms
    from django.template.loader import render_to_string

    widget = forms.RadioSelect(choices=[("a", "Alpha"), ("b", "Beta")])
    html = widget.render("mode", "b", attrs={"id": "id_mode"})
    # Render through the console widget template exactly as the change form does.
    widget.template_name = "console/widgets/radio_select.html"
    html = widget.render("mode", "b", attrs={"id": "id_mode"})
    assert 'role="radiogroup"' in html and "data-ui-radiogroup" in html
    assert html.count('type="radio"') == 2 and 'name="mode"' in html
    assert 'value="a"' in html and 'value="b"' in html and html.count("checked") == 1
    assert render_to_string("console/components/radio_group.html", {"name": "m", "label": "M", "value": "a", "options": [{"value": "a", "label": "A"}]})

    server = _server(live_server)
    problems: list[str] = []
    with chromium() as browser:
        context = server.context(browser, width=1280)
        page = context.new_page()
        probe = Probe(origin=server.url)
        probe.attach(page)
        probe.begin()
        open_console(page, probe, server, "/admin/", problems)
        page.evaluate("(html) => document.querySelector('.app-content').insertAdjacentHTML('beforeend', '<form id=\"lab\">' + html + '</form>')", html)
        group = page.locator("#lab [data-ui-radiogroup]")
        assert group.locator("label[data-selected]").inner_text() == "Beta"
        group.locator("input[value=b]").focus()
        page.keyboard.press("ArrowLeft")
        assert group.locator("input[value=a]").is_checked()
        assert group.locator("label[data-selected]").inner_text() == "Alpha"
        assert page.evaluate("() => new FormData(document.querySelector('#lab')).get('mode')") == "a"
        problems.extend(probe.end())
        context.close()
    assert problems == []
