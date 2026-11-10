"""The shared share component in a real browser (articles and firmware pages)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import BrowserContext, Page, expect

from apps.blog.models import Post, PostStatus
from apps.firmwares.models import OfficialFirmware
from tests.frontend.browser_support import make_user, overflow, ready, watch
from tests.frontend.catalog_browser_support import locked_chromium, seed_catalog

NETWORKS = ("X", "Facebook", "WhatsApp", "Telegram", "LinkedIn", "Email")


@pytest.fixture(name="pages")
def seeded_pages(request):
    request.getfixturevalue("transactional_db")
    data = seed_catalog()
    firmware = OfficialFirmware.objects.create(
        original_file_name="share.zip",
        stored_file_path="/firmware/share.zip",
        build_number="SHARE.240101",
        brand=data["acme"],
        model=data["model"],
        is_verified=True,
    )
    author = make_user("share-author")
    post = Post.objects.create(
        title="Shareable guide & more",
        summary="A guide worth sharing.",
        body="<p>Body text of the shareable guide, long enough to be valid.</p>",
        author=author,
        status=PostStatus.PUBLISHED,
        publish_at=timezone.now() - timedelta(hours=1),
    )
    return {
        "article": post.get_absolute_url(),
        "firmware": reverse("firmwares:firmware_detail", args=["official", firmware.pk]),
        "download": reverse("firmwares:firmware_download", args=["official", firmware.pk]),
    }


def _context(browser, *, width: int = 1280, native: str = "") -> BrowserContext:
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    if native:
        context.add_init_script(native)
    return context


def _share(page: Page):
    return page.locator("section.share[data-share]").first


@pytest.mark.django_db(transaction=True)
def test_share_links_copy_and_layout_on_articles_and_firmware(
    live_server, pages
) -> None:
    failures: list[str] = []
    with locked_chromium() as browser:
        for kind, noun in (
            ("article", "Article link copied."),
            ("firmware", "Firmware link copied."),
            ("download", "Firmware link copied."),
        ):
            context = _context(browser)
            page = context.new_page()
            watch(page, failures)
            url = f"{live_server.url}{pages[kind]}"
            page.goto(url, wait_until="load")
            ready(page)

            share = _share(page)
            expect(share).to_be_visible()
            canonical = share.get_attribute("data-share-url")
            assert canonical is not None
            assert canonical.split("//", 1)[1].split("/", 1)[1] == pages[
                "firmware" if kind == "download" else kind
            ].lstrip("/"), "download pages share the detail URL"
            for name in NETWORKS:
                link = share.get_by_role("link", name=name)
                expect(link).to_be_visible()
                href = link.get_attribute("href") or ""
                assert "mailto:" in href or (
                    link.get_attribute("target") == "_blank"
                    and link.get_attribute("rel") == "noopener noreferrer"
                ), (kind, name)

            # Copy link: the canonical URL lands on the clipboard and a toast confirms.
            share.get_by_role("button", name="Copy link").click()
            expect(page.locator(".toast-ui").filter(has_text=noun)).to_be_visible()
            assert page.evaluate("navigator.clipboard.readText()") == canonical

            # Keyboard users reach every control in order.
            share.get_by_role("button", name="Copy link").focus()
            page.keyboard.press("Tab")
            assert page.evaluate(
                "document.activeElement.textContent.trim().startsWith('X')"
            )
            context.close()

        for width in (360, 768):
            context = _context(browser, width=width)
            page = context.new_page()
            page.goto(f"{live_server.url}{pages['article']}", wait_until="load")
            sizes = overflow(page)
            assert sizes["document"] <= sizes["viewport"] + 1, (width, sizes)
            context.close()
    assert not failures, failures


NATIVE_OK = """
window.__shared = [];
Object.defineProperty(navigator, 'share', {
  configurable: true,
  value: async (data) => { window.__shared.push(data); },
});
"""
NATIVE_CANCELLED = """
Object.defineProperty(navigator, 'share', {
  configurable: true,
  value: async () => { throw new DOMException('cancelled', 'AbortError'); },
});
"""
NATIVE_BROKEN = """
Object.defineProperty(navigator, 'share', {
  configurable: true,
  value: async () => { throw new DOMException('denied', 'NotAllowedError'); },
});
"""
NATIVE_MISSING = """
Object.defineProperty(navigator, 'share', { configurable: true, value: undefined });
"""


@pytest.mark.django_db(transaction=True)
def test_native_share_is_an_enhancement_with_clear_failure_handling(
    live_server, pages
) -> None:
    """Needs ``vite build`` (modules/share.js)."""
    failures: list[str] = []
    url = f"{live_server.url}{pages['article']}"
    with locked_chromium() as browser:
        # Supported: the button appears and passes title, text and canonical URL on.
        context = _context(browser, native=NATIVE_OK)
        page = context.new_page()
        watch(page, failures)
        page.goto(url, wait_until="load")
        button = _share(page).get_by_role("button", name="Share…")
        expect(button).to_be_visible()
        button.click()
        shared = page.evaluate("window.__shared")
        assert shared == [
            {
                "title": "Shareable guide & more",
                "url": _share(page).get_attribute("data-share-url"),
                "text": "A guide worth sharing.",
            }
        ]
        context.close()

        # Unsupported: the button stays hidden, copy and the links remain.
        context = _context(browser, native=NATIVE_MISSING)
        page = context.new_page()
        page.goto(url, wait_until="load")
        expect(_share(page).get_by_role("button", name="Share…")).to_be_hidden()
        expect(_share(page).get_by_role("button", name="Copy link")).to_be_visible()
        context.close()

        # Closing the share sheet is silent; a real failure says what to do instead.
        context = _context(browser, native=NATIVE_CANCELLED)
        page = context.new_page()
        page.goto(url, wait_until="load")
        _share(page).get_by_role("button", name="Share…").click()
        page.wait_for_timeout(300)
        assert page.locator(".toast-ui").count() == 0
        context.close()

        context = _context(browser, native=NATIVE_BROKEN)
        page = context.new_page()
        page.goto(url, wait_until="load")
        _share(page).get_by_role("button", name="Share…").click()
        expect(
            page.locator(".toast-ui").filter(has_text="Use Copy link instead")
        ).to_be_visible()
        context.close()
    assert not failures, failures
