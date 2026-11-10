"""My files library: Active and History tabs follow the download lifetime rules."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.firmwares.access import (
    PACKAGE_EXPIRED_MESSAGE,
    check_download_access,
    finalize_served_download,
)
from apps.firmwares.models import (
    Brand,
    Model,
    OfficialFirmware,
    ServedDownload,
    Variant,
)
from apps.firmwares.package_history import purge_superseded_package_history
from apps.shop import services
from apps.shop.models import PackageTier, ShopSettings, UserPackage

pytestmark = pytest.mark.django_db


@pytest.fixture(name="member")
def member_fixture(django_user_model) -> Any:
    return django_user_model.objects.create_user(
        username="holder", email="holder@example.com", email_verified_at=timezone.now()
    )


@pytest.fixture(name="member_client")
def member_client_fixture(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture(name="gold")
def gold_fixture() -> PackageTier:
    return PackageTier.objects.create(
        name="Gold",
        slug="gold",
        price=Decimal("9.00"),
        duration_days=30,
        bandwidth_enabled=False,
        limit_devices=False,
        applies_to_featured=True,
    )


@pytest.fixture(name="make_file")
def make_file_fixture() -> Any:
    brand = Brand.objects.create(name="Acme", slug="acme")
    model = Model.objects.create(brand=brand, name="Phone One", slug="phone-one")
    variant = Variant.objects.create(model=model, name="Global", slug="global")
    counter = iter(range(1, 1000))

    def build(tier: str = "free") -> OfficialFirmware:
        number = next(counter)
        return OfficialFirmware.objects.create(
            original_file_name=f"lib-{number}.zip",
            stored_file_path=f"/firmware/lib-{number}.zip",
            brand=brand,
            model=model,
            variant=variant,
            is_verified=True,
            is_active=True,
            access_tier=tier,
        )

    return build


def page(client: Client, tab: str = "") -> str:
    url = reverse("library:my_files")
    response = client.get(url, {"tab": tab} if tab else {})
    assert response.status_code == 200
    return response.content.decode()


def serve(user: Any, firmware: OfficialFirmware) -> None:
    assert check_download_access(user, firmware).allowed
    finalize_served_download(user, firmware)


def expire(user: Any) -> None:
    UserPackage.objects.filter(user=user, is_baseline=False).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )


def test_an_expired_package_moves_its_files_to_a_read_only_history(
    member, member_client, gold, make_file
) -> None:
    services.activate_package(member, gold)
    featured, free = make_file("featured"), make_file()
    serve(member, featured)
    serve(member, free)
    active = page(member_client)
    assert featured.original_file_name in active
    assert "Download again" in active

    expire(member)

    active = page(member_client)
    history = page(member_client, "history")
    assert featured.original_file_name not in active
    assert free.original_file_name in active
    assert featured.original_file_name in history
    assert "Download again" not in history
    assert PACKAGE_EXPIRED_MESSAGE in history
    assert "data-renew-package" in history
    assert reverse("shop:pricing") in history
    assert not check_download_access(member, featured).allowed


def test_the_tabs_show_their_counts(member, member_client, gold, make_file) -> None:
    services.activate_package(member, gold)
    serve(member, make_file("featured"))
    serve(member, make_file())
    expire(member)

    content = page(member_client)

    assert 'data-library-tab="active"' in content
    assert 'data-library-tab="history"' in content
    assert content.count('<span class="badge text-bg-light">1</span>') == 2


def test_renewing_starts_a_new_history_and_purges_the_old_one(
    member, member_client, gold, make_file, django_capture_on_commit_callbacks
) -> None:
    services.activate_package(member, gold)
    featured, free = make_file("featured"), make_file()
    serve(member, featured)
    serve(member, free)
    expire(member)

    with django_capture_on_commit_callbacks(execute=True):
        services.activate_package(member, gold)

    history = page(member_client, "history")
    active = page(member_client)
    assert featured.original_file_name not in history
    assert featured.original_file_name not in active
    assert free.original_file_name in active
    assert ServedDownload.objects.filter(user=member).count() == 1
    assert check_download_access(member, featured).allowed
    serve(member, featured)
    assert featured.original_file_name in page(member_client)


def test_a_spent_free_allowance_lands_in_the_history(
    member, member_client, make_file
) -> None:
    ShopSettings.objects.update_or_create(
        pk=1, defaults={"free_redownload_window_days": 30, "free_redownload_max": 5}
    )
    firmware = make_file()
    serve(member, firmware)
    ServedDownload.objects.update(served_at=timezone.now() - timedelta(days=31))

    assert firmware.original_file_name not in page(member_client)
    history = page(member_client, "history")
    assert firmware.original_file_name in history
    assert "re-download allowance" in history
    assert "data-renew-package" not in history
    assert purge_superseded_package_history() == 0
    assert ServedDownload.objects.count() == 1
