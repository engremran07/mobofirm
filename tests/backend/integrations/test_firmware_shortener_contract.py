"""Contract of the firmware link shortener with the rest of the platform."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from apps.core.app_service import AppService
from apps.firmwares.models import (
    Brand,
    FirmwareSourceType,
    Model,
    OfficialFirmware,
)
from apps.firmwares.shortener_constants import ShortenerMode
from apps.firmwares.shortener_models import ShortenerSettings

pytestmark = pytest.mark.django_db

FIRMWARES = Path(__file__).resolve().parents[3] / "apps" / "firmwares"


@pytest.fixture(name="firmware")
def firmware_fixture():
    brand = Brand.objects.create(name="Contract", slug="contract")
    device = Model.objects.create(brand=brand, name="Phone", slug="phone")
    return OfficialFirmware.objects.create(
        original_file_name="c.zip",
        source_type=FirmwareSourceType.EXTERNAL,
        stored_file_path="",
        external_url="https://downloads.example.invalid/c.zip",
        brand=brand,
        model=device,
        is_verified=True,
    )


def test_the_contract_is_exported_through_app_service(firmware, monkeypatch):
    from apps.firmwares import shortener

    monkeypatch.setattr(
        shortener, "absolute_url", lambda path, request=None: f"https://site.test{path}"
    )
    export = AppService.export("firmwares", "shorten_download_url")

    assert callable(export)
    plain = export(firmware)
    assert plain.startswith("https://site.test/firmwares/file/official/")
    row = ShortenerSettings.get_solo()
    row.mode = ShortenerMode.FIRST_PARTY
    row.save()
    short = export(firmware)
    assert short.startswith("https://site.test/d/") and short.endswith("/")


def test_the_contract_never_returns_the_file_host_address(firmware, monkeypatch):
    from apps.firmwares import shortener

    monkeypatch.setattr(
        shortener, "absolute_url", lambda path, request=None: f"https://site.test{path}"
    )
    export = AppService.export("firmwares", "shorten_download_url")
    assert callable(export)
    for mode in ShortenerMode:
        row = ShortenerSettings.get_solo()
        row.mode = ShortenerMode.FIRST_PARTY if mode != ShortenerMode.OFF else mode
        row.save()
        assert "example.invalid" not in export(firmware)


def test_the_shortener_modules_do_not_import_sibling_apps_beyond_the_fetcher():
    allowed = {"apps.seo.fetcher", "apps.site_settings.services"}
    offenders: list[str] = []
    for path in FIRMWARES.glob("shortener*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            offenders += [
                f"{path.name}: {name}"
                for name in names
                if name.startswith("apps.")
                and not name.startswith("apps.firmwares")
                and name not in allowed
            ]

    assert offenders == []


def test_providers_use_the_ssrf_safe_fetcher_only():
    source = (FIRMWARES / "shortener_providers.py").read_text(encoding="utf-8")

    assert "apps.seo.fetcher" in source
    for banned in ("import requests", "urllib.request", "import httpx", "http.client"):
        assert banned not in source


def test_real_package_perks_decide_who_is_shortened(firmware, django_user_model):
    from decimal import Decimal

    from apps.firmwares import shortener
    from apps.shop import services
    from apps.shop.models import PackageTier, WindowKind

    PackageTier.objects.create(
        name="Free",
        slug="free",
        is_default=True,
        duration_days=0,
        window=WindowKind.MONTH,
        limit_files=20,
        device_limit=3,
    )
    ad_free_only = PackageTier.objects.create(
        name="Bronze",
        slug="bronze",
        price=Decimal("3.00"),
        duration_days=30,
        window=WindowKind.PACKAGE,
        limit_files=50,
        device_limit=5,
        ad_free=True,
    )
    silver = PackageTier.objects.create(
        name="Silver",
        slug="silver",
        price=Decimal("9.00"),
        duration_days=30,
        window=WindowKind.PACKAGE,
        limit_files=100,
        device_limit=10,
        ad_free=True,
        direct_links=True,
    )
    users = {
        name: django_user_model.objects.create_user(
            username=name, email=f"{name}@example.test"
        )
        for name in ("free", "bronze", "silver")
    }
    services.activate_package(users["bronze"], ad_free_only)
    services.activate_package(users["silver"], silver)

    assert shortener.should_shorten(users["free"], firmware) is True
    assert shortener.should_shorten(users["bronze"], firmware) is True
    assert shortener.should_shorten(users["silver"], firmware) is False
