from __future__ import annotations

import json
import re

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from apps.seo.crawler import parse_html
from apps.seo.models import ChangeLedger, Issue, Redirect
from tests.backend.seo.seo_helpers import SITE, make_catalog

NOISE = ("Failed to load resource:",)


def collect_errors(page, errors):
    page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
    page.on(
        "console",
        lambda message: (
            errors.append(f"console: {message.text}")
            if message.type == "error" and not message.text.startswith(NOISE)
            else None
        ),
    )


def launch(playwright):
    try:
        return playwright.chromium.launch()
    except PlaywrightError as error:
        pytest.fail(
            "Chromium is required for SEO browser checks. Install it with "
            f"`python -m playwright install chromium`.\n{error}"
        )


def overflow(page):
    return page.evaluate(
        """() => ({
            viewport: document.documentElement.clientWidth,
            document: document.documentElement.scrollWidth
        })"""
    )


@pytest.mark.django_db(transaction=True)
def test_sitemaps_and_robots_render_without_csp_or_console_errors(live_server):
    make_catalog()
    errors: list[str] = []

    with sync_playwright() as playwright:
        browser = launch(playwright)
        page = browser.new_page()
        collect_errors(page, errors)
        for path in ("/sitemap.xml", "/sitemap-brands.xml", "/robots.txt"):
            response = page.goto(f"{live_server.url}{path}", wait_until="load")
            assert response is not None and response.status == 200, path
        page.goto(f"{live_server.url}/sitemap.xml", wait_until="load")
        assert "sitemap-brands.xml" in page.content()
        browser.close()

    assert errors == []


@pytest.mark.django_db(transaction=True)
def test_view_source_head_is_correct_on_key_public_pages(live_server, settings):
    settings.SITE_URL = SITE
    data = make_catalog()
    paths = [
        "/",
        reverse("firmwares:brand_list"),
        reverse("firmwares:brand_detail", args=[data.brand.slug]),
        reverse("firmwares:model_detail", args=[data.brand.slug, data.model.slug]),
        reverse("firmwares:chipset_detail", args=[data.chipset.slug]),
        reverse("firmwares:firmware_detail", args=["official", data.firmware.pk]),
        reverse("firmwares:flashing_tools"),
        reverse("firmwares:flashing_tool_detail", args=[data.tool.slug]),
        reverse("blog:post_list"),
        data.post.get_absolute_url(),
        reverse("tags:detail", args=[data.tag.slug]),
        reverse("pages:page", args=[data.page.slug]),
    ]
    errors: list[str] = []

    with sync_playwright() as playwright:
        browser = launch(playwright)
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        collect_errors(page, errors)
        for path in paths:
            url = f"{live_server.url}{path}"
            # The raw response is exactly what a crawler sees ("view source").
            raw = page.request.get(url)
            assert raw.status == 200, path
            head = parse_html(raw.text())
            assert len(head.titles) == 1 and head.titles[0], path
            assert len(head.descriptions) == 1 and head.descriptions[0], path
            assert len(head.robots_meta) == 1, path
            assert head.canonicals == [f"{SITE}{'/' if path == '/' else path}"], path
            assert "x-robots-tag" not in raw.headers, path
            assert head.jsonld_errors == 0
            assert "BreadcrumbList" in head.jsonld_types or path == "/"

            page.goto(url, wait_until="load")
            counts = page.evaluate(
                """() => ({
                    titles: document.querySelectorAll("head title").length,
                    canonicals: document.querySelectorAll('head link[rel="canonical"]').length,
                    robots: document.querySelectorAll('head meta[name="robots"]').length,
                    descriptions: document.querySelectorAll('head meta[name="description"]').length,
                    ogTitle: document.querySelectorAll('head meta[property="og:title"]').length,
                    title: document.title,
                })"""
            )
            assert counts["titles"] == counts["canonicals"] == counts["robots"] == 1
            assert counts["descriptions"] == counts["ogTitle"] == 1
            assert counts["title"] == head.titles[0]
            blocks = page.evaluate(
                """() => Array.from(
                    document.querySelectorAll('script[type="application/ld+json"]')
                ).map(node => node.textContent)"""
            )
            for block in blocks:
                json.loads(block)
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1, path
        browser.close()

    assert errors == []


@pytest.mark.django_db(transaction=True)
def test_noindex_surfaces_in_the_served_head_for_search_and_variants(
    live_server, settings
):
    settings.SITE_URL = SITE
    make_catalog()

    with sync_playwright() as playwright:
        browser = launch(playwright)
        page = browser.new_page()
        for path in (
            f"{reverse('firmwares:universal_search')}?q=atlas",
            f"{reverse('firmwares:browse')}?q=atlas",
        ):
            head = parse_html(page.request.get(f"{live_server.url}{path}").text())
            assert head.robots_meta[0].startswith("noindex"), path
            assert head.jsonld_types == []
        admin = page.request.get(f"{live_server.url}/admin/login/")
        assert admin.headers["x-robots-tag"] == "noindex, nofollow"
        browser.close()


@pytest.mark.django_db(transaction=True)
def test_seo_control_center_is_usable_responsive_and_error_free(live_server):
    make_catalog()
    admin_user = get_user_model().objects.create_superuser(
        username="seo-browser-admin",
        email="seo-browser-admin@example.com",
        password="browser-pass-12345",
    )
    Issue.objects.create(
        code="missing_title",
        severity="error",
        url=f"{SITE}/a/",
        message="The page has no <title>.",
        fingerprint="browser-1",
    )
    Issue.objects.create(
        code="slow_response",
        severity="info",
        url=f"{SITE}/b/",
        message="Response took 4000 ms.",
        fingerprint="browser-2",
    )
    proposal = ChangeLedger.objects.create(
        idempotency_key="browser-proposal",
        kind="redirect_create",
        action="create",
        target_model="seo.redirect",
        target_label="/old-browser/ -> /new-browser/",
        after={
            "source": "/old-browser/",
            "target": "/new-browser/",
            "is_permanent": True,
            "is_active": True,
        },
        reason="Browser test proposal",
        source="test",
        requires_approval=True,
    )
    client = Client()
    client.force_login(admin_user)
    session = client.session
    session.pop("device_registration_prompt", None)
    session.save()
    cookie = client.cookies[settings.SESSION_COOKIE_NAME]
    errors: list[str] = []

    with sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(viewport={"width": 1365, "height": 900})
        context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": cookie.value,
                    "url": live_server.url,
                }
            ]
        )
        page = context.new_page()
        collect_errors(page, errors)

        for name in (
            "dashboard",
            "inventory",
            "inspect",
            "issues",
            "ledger",
            "jobs",
            "settings",
        ):
            for width in (1365, 390):
                page.set_viewport_size({"width": width, "height": 800})
                response = page.goto(
                    f"{live_server.url}{reverse(f'seo:{name}')}", wait_until="load"
                )
                assert response is not None and response.status == 200, name
                assert page.get_by_role("navigation", name="SEO sections").is_visible()
                assert page.locator("h1.mf-seo-title").is_visible(), name
                dimensions = overflow(page)
                assert dimensions["document"] <= dimensions["viewport"] + 1, (
                    f"{name} overflows at {width}px: {dimensions}"
                )

        page.set_viewport_size({"width": 1365, "height": 900})
        page.goto(f"{live_server.url}{reverse('seo:dashboard')}", wait_until="load")
        assert page.get_by_text("No completed crawl yet").is_visible()
        assert page.get_by_role("button", name="Run crawl and audit now").is_visible()

        page.goto(f"{live_server.url}{reverse('seo:issues')}", wait_until="load")
        page.get_by_label("Severity").select_option("error")
        page.get_by_role("button", name="Filter").click()
        page.wait_for_url(re.compile(r"severity=error"))
        assert page.get_by_text("The page has no <title>.").is_visible()
        assert page.get_by_text("Response took 4000 ms.").count() == 0

        page.goto(f"{live_server.url}{reverse('seo:inspect')}", wait_until="load")
        page.get_by_label("Page path or URL").fill("/firmwares/nova/atlas-one/")
        page.keyboard.press("Enter")
        page.wait_for_url(re.compile(r"url="))
        assert page.get_by_text("What the resolver intends").is_visible()
        assert page.get_by_text("Atlas One firmware download").first.is_visible()

        page.goto(
            f"{live_server.url}{reverse('seo:ledger')}?status=proposed",
            wait_until="load",
        )
        page.get_by_role("button", name="Approve and apply").click()
        page.wait_for_load_state("load")
        assert page.get_by_text("is now Applied").is_visible()
        browser.close()

    proposal.refresh_from_db()
    assert proposal.status == ChangeLedger.Status.APPLIED
    assert Redirect.objects.filter(source="/old-browser/").exists()
    assert errors == []
