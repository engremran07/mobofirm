"""Browser checks for the rich-text editor: lazy loading, tools, CSP, themes, layout."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from playwright.sync_api import Browser, BrowserContext, Page, expect

from apps.blog.models import Post, PostStatus
from apps.site_settings.editor_models import EditorProfile
from tests.frontend.browser_support import chromium, login_cookie, overflow, watch

PROFILE = "blog_post"


def _prepare() -> tuple[dict[str, str], int]:
    """Seed profiles, create a superuser and return its cookie and a profile pk."""
    EditorProfile.seed()
    user = get_user_model().objects.create_superuser(
        username="editor-admin",
        email="editor-admin@example.test",
        email_verified_at=timezone.now(),
    )
    return login_cookie(user), EditorProfile.objects.get(context=PROFILE).pk


def _context(
    browser: Browser,
    live_server,
    cookies: dict[str, str],
    *,
    width: int = 1365,
    scheme: str = "light",
) -> BrowserContext:
    context = browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme
    )
    context.add_cookies(
        [
            {"name": name, "value": value, "url": live_server.url}
            for name, value in cookies.items()
        ]
    )
    return context


def _open_profile(page: Page, live_server, pk: int) -> None:
    url = f"{live_server.url}/admin/site_settings/editorprofile/{pk}/change/"
    response = page.goto(url, wait_until="load", timeout=20_000)
    assert response is not None and response.status == 200
    # The editor mounts lazily, once its field is (nearly) on screen.
    page.locator(".mf-editor-preview").scroll_into_view_if_needed()
    page.wait_for_selector(".note-editor", timeout=15_000)


def _button(page: Page, label: str):
    return page.locator(f".note-editor .note-toolbar button[aria-label^='{label}']")


@pytest.mark.django_db(transaction=True)
def test_editor_loads_lazily_only_where_an_editor_exists(live_server) -> None:
    """Pages without an editor never request the heavy bundle; jQuery stays local."""
    cookies, pk = _prepare()
    failures: list[str] = []
    with chromium() as browser:
        page = _context(browser, live_server, cookies).new_page()
        watch(page, failures)
        requested: list[str] = []
        page.on("request", lambda request: requested.append(request.url))

        page.goto(f"{live_server.url}/admin/", wait_until="load", timeout=20_000)
        assert not [url for url in requested if "editor" in url], requested
        assert page.locator(".note-editor").count() == 0

        _open_profile(page, live_server, pk)
        assert any(url.endswith("/editor.js") for url in requested)
        assert any(url.endswith("/editor-core.js") for url in requested)
        assert any(url.endswith("/editor-core.css") for url in requested)
        assert page.evaluate("typeof window.jQuery + typeof window.$") == (
            "undefinedundefined"
        )
    assert not failures, failures


@pytest.mark.django_db(transaction=True)
def test_formatting_links_and_tables_work_under_the_admin_csp(live_server) -> None:
    """Bold, link and table tools produce markup the server vocabulary accepts."""
    cookies, pk = _prepare()
    failures: list[str] = []
    with chromium() as browser:
        page = _context(browser, live_server, cookies).new_page()
        watch(page, failures)
        _open_profile(page, live_server, pk)
        preview = page.locator(".mf-editor-preview")
        editable = preview.locator(".note-editable")
        textarea = preview.locator("textarea[name^='preview-']")
        editable.click()
        editable.press("Control+A")
        editable.type("Format me")
        editable.press("Enter")
        editable.type("Link me")
        editable.press("Enter")
        preview.get_by_role("button", name="Table").first.click()
        page.locator(".note-dimension-picker-mousecatcher").first.click(
            position={"x": 30, "y": 30}
        )
        page.wait_for_timeout(200)
        assert editable.locator("table.mf-table").count() == 1, editable.inner_html()

        editable.locator("p", has_text="Format me").click(click_count=3)
        _button(page, "Bold").first.click()
        assert editable.locator("b, strong").count() == 1
        assert "Format me</b>" in textarea.input_value() or (
            "Format me</strong>" in textarea.input_value()
        )

        editable.locator("p", has_text="Link me").click(click_count=3)
        preview.get_by_role("button", name="Link").first.click()
        dialog = page.locator(".note-modal.open")
        expect(dialog).to_be_visible()
        dialog.locator(".note-link-url").fill("https://example.com/guide")
        dialog.locator(".note-link-btn").click()
        assert editable.locator("a[href='https://example.com/guide']").count() == 1
        value = textarea.input_value()
        assert "https://example.com/guide" in value
        assert "mf-table" in value
        assert "style=" not in value
    assert not failures, failures


@pytest.mark.django_db(transaction=True)
def test_colour_font_and_alignment_use_classes_not_inline_styles(live_server) -> None:
    """The custom buttons write curated classes, never style attributes."""
    cookies, pk = _prepare()
    failures: list[str] = []
    with chromium() as browser:
        page = _context(browser, live_server, cookies).new_page()
        watch(page, failures)
        _open_profile(page, live_server, pk)
        preview = page.locator(".mf-editor-preview")
        editable = preview.locator(".note-editable")
        textarea = preview.locator("textarea[name^='preview-']")
        editable.click()
        editable.press("Control+A")
        editable.type("Colourful")
        editable.press("Control+A")

        preview.get_by_role("button", name="Colours").click()
        preview.locator("button[aria-label='Text red']").click()
        editable.press("Control+A")
        preview.get_by_role("button", name="Alignment").click()
        preview.get_by_role("menuitem", name="Center").click()
        value = textarea.input_value()
        assert "mf-fg-red" in value
        assert "mf-align-center" in value
        assert "style=" not in value
    assert not failures, failures


@pytest.mark.django_db(transaction=True)
def test_disabling_a_feature_in_the_admin_removes_its_tool(live_server) -> None:
    """Unchecking Tables and saving removes the table button from the editor."""
    cookies, pk = _prepare()
    failures: list[str] = []
    with chromium() as browser:
        page = _context(browser, live_server, cookies).new_page()
        watch(page, failures)
        _open_profile(page, live_server, pk)
        assert _button(page, "Table").count() >= 1
        page.locator("input[name='features'][value='tables']").uncheck()
        page.locator("button[name='_continue']").first.click()
        page.locator(".mf-editor-preview").scroll_into_view_if_needed()
        page.wait_for_selector(".note-editor", timeout=15_000)
        assert _button(page, "Table").count() == 0
        assert _button(page, "Bold").count() >= 1
    assert not failures, failures


@pytest.mark.django_db(transaction=True)
def test_master_switch_off_leaves_a_plain_textarea(live_server) -> None:
    """A disabled profile renders no editor and no editor requests."""
    cookies, pk = _prepare()
    EditorProfile.objects.filter(pk=pk).update(enabled=False)
    failures: list[str] = []
    with chromium() as browser:
        page = _context(browser, live_server, cookies).new_page()
        watch(page, failures)
        requested: list[str] = []
        page.on("request", lambda request: requested.append(request.url))
        url = f"{live_server.url}/admin/site_settings/editorprofile/{pk}/change/"
        page.goto(url, wait_until="load", timeout=20_000)
        page.wait_for_timeout(800)
        assert page.locator(".note-editor").count() == 0
        assert not [item for item in requested if "editor-core" in item]
        assert page.locator(
            ".mf-editor-preview textarea[name^='preview-']"
        ).is_visible()
    assert not failures, failures


@pytest.mark.django_db(transaction=True)
def test_dark_mode_and_mobile_layout(live_server) -> None:
    """The editor follows the dark theme and never overflows a phone viewport."""
    cookies, pk = _prepare()
    failures: list[str] = []
    with chromium() as browser:
        page = _context(browser, live_server, cookies, scheme="light").new_page()
        watch(page, failures)
        _open_profile(page, live_server, pk)
        light = page.evaluate(
            "() => getComputedStyle(document.querySelector('.note-editable'))"
            ".backgroundColor"
        )
        page.close()

        dark_page = _context(browser, live_server, cookies, scheme="dark").new_page()
        watch(dark_page, failures)
        _open_profile(dark_page, live_server, pk)
        dark = dark_page.evaluate(
            "() => getComputedStyle(document.querySelector('.note-editable'))"
            ".backgroundColor"
        )
        assert light != dark
        channels = [
            int(part) for part in dark.replace("rgb(", "").rstrip(")").split(",")[:3]
        ]
        assert sum(channels) / 3 < 90, dark

        phone = _context(browser, live_server, cookies, width=360).new_page()
        watch(phone, failures)
        _open_profile(phone, live_server, pk)
        sizes = overflow(phone)
        assert sizes["document"] <= sizes["viewport"] + 1, sizes
        toolbar = phone.locator(".mf-editor-preview .note-toolbar")
        box = toolbar.bounding_box()
        assert box is not None and box["width"] <= 360
    assert not failures, failures


def _journal() -> tuple[object, dict[str, str]]:
    """A published article with open comments and a signed-in reader's cookie."""
    user_model = get_user_model()
    author = user_model.objects.create_user(
        username="editor-author", email="editor-author@example.test"
    )
    reader = user_model.objects.create_user(
        username="editor-reader",
        email="editor-reader@example.test",
        email_verified_at=timezone.now(),
    )
    post = Post.objects.create(
        title="Editor article",
        summary="Summary of the editor article.",
        body="<p>An article people can comment on, long enough to be valid.</p>",
        author=author,
        status=PostStatus.PUBLISHED,
        publish_at=timezone.now() - timedelta(hours=1),
    )
    return post, login_cookie(reader)


@pytest.mark.django_db(transaction=True)
def test_comment_editor_works_under_the_strict_public_csp(live_server) -> None:
    """The public CSP (no inline styles/eval) still runs the comment editor."""
    EditorProfile.seed()
    post, cookies = _journal()
    failures: list[str] = []
    with chromium() as browser:
        page = _context(browser, live_server, cookies).new_page()
        watch(page, failures)
        requested: list[str] = []
        page.on("request", lambda request: requested.append(request.url))

        page.goto(f"{live_server.url}/blog/", wait_until="load", timeout=20_000)
        assert not [url for url in requested if "editor-core" in url], requested

        page.goto(
            f"{live_server.url}{post.get_absolute_url()}",
            wait_until="load",
            timeout=20_000,
        )
        page.locator("#comments").scroll_into_view_if_needed()
        editor = page.locator("#comments .note-editor").first
        expect(editor).to_be_visible(timeout=15_000)
        editable = editor.locator(".note-editable")
        editable.click()
        editable.type("Thanks for the guide")
        editable.press("Control+A")
        editor.get_by_role("button", name="Bold").first.click()
        editor.get_by_role("button", name="Link").first.click()
        dialog = page.locator(".note-modal.open")
        expect(dialog).to_be_visible()
        dialog.locator(".note-link-url").fill("https://example.com/tool")
        dialog.locator(".note-link-btn").click()
        assert editor.locator("a[href='https://example.com/tool']").count() == 1

        assert page.evaluate("typeof window.jQuery") == "undefined"
        light = page.evaluate(
            "getComputedStyle(document.querySelector('#comments .note-editable'))"
            ".backgroundColor"
        )
        page.get_by_role("button", name="Post comment").click()
        page.wait_for_load_state("load")
        body = page.locator("#comments .comment__body").first
        expect(body).to_be_visible(timeout=10_000)
        assert body.locator("a[href='https://example.com/tool']").count() == 1
        assert 'rel="nofollow noopener"' in body.inner_html()

        page.evaluate("localStorage.setItem('mobofirm-theme', 'dark')")
        page.reload(wait_until="load")
        page.locator("#comments").scroll_into_view_if_needed()
        expect(page.locator("#comments .note-editor").first).to_be_visible(
            timeout=15_000
        )
        dark = page.evaluate(
            "getComputedStyle(document.querySelector('#comments .note-editable'))"
            ".backgroundColor"
        )
        assert light != dark
    assert not failures, failures


@pytest.mark.django_db(transaction=True)
def test_public_editor_is_responsive_and_overflow_free(live_server) -> None:
    """The comment editor fits a 360 px viewport without horizontal overflow."""
    EditorProfile.seed()
    post, cookies = _journal()
    failures: list[str] = []
    with chromium() as browser:
        page = _context(browser, live_server, cookies, width=360).new_page()
        watch(page, failures)
        page.goto(
            f"{live_server.url}{post.get_absolute_url()}",
            wait_until="load",
            timeout=20_000,
        )
        page.locator("#comments").scroll_into_view_if_needed()
        expect(page.locator("#comments .note-editor").first).to_be_visible(
            timeout=15_000
        )
        sizes = overflow(page)
        assert sizes["document"] <= sizes["viewport"] + 1, sizes
    assert not failures, failures
