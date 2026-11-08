"""Download access tiers: decisions, gate enforcement and the served-file record."""

from __future__ import annotations

import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.firmwares import access
from apps.firmwares.access import (
    check_download_access,
    finalize_served_download,
    record_served_download,
)
from apps.firmwares.download_service import (
    consume_download_token,
    create_download_token,
    generate_signed_url,
    sign_token,
)
from apps.firmwares.models import (
    AccessTier,
    DownloadSession,
    OfficialFirmware,
    ServedDownload,
)

PRICE = Decimal("4.50")
GATE_OFF = {
    "FIRMWARE_DOWNLOAD_GATE_ENABLED": False,
    "FIRMWARE_DOWNLOAD_HOTLINK_PROTECTION": False,
    "FIRMWARE_DOWNLOAD_REQUIRE_LOGIN": False,
    "FIRMWARE_DOWNLOAD_MAX_PER_DAY": 0,
    "DEBUG": True,
}


@pytest.fixture(name="storage")
def storage_fixture(settings):
    with tempfile.TemporaryDirectory() as directory:
        settings.FIRMWARE_STORAGE_ROOT = Path(directory)
        for name, value in GATE_OFF.items():
            setattr(settings, name, value)
        yield Path(directory)


@pytest.fixture(name="make_firmware")
def make_firmware_fixture(catalog, storage):
    def factory(name="file.zip", *, tier=AccessTier.FREE, price=None, **extra):
        path = storage / name
        path.write_bytes(b"firmware-bytes")
        values = {
            "original_file_name": name,
            "stored_file_path": str(path),
            "brand": catalog.brand,
            "model": catalog.model,
            "variant": catalog.variant,
            "is_verified": True,
            "file_size": 14,
            "access_tier": tier,
            "approved_price": price,
        }
        values.update(extra)
        return OfficialFirmware.objects.create(**values)

    return factory


def paths(firmware):
    kwargs = {"firmware_type": "official", "firmware_id": str(firmware.pk)}
    return {
        "detail": reverse("firmwares:firmware_detail", kwargs=kwargs),
        "page": reverse("firmwares:firmware_download", kwargs=kwargs),
        "start": reverse("firmwares:firmware_download_start", kwargs=kwargs),
    }


def client_for(user):
    client = Client()
    client.force_login(user)
    return client


# --------------------------------------------------------------------------- decisions


@pytest.mark.django_db
class TestDecisions:
    @pytest.mark.usefixtures("shop")
    def test_free_files_are_open_to_anonymous_visitors(self, make_firmware):
        decision = check_download_access(None, make_firmware())

        assert decision.allowed
        assert decision.requires is None

    def test_free_files_need_the_allowance_of_signed_in_users(
        self, make_firmware, make_user, shop
    ):
        user = make_user("free-user")
        firmware = make_firmware()
        assert check_download_access(user, firmware).allowed

        shop.exhausted_users.add(user.pk)
        decision = check_download_access(user, firmware)

        assert not decision.allowed
        assert decision.requires == "package"
        assert decision.reason == "Quota used up."

    @pytest.mark.usefixtures("shop")
    def test_paid_files_ask_anonymous_visitors_to_sign_in(self, make_firmware):
        decision = check_download_access(
            None, make_firmware(tier=AccessTier.PAID, price=PRICE)
        )

        assert (decision.allowed, decision.requires) == (False, "login")
        assert decision.price == PRICE

    def test_paid_files_require_a_purchase_for_that_exact_file(
        self, make_firmware, make_user, shop
    ):
        user = make_user("buyer")
        paid = make_firmware("paid.zip", tier=AccessTier.PAID, price=PRICE)
        other = make_firmware("other.zip", tier=AccessTier.PAID, price=PRICE)

        locked = check_download_access(user, paid)
        shop.purchased.add((user.pk, paid.pk))

        assert (locked.allowed, locked.requires, locked.price) == (
            False,
            "purchase",
            PRICE,
        )
        assert check_download_access(user, paid).allowed
        assert not check_download_access(user, other).allowed

    @pytest.mark.usefixtures("shop")
    def test_a_paid_file_without_an_approved_price_cannot_be_bought(
        self, make_firmware, make_user
    ):
        decision = check_download_access(
            make_user("early"), make_firmware(tier=AccessTier.PAID, price=None)
        )

        assert (decision.allowed, decision.requires, decision.price) == (
            False,
            "purchase",
            None,
        )

    def test_featured_files_need_featured_access(self, make_firmware, make_user, shop):
        member, visitor = make_user("member"), make_user("visitor")
        shop.featured_users.add(member.pk)
        featured = make_firmware(tier=AccessTier.FEATURED)

        assert check_download_access(member, featured).allowed
        denied = check_download_access(visitor, featured)
        assert (denied.allowed, denied.requires) == (False, "package")
        assert check_download_access(None, featured).requires == "login"

    def test_without_the_shop_paid_and_featured_files_stay_locked(
        self, make_firmware, make_user, optional_modules
    ):
        optional_modules[access.ENTITLEMENTS_MODULE] = None
        user = make_user("no-shop")

        paid = check_download_access(
            user, make_firmware("p.zip", tier=AccessTier.PAID, price=PRICE)
        )
        featured = check_download_access(
            user, make_firmware("f.zip", tier=AccessTier.FEATURED)
        )
        free = check_download_access(user, make_firmware("z.zip"))

        assert not paid.allowed and not featured.allowed
        assert free.allowed

    @pytest.mark.usefixtures("shop")
    def test_a_file_already_served_stays_downloadable(self, make_firmware, make_user):
        user = make_user("returning")
        paid = make_firmware(tier=AccessTier.PAID, price=PRICE)
        record_served_download(user, paid, tier=AccessTier.PAID, charged_quota=True)

        decision = check_download_access(user, paid)

        assert decision.allowed and decision.already_served

    def test_the_shop_module_is_optional_and_only_its_absence_is_tolerated(
        self, monkeypatch
    ):
        assert access.optional_module("apps.shop.entitlements_missing_probe") is None
        monkeypatch.setattr(
            access,
            "import_module",
            lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name="other.dep")),
        )
        with pytest.raises(ModuleNotFoundError):
            access.optional_module("apps.shop.entitlements")

    def test_sellers_are_recognised_through_the_marketplace_seam(
        self, make_user, sellers, optional_modules
    ):
        seller, buyer = make_user("seller"), make_user("buyer2")
        sellers.add(seller.pk)

        assert access.is_seller(seller)
        assert not access.is_seller(buyer)
        assert not access.is_seller(None)
        optional_modules[access.SELLER_MODULE] = None
        assert not access.is_seller(seller)


# --------------------------------------------------------------------------- served downloads


@pytest.mark.django_db
class TestServedDownload:
    def test_recording_is_idempotent_and_never_charges_twice(
        self, make_firmware, make_user
    ):
        user = make_user("reader")
        firmware = make_firmware(tier=AccessTier.PAID, price=PRICE)

        first = record_served_download(
            user, firmware, tier=AccessTier.PAID, charged_quota=True
        )
        second = record_served_download(
            user, firmware, tier=AccessTier.PAID, charged_quota=False
        )

        assert first is not None and second is not None
        assert ServedDownload.objects.count() == 1
        row = ServedDownload.objects.get()
        assert (row.times_served, row.paid_amount, row.charged_quota) == (
            2,
            PRICE,
            True,
        )
        assert row.content_type == ContentType.objects.get_for_model(OfficialFirmware)

    def test_anonymous_downloads_are_not_recorded(self, make_firmware):
        assert (
            record_served_download(
                None, make_firmware(), tier=AccessTier.FREE, charged_quota=False
            )
            is None
        )
        assert not ServedDownload.objects.exists()

    def test_finalizing_charges_the_allowance_once(
        self, make_firmware, make_user, shop
    ):
        user = make_user("charged")
        firmware = make_firmware()

        finalize_served_download(user, firmware)
        finalize_served_download(user, firmware)

        assert shop.consumed == [(user.pk, firmware.pk, "free")]
        assert ServedDownload.objects.get().charged_quota

    def test_a_failed_charge_records_nothing(self, make_firmware, make_user, shop):
        shop.fail_consume = True

        with pytest.raises(PermissionDenied):
            finalize_served_download(make_user("broke"), make_firmware())

        assert not ServedDownload.objects.exists()

    def test_without_the_shop_nothing_is_charged_but_the_file_is_recorded(
        self, make_firmware, make_user, optional_modules
    ):
        optional_modules[access.ENTITLEMENTS_MODULE] = None

        row = finalize_served_download(make_user("plain"), make_firmware())

        assert row is not None and not row.charged_quota


# --------------------------------------------------------------------------- the gate


@pytest.mark.django_db
class TestDownloadGate:
    @pytest.mark.usefixtures("shop")
    def test_a_locked_file_never_issues_a_token(self, make_firmware, make_user):
        firmware = make_firmware(tier=AccessTier.PAID, price=PRICE)
        client = client_for(make_user("locked"))

        response = client.get(paths(firmware)["page"])

        assert response.status_code == 302
        assert response.url == paths(firmware)["detail"]
        from apps.firmwares.models import DownloadToken

        assert not DownloadToken.objects.exists()

    def test_the_start_endpoint_re_checks_access_after_a_token_was_issued(
        self, make_firmware, make_user, shop
    ):
        user = make_user("revoked")
        firmware = make_firmware(tier=AccessTier.PAID, price=PRICE)
        shop.purchased.add((user.pk, firmware.pk))
        client = client_for(user)
        page = client.get(paths(firmware)["page"])
        shop.purchased.clear()

        response = client.post(
            paths(firmware)["start"],
            {
                "token": page.context["download_token"],
                "sig": page.context["download_signature"],
            },
        )

        assert response.status_code == 302
        assert response.url == paths(firmware)["detail"]
        assert not ServedDownload.objects.exists()

    def test_the_signed_local_link_re_checks_access_before_serving(
        self, make_firmware, make_user, shop, rf
    ):
        user = make_user("serve-denied")
        firmware = make_firmware(tier=AccessTier.PAID, price=PRICE)
        shop.purchased.add((user.pk, firmware.pk))
        request = rf.get("/")
        request.user = user
        token = create_download_token(firmware, request)
        consume_download_token(token.token, firmware, request)
        link = generate_signed_url(firmware, token)
        shop.purchased.clear()

        denied = client_for(user).get(link)
        shop.purchased.add((user.pk, firmware.pk))
        served = client_for(user).get(link)
        served.close()

        assert denied.status_code == 403
        assert served.status_code == 200
        assert ServedDownload.objects.filter(user=user).count() == 1

    def test_a_purchaser_downloads_end_to_end_and_it_is_recorded(
        self, make_firmware, make_user, shop
    ):
        user = make_user("full-flow")
        firmware = make_firmware(tier=AccessTier.PAID, price=PRICE)
        shop.purchased.add((user.pk, firmware.pk))
        client = client_for(user)
        page = client.get(paths(firmware)["page"])

        start = client.post(
            paths(firmware)["start"],
            {
                "token": page.context["download_token"],
                "sig": sign_token(page.context["download_token"]),
            },
        )
        served = client.get(start.url)
        served.close()

        assert served.status_code == 200
        row = ServedDownload.objects.get()
        assert (row.user, row.tier, row.paid_amount, row.charged_quota) == (
            user,
            "paid",
            PRICE,
            True,
        )
        assert shop.consumed == [(user.pk, firmware.pk, "paid")]

    @pytest.mark.usefixtures("storage")
    def test_an_external_redirect_is_gated_and_recorded(
        self, make_user, shop, settings
    ):
        settings.FIRMWARE_EXTERNAL_URL_ALLOWED_HOSTS = ["downloads.example.invalid"]
        user = make_user("external")
        firmware = OfficialFirmware.objects.create(
            original_file_name="ext.zip",
            source_type="external",
            external_url="https://downloads.example.invalid/ext.zip",
            is_verified=True,
            access_tier=AccessTier.FEATURED,
        )
        client = client_for(user)

        denied = client.get(paths(firmware)["page"])
        shop.featured_users.add(user.pk)
        page = client.get(paths(firmware)["page"])
        start = client.post(
            paths(firmware)["start"],
            {
                "token": page.context["download_token"],
                "sig": page.context["download_signature"],
            },
        )

        assert denied.status_code == 302 and denied.url == paths(firmware)["detail"]
        assert start.status_code == 302
        assert start.url == "https://downloads.example.invalid/ext.zip"
        assert ServedDownload.objects.get().tier == "featured"

    @pytest.mark.usefixtures("shop")
    def test_free_anonymous_downloads_still_work_and_are_not_recorded(
        self, make_firmware
    ):
        firmware = make_firmware()
        client = Client()
        page = client.get(paths(firmware)["page"])

        start = client.post(
            paths(firmware)["start"],
            {
                "token": page.context["download_token"],
                "sig": page.context["download_signature"],
            },
        )

        served = client.get(start["Location"])
        served.close()

        assert served.status_code == 200
        assert not ServedDownload.objects.exists()


# --------------------------------------------------------------------------- pages


@pytest.mark.django_db
class TestTierPages:
    @pytest.mark.usefixtures("shop")
    def test_the_detail_page_shows_each_locked_state_with_its_call_to_action(
        self, make_firmware, make_user
    ):
        paid = make_firmware("paid.zip", tier=AccessTier.PAID, price=PRICE)
        featured = make_firmware("feat.zip", tier=AccessTier.FEATURED)
        user = make_user("viewer")

        anonymous = Client().get(paths(paid)["detail"])
        buyer = client_for(user).get(paths(paid)["detail"])
        member = client_for(user).get(paths(featured)["detail"])

        assert anonymous.context["access"].requires == "login"
        assert b"Sign in to continue" in anonymous.content
        assert b"Paid \xc2\xb7 USD 4.50" in anonymous.content
        assert b"Request this file" in buyer.content
        assert (
            reverse(
                "shop:request_file",
                kwargs={"firmware_type": "official", "firmware_id": paid.pk},
            )
            in buyer.content.decode()
        )
        assert b"Featured \xc2\xb7 members" in member.content
        assert b"See plans" in member.content

    @pytest.mark.usefixtures("shop")
    def test_unlocked_files_show_the_download_button(self, make_firmware, make_user):
        free = make_firmware()

        response = client_for(make_user("free-viewer")).get(paths(free)["detail"])

        assert b"Continue to download" in response.content
        assert b"tier-badge--free" in response.content

    @pytest.mark.usefixtures("shop")
    def test_the_browse_table_shows_tier_badges(self, make_firmware):
        make_firmware("a.zip")
        make_firmware("b.zip", tier=AccessTier.PAID, price=PRICE)
        make_firmware("c.zip", tier=AccessTier.FEATURED)

        response = Client().get(reverse("firmwares:browse"))
        body = response.content.decode()

        assert "tier-badge--free" in body
        assert "Paid · USD 4.50" in body
        assert "Featured · members" in body


# ------------------------------------------------------------------ package perks and caps


@pytest.mark.django_db
class TestPackageCaps:
    def start_session(self, user, firmware, *, minutes_ago=0, status="started"):
        request = RequestFactory().get("/")
        request.user = user
        token = create_download_token(firmware, request)
        session = DownloadSession.objects.create(token=token, user=user, status=status)
        DownloadSession.objects.filter(pk=session.pk).update(
            started_at=timezone.now() - timedelta(minutes=minutes_ago)
        )

    def test_the_concurrent_download_cap_blocks_a_new_download(
        self, make_firmware, make_user, shop
    ):
        user = make_user("busy")
        shop.max_concurrent = 1
        running = make_firmware("running.zip")
        self.start_session(user, running)

        decision = check_download_access(user, make_firmware("next.zip"))

        assert not decision.allowed
        assert "1 download at once" in decision.reason

    def test_the_cap_also_applies_to_files_already_served(
        self, make_firmware, make_user, shop
    ):
        user = make_user("busy2")
        shop.max_concurrent = 1
        served = make_firmware("served.zip")
        record_served_download(user, served, tier=AccessTier.FREE, charged_quota=True)
        self.start_session(user, make_firmware("running.zip"))

        assert not check_download_access(user, served).allowed

    @pytest.mark.parametrize(
        ("minutes_ago", "status"), [(120, "started"), (0, "completed")]
    )
    def test_stale_and_finished_sessions_do_not_count(
        self, make_firmware, make_user, shop, minutes_ago, status
    ):
        user = make_user("idle")
        shop.max_concurrent = 1
        self.start_session(
            user, make_firmware("old.zip"), minutes_ago=minutes_ago, status=status
        )

        assert check_download_access(user, make_firmware("next.zip")).allowed

    @pytest.mark.usefixtures("shop")
    def test_no_cap_means_no_limit(self, make_firmware, make_user):
        user = make_user("free-runner")
        self.start_session(user, make_firmware("running.zip"))

        assert check_download_access(user, make_firmware("next.zip")).allowed

    def test_ad_free_packages_skip_the_ad_gate(
        self, make_firmware, make_user, shop, settings
    ):
        settings.FIRMWARE_DOWNLOAD_GATE_ENABLED = True
        settings.FIRMWARE_DOWNLOAD_AD_GATE_ENABLED = True
        member = make_user("adfree")
        other = make_user("ads")
        shop.ad_free_users.add(member.pk)
        firmware = make_firmware("gate.zip")

        def token_for(user):
            request = RequestFactory().get("/")
            request.user = user
            return create_download_token(firmware, request)

        assert not token_for(member).ad_gate_required
        assert token_for(other).ad_gate_required
