"""Browser checks for the wallet and referral pages (one browser at a time)."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from apps.referral import services as referral_services
from apps.wallet import services
from apps.wallet.models import LedgerReason
from tests.frontend.browser_support import (
    chromium,
    login_cookie,
    make_user,
    ready,
    session_context,
    visit_all,
    watch,
)

LOCK = Path(settings.BASE_DIR) / ".browser_lock"
LOCK_WAIT_SECONDS = 600
LOCK_RETRY_SECONDS = 10


@contextmanager
def browser_lock() -> Iterator[None]:
    """Machine-wide lock so only one Chromium runs at a time (low-RAM host)."""
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            os.mkdir(LOCK)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                pytest.fail("Timed out waiting for the browser lock.")
            time.sleep(LOCK_RETRY_SECONDS)
    try:
        yield
    finally:
        os.rmdir(LOCK)


@pytest.mark.django_db(transaction=True)
def test_wallet_and_referral_pages_have_no_console_csp_or_overflow_problems(
    live_server,
):
    user = make_user("browser_wallet")
    services.credit(user, 120, reason=LedgerReason.TOPUP, key="browser:seed")
    services.credit(user, 600, reason=LedgerReason.EARNING, key="browser:sale")
    services.request_topup(user, 100)
    referral_services.get_or_create_code(user)
    cookie = login_cookie(user)
    paths = [
        reverse("wallet:dashboard"),
        reverse("wallet:ledger"),
        reverse("wallet:earn"),
        reverse("wallet:spend"),
        reverse("wallet:topup"),
        reverse("wallet:payouts"),
        reverse("referral:dashboard"),
    ]
    failures: list[str] = []
    with browser_lock(), chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        visit_all(page, live_server, paths, failures)

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_cancelling_a_topup_uses_the_app_dialog_not_a_native_one(live_server):
    user = make_user("browser_topup")
    topup = services.request_topup(user, 100)
    cookie = login_cookie(user)
    failures: list[str] = []
    native: list[str] = []
    with browser_lock(), chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)

        def record_native(dialog) -> None:
            native.append(dialog.type)
            dialog.dismiss()

        page.on("dialog", record_native)
        page.goto(f"{live_server.url}{reverse('wallet:topup')}", wait_until="load")
        ready(page)

        page.locator(f'[data-dialog-open="cancel-topup-{topup.pk}"]').click()
        dialog = page.locator(f"#cancel-topup-{topup.pk}")
        assert dialog.is_visible()
        page.keyboard.press("Escape")
        assert not dialog.is_visible()

        page.locator(f'[data-dialog-open="cancel-topup-{topup.pk}"]').click()
        dialog.get_by_role("button", name="Cancel top-up").click()
        page.wait_for_load_state("load")
        assert page.get_by_text("Top-up request cancelled.").is_visible()

    assert native == []
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_ledger_filter_updates_the_results_without_a_full_reload(live_server):
    user = make_user("browser_ledger")
    services.credit(user, 50, reason=LedgerReason.TOPUP, key="browser:in")
    services.debit(user, 7, reason=LedgerReason.SPEND, key="browser:out")
    cookie = login_cookie(user)
    failures: list[str] = []
    with browser_lock(), chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}{reverse('wallet:ledger')}", wait_until="load")
        ready(page)

        page.get_by_label("Type").select_option("debit")
        page.wait_for_function(
            "() => !document.querySelector('#ledger-results').textContent.includes('Top-up')"
        )
        results = page.locator("#ledger-results").inner_text()

    assert "Spend" in results and "Top-up" not in results
    assert not failures, "\n".join(failures)
