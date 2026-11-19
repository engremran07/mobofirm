"""Who is routed through the shortener: the package model decides (``should_shorten``)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import Client
from django.urls import reverse

from apps.firmwares import access, shortener, shortener_providers
from apps.firmwares.models import (
    Brand,
    FirmwareSourceType,
    Model,
    OfficialFirmware,
)
from apps.firmwares.shortener_constants import ShortenerMode
from apps.firmwares.shortener_models import DownloadShortLink, ShortenerSettings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    def refuse(timeout):
        raise AssertionError("A test tried to build the real SafeFetcher.")

    monkeypatch.setattr(shortener_providers, "default_fetcher", refuse)
    real = shortener.absolute_url
    monkeypatch.setattr(
        shortener,
        "absolute_url",
        lambda path, request=None: (
            real(path, request) if request is not None else f"http://testserver{path}"
        ),
    )


@pytest.fixture(name="firmware")
def firmware_fixture():
    brand = Brand.objects.create(name="Audience", slug="audience")
    device = Model.objects.create(brand=brand, name="Phone", slug="phone")
    row = ShortenerSettings.get_solo()
    row.mode = ShortenerMode.FIRST_PARTY
    row.save()
    return OfficialFirmware.objects.create(
        original_file_name="aud.zip",
        source_type=FirmwareSourceType.EXTERNAL,
        stored_file_path="",
        external_url="https://downloads.example.invalid/aud.zip",
        brand=brand,
        model=device,
        is_verified=True,
    )


@pytest.fixture(name="package")
def package_fixture(shop):
    """``package(user, direct_links=..., ad_free=...)``: that user's perks.

    Builds on the shared fake shop (quota, featured access) and adds the one perk the
    shortener gate reads.
    """
    perks_by_user: dict[int, dict[str, bool]] = {}
    base = shop.effective_entitlements

    def resolve(user):
        entitlements = base(user)
        for name, value in perks_by_user.get(user.pk, {}).items():
            setattr(entitlements.perks, name, value)
        return entitlements

    shop.effective_entitlements = resolve

    def grant(user, **perks):
        perks_by_user[user.pk] = perks

    return grant


def detail(firmware) -> str:
    return reverse(
        "firmwares:firmware_detail",
        kwargs={"firmware_type": "official", "firmware_id": firmware.pk},
    )


@pytest.mark.usefixtures("package")
def test_anonymous_visitors_are_shortened(firmware):
    assert shortener.should_shorten(AnonymousUser(), firmware) is True
    assert shortener.should_shorten(None, firmware) is True


def test_the_free_tier_is_shortened(firmware, package, make_user):
    free = make_user("free-member")

    assert shortener.should_shorten(free, firmware) is True
    package(free, direct_links=False, ad_free=False)
    assert shortener.should_shorten(free, firmware) is True


def test_a_package_with_direct_links_gets_the_plain_link(firmware, package, make_user):
    silver = make_user("silver-member")
    package(silver, direct_links=True, ad_free=True)

    assert shortener.should_shorten(silver, firmware) is False


def test_ad_free_alone_does_not_skip_the_shortener(firmware, package, make_user):
    member = make_user("ad-free-member")
    package(member, direct_links=False, ad_free=True)

    assert shortener.should_shorten(member, firmware) is True


def test_without_the_shop_everybody_is_the_free_tier(
    firmware, optional_modules, make_user
):
    optional_modules[access.ENTITLEMENTS_MODULE] = None

    assert shortener.should_shorten(make_user("no-shop"), firmware) is True


def test_the_contract_honours_the_user_argument(firmware, package, make_user):
    silver = make_user("silver-contract")
    package(silver, direct_links=True)

    plain = shortener.shorten_download_url(firmware, user=silver)
    short = shortener.shorten_download_url(firmware)

    assert "/d/" not in plain and plain.endswith("/download/")
    assert "/d/" in short
    assert DownloadShortLink.objects.count() == 1  # only the anonymous call made one


def test_pages_route_each_viewer_by_package(firmware, package, make_user):
    silver = make_user("silver-page")
    free = make_user("free-page")
    package(silver, direct_links=True)
    package(free, direct_links=False)

    def share_url(user) -> str:
        client = Client()
        if user is not None:
            client.force_login(user)
        response = client.get(detail(firmware))
        assert response.status_code == 200
        return response.context["share_url"]

    assert "/d/" in share_url(None)
    assert "/d/" in share_url(free)
    assert share_url(silver) == "http://testserver" + detail(firmware)
    assert DownloadShortLink.objects.count() == 1


def test_a_direct_links_viewer_never_triggers_a_provider_call(
    firmware, package, make_user
):
    row = ShortenerSettings.get_solo()
    row.mode = ShortenerMode.BOTH
    row.save()
    silver = make_user("silver-both")
    package(silver, direct_links=True)

    result = shortener.shorten_download(firmware, user=silver)

    assert (result.shortened, result.mode) == (False, ShortenerMode.OFF)
    assert DownloadShortLink.objects.count() == 0
