"""My files at /files/: library, uploads, quota, filters, pagination and access control."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AppRegistry
from apps.firmwares.access import record_served_download
from apps.firmwares.models import (
    Brand,
    Model,
    OfficialFirmware,
    ServedDownload,
    Variant,
)
from apps.shop import entitlements, services
from apps.shop.models import PackageTier, WindowKind

pytestmark = pytest.mark.django_db

PASSWORD = "Pass-12345!"


def text(response: Any) -> str:
    return response.content.decode()


@pytest.fixture
def make_user(django_user_model: Any) -> Callable[..., Any]:
    def build(username: str) -> Any:
        return django_user_model.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=PASSWORD,
            email_verified_at=timezone.now(),
        )

    return build


@pytest.fixture
def owner(make_user: Callable[..., Any]) -> Any:
    return make_user("library_owner")


@pytest.fixture
def stranger(make_user: Callable[..., Any]) -> Any:
    return make_user("library_stranger")


@pytest.fixture
def owner_client(owner: Any) -> Client:
    client = Client()
    client.force_login(owner)
    return client


@pytest.fixture
def default_tier() -> PackageTier:
    return PackageTier.objects.create(
        name="Free",
        slug="free",
        is_default=True,
        duration_days=0,
        window=WindowKind.DAY,
        limit_files=5,
        fair_monthly_files=20,
        applies_to_paid=True,
    )


@pytest.fixture
def make_device() -> Callable[..., tuple[Brand, Model, Variant]]:
    def build(brand_name: str = "Acme") -> tuple[Brand, Model, Variant]:
        brand, _ = Brand.objects.get_or_create(
            slug=brand_name.lower(), defaults={"name": brand_name}
        )
        model, _ = Model.objects.get_or_create(
            brand=brand, slug="phone-one", defaults={"name": "Phone One"}
        )
        variant, _ = Variant.objects.get_or_create(
            model=model, slug="global", defaults={"name": "Global"}
        )
        return brand, model, variant

    return build


@pytest.fixture
def make_served(
    make_device: Callable[..., tuple[Brand, Model, Variant]],
) -> Callable[..., OfficialFirmware]:
    """Create a firmware file and record it as served to ``user``."""
    counter = iter(range(1, 1000))

    def build(
        user: Any,
        *,
        name: str | None = None,
        tier: str = "free",
        price: str | None = None,
        charged: bool = False,
        brand: str = "Acme",
        served_at: datetime | None = None,
        is_active: bool = True,
    ) -> OfficialFirmware:
        brand_row, model, variant = make_device(brand)
        number = next(counter)
        file_name = name or f"served-{number}.zip"
        firmware = OfficialFirmware.objects.create(
            original_file_name=file_name,
            stored_file_path=f"/firmware/{file_name}",
            brand=brand_row,
            model=model,
            variant=variant,
            is_verified=True,
            is_active=is_active,
            access_tier=tier,
            approved_price=Decimal(price) if price else None,
        )
        row = record_served_download(user, firmware, tier=tier, charged_quota=charged)
        assert row is not None
        if served_at is not None:
            ServedDownload.objects.filter(pk=row.pk).update(
                served_at=served_at, last_served_at=served_at
            )
        return firmware

    return build


class TestRoutes:
    def test_urls_live_under_files(self):
        assert reverse("library:my_files") == "/files/"
        assert reverse("library:my_uploads") == "/files/uploads/"
        assert reverse("library:my_quota") == "/files/quota/"

    def test_personal_storage_no_longer_exists(self, owner_client):
        for path in ("/files/storage/", "/files/s/abc/"):
            assert owner_client.get(path).status_code == 404, path

    def test_pages_require_login(self, client):
        for name in ("my_files", "my_uploads", "my_quota"):
            response = client.get(reverse(f"library:{name}"))
            assert response.status_code == 302, name

    def test_nav_labels_the_link_my_files(self, owner_client):
        page = text(owner_client.get(reverse("library:my_files")))

        assert 'href="/files/"' in page
        assert ">My files</a>" in page

    def test_tabs_offer_library_uploads_and_quota_only(self, owner_client):
        for name in ("my_files", "my_uploads", "my_quota"):
            page = text(owner_client.get(reverse(f"library:{name}")))
            assert "Personal storage" not in page, name
            assert "/files/storage/" not in page, name
            assert 'href="/files/quota/"' in page, name


class TestLibrary:
    def test_lists_every_served_tier_with_badges(
        self, owner, owner_client, default_tier, make_served
    ):
        make_served(owner, name="free.zip")
        make_served(owner, tier="paid", price="7.50", charged=True, name="paid.zip")
        make_served(owner, tier="featured", charged=True, name="featured.zip")

        page = text(owner_client.get(reverse("library:my_files")))

        for name in ("free.zip", "paid.zip", "featured.zip"):
            assert name in page
        assert "Paid · counted in quota" in page
        assert "Featured · counted in quota" in page
        assert "USD 7.50" in page

    def test_redownload_goes_through_the_download_gate_never_a_file_url(
        self, owner, owner_client, default_tier, make_served
    ):
        firmware = make_served(owner, tier="paid", price="7.50", charged=True)

        page = text(owner_client.get(reverse("library:my_files")))

        gate = reverse(
            "firmwares:firmware_download",
            kwargs={"firmware_type": "official", "firmware_id": firmware.pk},
        )
        assert f'href="{gate}"' in page
        assert "/media/" not in page
        assert firmware.stored_file_path not in page

    def test_unpublished_files_have_no_download_button(
        self, owner, owner_client, default_tier, make_served
    ):
        make_served(owner, name="gone.zip", is_active=False)

        page = text(owner_client.get(reverse("library:my_files")))

        assert "gone.zip" in page
        assert "No longer available" in page
        assert "Download again" not in page

    def test_empty_library_says_so(self, owner_client, default_tier):
        page = text(owner_client.get(reverse("library:my_files")))

        assert "Nothing in your library yet" in page

    def test_idor_other_users_files_never_appear(
        self, owner, stranger, owner_client, default_tier, make_served
    ):
        make_served(stranger, name="strangers-secret.zip", tier="paid", price="9.00")
        make_served(owner, name="mine.zip")

        page = text(owner_client.get(reverse("library:my_files")))
        filtered = text(owner_client.get(reverse("library:my_files"), {"q": "secret"}))

        assert "mine.zip" in page
        assert "strangers-secret" not in page
        assert "strangers-secret" not in filtered
        assert "No files match" in filtered

    def test_filters_by_tier_brand_search_and_date(
        self, owner, owner_client, default_tier, make_served
    ):
        old = timezone.now() - timedelta(days=40)
        make_served(owner, name="alpha-free.zip", brand="Acme")
        make_served(
            owner,
            name="beta-paid.zip",
            brand="Zeta",
            tier="paid",
            price="5.00",
            charged=True,
        )
        make_served(owner, name="gamma-old.zip", brand="Acme", served_at=old)
        url = reverse("library:my_files")

        by_tier = text(owner_client.get(url, {"tier": "paid"}))
        by_brand = text(owner_client.get(url, {"brand": "zeta"}))
        by_search = text(owner_client.get(url, {"q": "GAMMA"}))
        recent = text(
            owner_client.get(
                url, {"from": (timezone.now() - timedelta(days=7)).date().isoformat()}
            )
        )
        ancient = text(
            owner_client.get(
                url, {"to": (timezone.now() - timedelta(days=30)).date().isoformat()}
            )
        )

        assert "beta-paid.zip" in by_tier and "alpha-free.zip" not in by_tier
        assert "beta-paid.zip" in by_brand and "alpha-free.zip" not in by_brand
        assert "gamma-old.zip" in by_search and "alpha-free.zip" not in by_search
        assert "alpha-free.zip" in recent and "gamma-old.zip" not in recent
        assert "gamma-old.zip" in ancient and "alpha-free.zip" not in ancient

    def test_invalid_filter_values_are_ignored(
        self, owner, owner_client, default_tier, make_served
    ):
        make_served(owner, name="kept.zip")

        response = owner_client.get(
            reverse("library:my_files"),
            {
                "tier": "<script>",
                "from": "not-a-date",
                "to": "2026-99-99",
                "q": "x" * 500,
            },
        )

        assert response.status_code == 200
        assert "<script>" not in text(response).replace("<script src", "")

    def test_htmx_requests_get_only_the_results_fragment(
        self, owner, owner_client, default_tier, make_served
    ):
        make_served(owner, name="fragment.zip")

        response = owner_client.get(reverse("library:my_files"), HTTP_HX_REQUEST="true")

        body = text(response)
        assert "fragment.zip" in body
        assert "<html" not in body
        assert "HX-Request" in response["Vary"]

    def test_pagination_preserves_filters_and_uses_htmx(
        self, owner, owner_client, default_tier, make_served
    ):
        for number in range(25):
            make_served(owner, name=f"bulk-{number:02d}.zip", tier="free")
        make_served(owner, name="paid-one.zip", tier="paid", price="5.00", charged=True)

        page = text(owner_client.get(reverse("library:my_files"), {"tier": "free"}))
        second = text(
            owner_client.get(reverse("library:my_files"), {"tier": "free", "page": 2})
        )

        assert "tier=free" in page and "page=2" in page
        assert 'hx-target="#library-results"' in page
        assert "paid-one.zip" not in page and "paid-one.zip" not in second
        shown_on_second = {
            f"bulk-{number:02d}.zip"
            for number in range(25)
            if f"bulk-{number:02d}.zip" in second
        }
        shown_on_first = {
            f"bulk-{number:02d}.zip"
            for number in range(25)
            if f"bulk-{number:02d}.zip" in page
        }
        assert len(shown_on_first) == 20
        assert len(shown_on_second) == 5
        assert not shown_on_first & shown_on_second
        assert "Page 2 of 2" in second

    def test_library_reads_the_single_served_record(
        self, owner, owner_client, default_tier, make_served
    ):
        make_served(owner, name="one.zip")
        assert ServedDownload.objects.filter(user=owner).count() == 1

        page = text(owner_client.get(reverse("library:my_files")))

        assert "one.zip" in page


class TestQuotaPanel:
    def test_library_shows_the_meter_with_accessible_text_and_reset(
        self, owner, owner_client, default_tier, make_served
    ):
        firmware = make_served(owner)
        entitlements.consume_quota(owner, firmware, reason="free")

        page = text(owner_client.get(reverse("library:my_files")))

        assert "<meter" in page
        assert "1 of 5 used today" in page
        assert "4</strong> left of 5" in page
        assert "resets" in page
        assert "Free" in page
        assert reverse("shop:pricing") in page

    def test_quota_page_shows_the_allowance_and_fair_usage_and_counts_paid_files(
        self, owner, owner_client, default_tier, make_user, make_served
    ):
        staff = make_user("quota_staff")
        paid = make_served(owner, tier="paid", price="4.00", charged=True)
        services.mark_order_paid(
            entitlements.request_purchase(owner, paid).pk, staff_user=staff
        )
        entitlements.consume_quota(owner, paid, reason="paid")

        page = text(owner_client.get(reverse("library:my_quota")))

        assert "New files today" in page
        assert "Fair use · files this month" in page
        assert "1 of 5 used today" in page
        assert "0 of 20 used this month" in page
        assert "5 files per day" in page

    def test_purchased_but_unserved_files_are_listed_as_available(
        self, owner, owner_client, default_tier, make_user, make_device
    ):
        staff = make_user("purchase_staff")
        brand, model, variant = make_device()
        firmware = OfficialFirmware.objects.create(
            original_file_name="waiting.zip",
            stored_file_path="/firmware/waiting.zip",
            brand=brand,
            model=model,
            variant=variant,
            is_verified=True,
            is_active=True,
            access_tier="paid",
            approved_price=Decimal("6.00"),
        )
        services.mark_order_paid(
            entitlements.request_purchase(owner, firmware).pk, staff_user=staff
        )

        quota = text(owner_client.get(reverse("library:my_quota")))
        library = text(owner_client.get(reverse("library:my_files")))

        for page in (quota, library):
            assert "Purchased — download available" in page
            assert "waiting.zip" in page

    def test_unlimited_package_is_described_as_unlimited(
        self, owner, owner_client, default_tier
    ):
        tier = PackageTier.objects.create(
            name="Pro", slug="pro", price=Decimal(9), bandwidth_enabled=False
        )
        services.activate_package(owner, tier)

        page = text(owner_client.get(reverse("library:my_quota")))

        assert "Pro" in page
        assert "Unlimited" in page

    def test_quota_isolated_per_user(
        self, owner, stranger, owner_client, default_tier, make_served
    ):
        entitlements.consume_quota(stranger, make_served(stranger), reason="free")

        page = text(owner_client.get(reverse("library:my_quota")))

        assert "0 of 5 used today" in page

    def test_quota_page_without_the_shop_says_unavailable(
        self, owner_client, default_tier
    ):
        registry = AppRegistry.get_solo()
        registry.shop_enabled = False
        registry.save()

        response = owner_client.get(reverse("library:my_quota"))

        assert response.status_code == 200
        assert "Download quotas are not available" in text(response)
        assert "<meter" not in text(response)

    def test_without_the_firmware_app_the_library_and_uploads_are_empty(
        self, owner, owner_client, default_tier, make_served
    ):
        make_served(owner, name="hidden.zip")
        registry = AppRegistry.get_solo()
        registry.firmwares_enabled = False
        registry.save()

        library = text(owner_client.get(reverse("library:my_files")))
        uploads = text(owner_client.get(reverse("library:my_uploads")))

        assert "hidden.zip" not in library
        assert "Nothing in your library yet" in library
        assert "You have not uploaded any firmware" in uploads
