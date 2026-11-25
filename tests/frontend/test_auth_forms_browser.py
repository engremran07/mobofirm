"""Account pages in a real browser: live password checklist, social buttons, inline hints.

Needs the rebuilt frontend bundle (``static/build``): the checklist and the inline
hints are new lazy modules. The social-button dialog uses the existing dialog module.
"""

from __future__ import annotations

import uuid

import pytest
from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.sites.models import Site
from django.test import Client, override_settings
from playwright.sync_api import Page, Request, expect

from apps.consent import services as consent_services
from tests.frontend.browser_support import (
    chromium,
    dismiss_dialogs,
    login_cookie,
    make_user,
    overflow,
    ready,
    record_requests,
    session_context,
    watch,
)

SIGNUP = "/users/signup/"
LOGIN = "/users/login/"
STRONG = "Quill-Marble-7291-Drift!"
DIALOG_TEXT = "Google sign-in is not set up yet by the site admin."


def _state(page: Page, rule: str) -> str | None:
    return page.locator(f'[data-rule="{rule}"]').get_attribute("data-state")


def _wait_state(page: Page, rule: str, state: str) -> None:
    page.wait_for_function(
        '([rule, state]) => document.querySelector(`[data-rule="${rule}"]`)?.dataset.state === state',
        arg=[rule, state],
        timeout=15_000,
    )


def _open(page: Page, live_server, path: str) -> None:
    page.goto(f"{live_server.url}{path}", wait_until="load")
    ready(page)


@pytest.mark.django_db(transaction=True)
def test_signup_checklist_updates_live_and_never_leaks_the_password(live_server):
    failures: list[str] = []
    urls: list[str] = []
    posts: list[tuple[str, str]] = []

    with chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        watch(page, failures)
        record_requests(page, urls)

        def record_post(request: Request) -> None:
            if request.method == "POST":
                posts.append((request.url, request.headers.get("x-csrftoken", "")))

        page.on("request", record_post)
        _open(page, live_server, SIGNUP)

        # Rows come from the server's validators, minimum length included.
        assert page.get_by_text("At least 8 characters long").is_visible()
        assert _state(page, "min_length") == "pending"

        page.fill("#id_password1", "12345678")
        _wait_state(page, "numeric", "fail")
        assert _state(page, "min_length") == "pass"
        assert _state(page, "common") == "fail"
        # No email typed yet: the similarity rule says it will be checked on submit.
        assert _state(page, "similarity") == "unchecked"

        page.fill("#id_email", "tangerine@example.com")
        page.fill("#id_password1", "tangerine1")
        _wait_state(page, "similarity", "fail")

        page.fill("#id_password1", STRONG)
        for rule in ("min_length", "numeric", "common", "similarity"):
            _wait_state(page, rule, "pass")
        summary = page.locator("[data-pw-summary]")
        assert summary.get_attribute("aria-live") == "polite"
        page.wait_for_function(
            "() => document.querySelector('[data-pw-summary]').textContent.includes('met')"
        )

        # Confirm-match indicator.
        assert _state(page, "match") == "pending"
        page.fill("#id_password2", "something-else")
        _wait_state(page, "match", "fail")
        page.fill("#id_password2", STRONG)
        _wait_state(page, "match", "pass")
        assert "All password requirements are met." in summary.inner_text()

        # Clearing the field resets every row without another request.
        page.fill("#id_password1", "")
        _wait_state(page, "min_length", "pending")
        assert overflow(page)["document"] <= overflow(page)["viewport"] + 1

    assert posts and all(url.endswith("/users/password-rules/") for url, _ in posts)
    assert all(token for _, token in posts), "every check carries the CSRF token"
    assert not [url for url in urls if STRONG in url or "tangerine1" in url]
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_profile_completion_and_change_password_have_the_checklist(live_server):
    user = make_user("checklist_profile")
    cookies = login_cookie(user)
    failures: list[str] = []

    with chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        _open(page, live_server, "/users/profile/")
        page.fill("#new-password-1", "12345678")
        _wait_state(page, "numeric", "fail")
        page.fill("#new-password-2", "12345678")
        _wait_state(page, "match", "pass")
        context.close()

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_account_menu_is_responsive_and_logout_requires_app_confirmation(live_server):
    member = make_user("account-menu")
    policy = consent_services.active_policy()
    cookies = {
        **login_cookie(member),
        consent_services.cookie_name(): consent_services.encode_cookie(
            policy.version, [], uuid.uuid4()
        ),
    }
    failures: list[str] = []
    native: list[str] = []

    with chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        page.set_viewport_size({"width": 1365, "height": 900})
        watch(page, failures)
        dismiss_dialogs(page, native)
        _open(page, live_server, "/")

        desktop_menu = page.locator(".site-header-actions details.site-account-menu")
        desktop_menu.locator("summary").click()
        expect(desktop_menu.locator("summary span")).to_have_count(0)
        for label in (
            "Account overview",
            "Profile",
            "Wallet",
            "My contributions",
            "Notifications",
        ):
            expect(
                desktop_menu.get_by_role("link", name=label, exact=True)
            ).to_be_visible()
        expect(desktop_menu.locator(".eco-pill")).to_be_visible()
        for mode in ("auto", "light", "dark"):
            expect(desktop_menu.locator(f'[data-theme-set="{mode}"]')).to_be_visible()
        expect(desktop_menu.locator("[data-contrast-toggle]")).to_be_visible()
        desktop_menu.locator('[data-theme-set="light"]').click()
        light_menu_colour = desktop_menu.locator(".site-account-menu__panel").evaluate(
            "(element) => getComputedStyle(element).backgroundColor"
        )
        desktop_menu.locator('[data-theme-set="dark"]').click()
        dark_menu_colour = desktop_menu.locator(".site-account-menu__panel").evaluate(
            "(element) => getComputedStyle(element).backgroundColor"
        )
        assert light_menu_colour != dark_menu_colour
        assert light_menu_colour != "rgb(0, 0, 0)"
        assert dark_menu_colour != "rgb(0, 0, 0)"
        page.mouse.click(5, 800)
        expect(desktop_menu).not_to_have_attribute("open", "")
        desktop_menu.locator("summary").click()
        page.keyboard.press("Escape")
        expect(desktop_menu).not_to_have_attribute("open", "")
        expect(desktop_menu.locator("summary")).to_be_focused()

        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="Toggle navigation").click()
        mobile_menu = page.locator("#mobile-navigation details.site-account-menu")
        mobile_menu.locator("summary").click()
        expect(mobile_menu.get_by_role("link", name="Profile")).to_be_visible()
        assert overflow(page)["document"] <= overflow(page)["viewport"] + 1

        mobile_menu.get_by_role("button", name="Sign out").click()
        confirmation = page.locator("dialog[data-app-dialog]")
        expect(confirmation).to_be_visible()
        expect(confirmation.get_by_role("heading", name="Sign out?")).to_be_visible()
        confirmation.get_by_role("button", name="Cancel").click()
        expect(confirmation).to_be_hidden()

        if mobile_menu.get_attribute("open") is None:
            mobile_menu.locator("summary").click()
        mobile_menu.get_by_role("button", name="Sign out").click()
        expect(confirmation).to_be_visible()
        with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.endswith("/users/logout/")
            )
        ) as logout_response:
            confirmation.get_by_role("button", name="Sign out").click()
        assert logout_response.value.status in {302, 303}
        assert (
            page.request.get(
                f"{live_server.url}/users/dashboard/", max_redirects=0
            ).status
            == 302
        )
        context.close()

    assert not native, f"native dialogs were used: {native}"
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_account_workspace_services_fit_phone_and_desktop_viewports(live_server):
    user = make_user("account_workspace")
    cookies = login_cookie(user)
    failures: list[str] = []

    with chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        _open(page, live_server, "/users/dashboard/")

        for width in (390, 1365):
            page.set_viewport_size({"width": width, "height": 900})
            assert overflow(page)["document"] <= overflow(page)["viewport"] + 1
            for label in ("Purchased articles", "Wallet", "Referrals"):
                assert page.locator(".eco-tile").filter(has_text=label).count() == 1
        context.close()

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("path", [LOGIN, SIGNUP])
def test_unconfigured_google_button_opens_an_app_dialog_and_submits_nothing(
    live_server, path
):
    failures: list[str] = []
    native: list[str] = []
    requests: list[str] = []

    with override_settings(SOCIALACCOUNT_PROVIDERS={}), chromium() as browser:
        page = browser.new_page(viewport={"width": 390, "height": 844})
        watch(page, failures)
        dismiss_dialogs(page, native)
        record_requests(page, requests)
        _open(page, live_server, path)
        assert overflow(page)["document"] <= overflow(page)["viewport"] + 1

        button = page.get_by_role("button", name="Continue with Google")
        assert button.is_visible()
        before = page.url
        button.click()

        dialog = page.locator("dialog#social-unavailable-google")
        dialog.wait_for(state="visible")
        assert DIALOG_TEXT in dialog.inner_text()
        assert dialog.get_attribute("aria-labelledby")
        assert page.url == before
        assert not [url for url in requests if "/accounts/google" in url]
        # The page has no sideways scroll before the dialog opens, and the dialog itself
        # (a bottom sheet on phones) fits the viewport.
        box = dialog.bounding_box()
        assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= 391
        page.keyboard.press("Escape")
        dialog.wait_for(state="hidden")
        assert (
            page.evaluate("document.activeElement?.dataset.socialProvider") == "google"
        )

    assert not native, f"native dialogs were used: {native}"
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_configured_google_button_continues_into_oauth(live_server):
    app = SocialApp.objects.create(
        provider="google",
        name="Google",
        client_id="1234567890-real.apps.googleusercontent.com",
        secret="real-secret",
    )
    app.sites.add(Site.objects.get_current())
    failures: list[str] = []
    reached: list[str] = []

    with chromium() as browser:
        context = browser.new_context(viewport={"width": 1365, "height": 900})
        # Never reach the real provider: answer its origin locally.
        context.route(
            "https://accounts.google.com/**",
            lambda route: route.fulfill(
                status=200, content_type="text/html", body="<p>google</p>"
            ),
        )
        page = context.new_page()
        watch(page, failures)

        def record_google(request: Request) -> None:
            if "accounts.google.com" in request.url:
                reached.append(request.url)

        page.on("request", record_google)
        _open(page, live_server, LOGIN)

        assert page.locator("dialog#social-unavailable-google").count() == 0
        page.get_by_role("button", name="Continue with Google").click()
        page.wait_for_url("https://accounts.google.com/**")
        context.close()

    assert reached and "client_id=1234567890-real" in reached[0]
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_signup_shows_inline_hints_and_blocks_an_invalid_submit(live_server):
    failures: list[str] = []
    native: list[str] = []
    posts: list[str] = []

    with chromium() as browser:
        page = browser.new_page(viewport={"width": 390, "height": 844})
        watch(page, failures)
        dismiss_dialogs(page, native)
        record_requests(page, posts, method="POST")
        _open(page, live_server, SIGNUP)
        assert (
            page.locator("form[data-auth-form]").get_attribute("novalidate") is not None
        )

        # Invalid email: friendly hint in the field's own alert container on blur.
        page.fill("#id_email", "not-an-email")
        page.locator("#id_password1").focus()
        hint = page.locator("#id_email_errors")
        hint.wait_for(state="visible")
        assert "valid email address" in hint.inner_text()
        assert hint.get_attribute("role") == "alert"
        assert page.locator("#id_email").get_attribute("aria-invalid") == "true"
        describedby = page.locator("#id_email").get_attribute("aria-describedby")
        assert describedby is not None and "id_email_errors" in describedby

        # Fixing the value clears the hint as you type.
        page.fill("#id_email", "person@example.com")
        hint.wait_for(state="hidden")
        assert page.locator("#id_email").get_attribute("aria-invalid") is None

        # Submitting with missing fields sends nothing and focuses the first problem.
        page.fill("#id_email", "")
        page.get_by_role("button", name="Create account").click()
        page.locator("#id_email_errors").wait_for(state="visible")
        assert "required" in page.locator("#id_email_errors").inner_text().lower()
        assert page.locator("#id_password1_errors").is_visible()
        assert page.evaluate("document.activeElement.id") == "id_email"

    assert not posts, f"an invalid form was submitted: {posts}"
    assert not native
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_server_errors_use_the_same_container_and_clear_on_edit(live_server):
    user = make_user("hint_server")
    cookies = login_cookie(user)
    failures: list[str] = []

    with chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        _open(page, live_server, "/users/tell-us-about-you/")

        # website is type=url: the browser-side rule gives a hint with an example.
        page.fill("#id_website", "yoursite")
        page.locator("#id_location").focus()
        hint = page.locator("#id_website_errors")
        hint.wait_for(state="visible")
        assert "https://" in hint.inner_text()
        page.fill("#id_website", "https://example.com")
        hint.wait_for(state="hidden")

        # Required text input on submit: focus moves to it, nothing is submitted.
        page.fill("#id_full_name", "")
        page.get_by_role("button", name="Save and continue").click()
        page.locator("#id_full_name_errors").wait_for(state="visible")
        assert page.evaluate("document.activeElement.id") in {
            "id_username",
            "id_full_name",
        }
        context.close()

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_recovery_codes_can_be_copied_and_downloaded_as_text(live_server, tmp_path):
    user = make_user("recovery_codes_user")
    codes = [f"ABCD-{index:04d}" for index in range(10)]
    client = Client()
    client.force_login(user)
    session = client.session
    session.pop("device_registration_prompt", None)
    session["mfa_backup_codes_once"] = codes
    session.save()
    cookies = {
        settings.SESSION_COOKIE_NAME: client.cookies[settings.SESSION_COOKIE_NAME].value
    }
    failures: list[str] = []

    with chromium() as browser:
        context = session_context(browser, live_server, cookies)
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        page = context.new_page()
        watch(page, failures)
        _open(page, live_server, "/users/mfa/backup-codes/")

        copy_button = page.get_by_role("button", name="Copy all")
        download_button = page.get_by_role("button", name="Download .txt")
        assert copy_button.is_visible()
        assert download_button.is_visible()

        copy_button.click()
        page.get_by_text("Recovery codes copied.").wait_for(state="visible")
        copied = page.evaluate("navigator.clipboard.readText()")
        assert copied.split() == codes

        with page.expect_download() as download_info:
            download_button.click()
        download = download_info.value
        assert download.suggested_filename == "mobofirm-recovery-codes.txt"
        saved = tmp_path / download.suggested_filename
        download.save_as(saved)
        assert saved.read_text(encoding="utf-8").split() == codes
        assert overflow(page)["document"] <= overflow(page)["viewport"]
        context.close()

    assert not failures, "\n".join(failures)
