"""Shared pieces of the firmware catalogue browser checks (lock, data, sign-in)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from apps.firmwares.models import Brand, Chipset, Model, Variant
from tests.frontend.browser_support import chromium, ready

BROWSER_LOCK = Path(__file__).resolve().parents[2] / ".browser_lock"
LOCK_WAIT_SECONDS = 600
LOCK_RETRY_SECONDS = 10


@contextmanager
def locked_chromium() -> Iterator[Browser]:
    """One Chromium at a time on this machine: hold the shared lock directory."""
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            BROWSER_LOCK.mkdir()
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                pytest.fail("The browser lock stayed busy for 10 minutes.")
            time.sleep(LOCK_RETRY_SECONDS)
    try:
        with chromium() as browser:
            yield browser
    finally:
        BROWSER_LOCK.rmdir()


def seed_catalog() -> dict[str, object]:
    """Two brands, models for each, one variant and a chipset."""
    acme = Brand.objects.create(name="Acme Mobile", slug="acme-mobile")
    zenith = Brand.objects.create(name="Zenith Phones", slug="zenith-phones")
    phone = Model.objects.create(brand=acme, name="Acme One", slug="acme-one")
    Model.objects.create(brand=acme, name="Acme Two", slug="acme-two")
    Model.objects.create(brand=zenith, name="Zenith Prime", slug="zenith-prime")
    variant = Variant.objects.create(model=phone, name="Global", slug="global")
    chipset = Chipset.objects.create(
        vendor="MediaTek", name="Helio G99", slug="mediatek-helio-g99"
    )
    return {
        "acme": acme,
        "zenith": zenith,
        "model": phone,
        "variant": variant,
        "chipset": chipset,
    }


def pick(page: Page, input_id: str, text: str, option: str) -> None:
    """Type into a combobox and choose the option whose title is ``option``."""
    field = page.locator(f"#{input_id}")
    field.click()
    field.fill("")
    page.keyboard.type(text)
    results = page.locator(f"#{input_id}-results")
    page.wait_for_function(
        "([id, wanted]) => [...document.querySelectorAll(`#${id}-results [role=option]`)]"
        ".some(node => node.textContent.includes(wanted))",
        arg=[input_id, option],
        timeout=8_000,
    )
    results.get_by_role("option").filter(has_text=option).first.click()


def option_titles(page: Page, input_id: str) -> list[str]:
    return page.locator(f"#{input_id}-results [role=option]").all_inner_texts()


def open_page(page: Page, live_server, path: str) -> None:
    page.goto(f"{live_server.url}{path}", wait_until="load")
    ready(page)
