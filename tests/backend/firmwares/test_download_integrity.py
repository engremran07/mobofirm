"""Download gate integrity: bound links, replay refusal, cookie identity, real leases.

Covers F-039 (the concurrent-download cap measures live transfer leases) and F-040
(a signed link is bound to its token's principal, short lived and bounded; anonymous
tokens belong to a signed cookie, never to an IP; the ad gate takes nothing from the
client).
"""

from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import StreamingHttpResponse
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.firmwares import download_leases
from apps.firmwares.access import concurrency_refusal
from apps.firmwares.download_identity import IDENTITY_COOKIE, subject_of
from apps.firmwares.download_leases import (
    REDEMPTION_BUDGET,
    held_transfers,
    release_expired,
    report_transfer,
    transfer_window,
)
from apps.firmwares.download_service import (
    ANON_TOKENS_PER_IP_HOUR,
    LINK_TTL_CAP_SECONDS,
    TokenError,
    consume_download_token,
    create_download_token,
    generate_signed_url,
    link_ttl_seconds,
    sign_token,
    verify_link,
)
from apps.firmwares.models import (
    AdGateLog,
    DownloadSession,
    DownloadToken,
    FirmwareSourceType,
    OfficialFirmware,
    ServedDownload,
)
from apps.firmwares.tasks import expire_download_tokens
from apps.firmwares.tracking_models import FirmwareDownloadAttempt

CONTENT = b"firmware-bytes"
EXTERNAL_URL = "https://downloads.example.invalid/external.zip"


@pytest.fixture(name="storage")
def storage_fixture(settings):
    with tempfile.TemporaryDirectory() as directory:
        settings.FIRMWARE_STORAGE_ROOT = Path(directory)
        settings.FIRMWARE_DOWNLOAD_GATE_ENABLED = False
        settings.FIRMWARE_DOWNLOAD_AD_GATE_ENABLED = False
        settings.FIRMWARE_DOWNLOAD_HOTLINK_PROTECTION = False
        settings.FIRMWARE_DOWNLOAD_REQUIRE_LOGIN = False
        settings.FIRMWARE_DOWNLOAD_MAX_PER_DAY = 0
        settings.FIRMWARE_DOWNLOAD_LINK_TTL_SECONDS = 600
        settings.DEBUG = True
        yield Path(directory)


@pytest.fixture(name="make_firmware")
def make_firmware_fixture(catalog, storage):
    def factory(name="file.zip", *, content=CONTENT, **extra):
        path = storage / name
        path.write_bytes(content)
        values = {
            "original_file_name": name,
            "stored_file_path": str(path),
            "brand": catalog.brand,
            "model": catalog.model,
            "variant": catalog.variant,
            "is_verified": True,
            "file_size": len(content),
        }
        values.update(extra)
        return OfficialFirmware.objects.create(**values)

    return factory


@pytest.fixture(name="make_external")
def make_external_fixture(catalog):
    def factory(name="external.zip"):
        return OfficialFirmware.objects.create(
            original_file_name=name,
            source_type=FirmwareSourceType.EXTERNAL,
            external_url=EXTERNAL_URL,
            brand=catalog.brand,
            model=catalog.model,
            variant=catalog.variant,
            is_verified=True,
        )

    return factory


def urls(firmware):
    kwargs = {"firmware_type": "official", "firmware_id": str(firmware.pk)}
    return {
        "page": reverse("firmwares:firmware_download", kwargs=kwargs),
        "start": reverse("firmwares:firmware_download_start", kwargs=kwargs),
        "ad": reverse("firmwares:firmware_ad_gate_complete", kwargs=kwargs),
    }


def member_client(user):
    client = Client()
    client.force_login(user)
    return client


def admit(client, firmware):
    """Open the page and start the download; returns ``(response, page)``.

    When the page itself refuses (cap reached, quota) the refusal is returned twice.
    """
    page = client.get(urls(firmware)["page"])
    if page.status_code != 200:
        return page, page
    data = {
        "token": page.context["download_token"],
        "sig": page.context["download_signature"],
    }
    return client.post(urls(firmware)["start"], data), page


def link_of(client, firmware):
    started, _page = admit(client, firmware)
    assert started.status_code == 302
    assert "/firmwares/serve/" in started["Location"]
    return started["Location"]


def open_link(client, location):
    response = client.get(location)
    if isinstance(response, StreamingHttpResponse):
        response.getvalue()
        response.close()
    return response


def session_of(firmware):
    return DownloadSession.objects.get(token__object_id=firmware.pk)


# ------------------------------------------------------------- identity: cookie, not IP


@pytest.mark.django_db
class TestAnonymousIdentity:
    def test_the_page_sets_a_signed_http_only_cookie_and_stores_only_its_hash(
        self, make_firmware
    ):
        firmware = make_firmware()
        client = Client()

        client.get(urls(firmware)["page"])

        morsel = client.cookies[IDENTITY_COOKIE]
        assert morsel["httponly"]
        assert morsel["samesite"] == "Lax"
        token = DownloadToken.objects.get()
        assert token.anon_hash
        assert token.anon_hash not in morsel.value
        assert len(token.anon_hash) == 64

    def test_one_browser_reuses_its_token_and_two_browsers_never_share_one(
        self, make_firmware
    ):
        firmware = make_firmware()
        first, second = Client(), Client()

        one = first.get(urls(firmware)["page"]).context["download_token"]
        again = first.get(urls(firmware)["page"]).context["download_token"]
        other = second.get(urls(firmware)["page"]).context["download_token"]

        assert one == again
        assert other != one
        assert DownloadToken.objects.count() == 2

    def test_clients_behind_one_ip_do_not_share_countdown_state(self, make_firmware):
        firmware = make_firmware()

        one = Client(REMOTE_ADDR="203.0.113.7").get(urls(firmware)["page"])
        two = Client(REMOTE_ADDR="203.0.113.7").get(urls(firmware)["page"])

        assert one.context["download_token"] != two.context["download_token"]

    def test_a_forged_cookie_is_replaced_not_trusted(self, make_firmware):
        firmware = make_firmware()
        client = Client()
        client.cookies[IDENTITY_COOKIE] = "forged-value"

        response = client.get(urls(firmware)["page"])

        assert response.status_code == 200
        assert client.cookies[IDENTITY_COOKIE].value != "forged-value"

    def test_one_ip_cannot_mint_unbounded_anonymous_tokens(self, make_firmware):
        firmware = make_firmware()
        for _ in range(ANON_TOKENS_PER_IP_HOUR):
            Client(REMOTE_ADDR="203.0.113.9").get(urls(firmware)["page"])

        blocked = Client(REMOTE_ADDR="203.0.113.9").get(urls(firmware)["page"])

        assert blocked.status_code == 302
        assert DownloadToken.objects.count() == ANON_TOKENS_PER_IP_HOUR

    def test_service_raises_a_rate_error_for_an_ip_over_the_limit(
        self, make_firmware, rf
    ):
        firmware = make_firmware()
        for _ in range(ANON_TOKENS_PER_IP_HOUR):
            request = rf.get("/", REMOTE_ADDR="203.0.113.10")
            request.user = AnonymousUser()
            create_download_token(firmware, request)
        request = rf.get("/", REMOTE_ADDR="203.0.113.10")
        request.user = AnonymousUser()

        with pytest.raises(TokenError) as raised:
            create_download_token(firmware, request)

        assert raised.value.code == "rate"


# ----------------------------------------------------------------- signed link binding


@pytest.mark.django_db
class TestSignedLinkBinding:
    def test_the_owner_downloads_and_the_ttl_is_capped(self, make_firmware):
        firmware = make_firmware()
        browser = Client()

        location = link_of(browser, firmware)
        served = open_link(browser, location)

        assert served.status_code == 200
        assert link_ttl_seconds() == LINK_TTL_CAP_SECONDS
        assert served["Cache-Control"] == "private, no-store"
        assert served["Referrer-Policy"] == "no-referrer"

    def test_another_browser_cannot_replay_a_leaked_link(self, make_firmware):
        firmware = make_firmware()
        owner = Client()
        location = link_of(owner, firmware)

        stranger_without_cookie = Client().get(location)
        stranger_with_cookie = Client()
        stranger_with_cookie.get(urls(firmware)["page"])

        assert stranger_without_cookie.status_code == 404
        assert stranger_with_cookie.get(location).status_code == 404
        assert not session_of(firmware).redeem_count

    def test_a_signed_in_users_link_does_not_work_for_anyone_else(
        self, make_firmware, make_user
    ):
        owner, other = make_user("owner"), make_user("other")
        firmware = make_firmware()
        location = link_of(member_client(owner), firmware)

        assert Client().get(location).status_code == 404
        assert member_client(other).get(location).status_code == 404
        assert open_link(member_client(owner), location).status_code == 200

    def test_anonymous_token_cannot_be_started_by_another_browser(self, make_firmware):
        firmware = make_firmware()
        owner = Client()
        page = owner.get(urls(firmware)["page"])
        data = {
            "token": page.context["download_token"],
            "sig": page.context["download_signature"],
        }

        stolen = Client().post(urls(firmware)["start"], data)

        assert stolen.status_code == 302
        assert "/firmwares/serve/" not in stolen["Location"]
        assert DownloadToken.objects.get().status == DownloadToken.Status.ACTIVE
        assert not DownloadSession.objects.exists()

    def test_a_signed_in_token_cannot_be_started_anonymously(
        self, make_firmware, make_user
    ):
        firmware = make_firmware()
        owner = member_client(make_user("holder"))
        page = owner.get(urls(firmware)["page"])
        data = {
            "token": page.context["download_token"],
            "sig": page.context["download_signature"],
        }

        stolen = Client().post(urls(firmware)["start"], data)

        assert "/firmwares/serve/" not in stolen["Location"]
        assert DownloadToken.objects.get().status == DownloadToken.Status.ACTIVE

    def test_the_signature_covers_the_principal(self, make_firmware, make_user):
        owner = make_user("signature-owner")
        firmware = make_firmware()
        token = DownloadToken.objects.create(
            firmware=firmware,
            user=owner,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        link = generate_signed_url(firmware, token)
        assert link is not None
        query = parse_qs(urlsplit(link).query)
        expires, signature = query["exp"][0], query["sig"][0]

        def valid(subject):
            return verify_link(
                token.token, str(firmware.pk), expires, signature, subject=subject
            )

        assert valid(subject_of(token))
        assert not valid(f"u{owner.pk + 1}")
        assert not valid("a" + "0" * 64)

    def test_the_link_is_refused_after_its_expiry(self, make_firmware):
        firmware = make_firmware()
        browser = Client()
        location = link_of(browser, firmware)
        DownloadSession.objects.update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )

        assert browser.get(location).status_code == 404

    def test_a_replayed_link_stops_after_the_redemption_budget(
        self, make_firmware, make_user, settings
    ):
        settings.DEBUG = False
        user = make_user("resumer")
        firmware = make_firmware()
        browser = member_client(user)
        location = link_of(browser, firmware)

        statuses = [browser.get(location).status_code for _ in range(REDEMPTION_BUDGET)]
        replay = browser.get(location)

        assert statuses == [200] * REDEMPTION_BUDGET
        assert replay.status_code == 404
        assert session_of(firmware).redeem_count == REDEMPTION_BUDGET

    def test_resumes_charge_the_library_once(self, make_firmware, make_user, settings):
        settings.DEBUG = False
        user = make_user("charged-once")
        firmware = make_firmware()
        browser = member_client(user)
        location = link_of(browser, firmware)

        for _ in range(3):
            assert browser.get(location).status_code == 200

        row = ServedDownload.objects.get(user=user)
        assert row.times_served == 1

    def test_a_finished_transfer_kills_the_link(self, make_firmware):
        firmware = make_firmware()
        browser = Client()
        location = link_of(browser, firmware)
        assert open_link(browser, location).status_code == 200

        assert browser.get(location).status_code == 404

    def test_a_refused_charge_fails_the_session_and_frees_the_slot(
        self, make_firmware, make_user, shop
    ):
        shop.max_concurrent = 1
        user = make_user("refused")
        firmware = make_firmware()
        browser = member_client(user)
        location = link_of(browser, firmware)
        shop.fail_consume = True

        denied = browser.get(location)

        session = session_of(firmware)
        assert denied.status_code == 403
        assert session.status == DownloadSession.Status.FAILED
        assert session.released_at is not None
        assert session.redeem_count == 0
        assert held_transfers(user.pk) == 0
        assert FirmwareDownloadAttempt.objects.get().status == "failed"


# ------------------------------------------------------------------- the ad gate proof


@pytest.mark.django_db
class TestAdGate:
    @pytest.fixture(autouse=True)
    def _gate_on(self, settings, shop, storage):
        settings.FIRMWARE_DOWNLOAD_GATE_ENABLED = True
        settings.FIRMWARE_DOWNLOAD_COUNTDOWN_SECONDS = 0
        settings.FIRMWARE_DOWNLOAD_AD_GATE_ENABLED = True
        settings.FIRMWARE_DOWNLOAD_AD_GATE_SECONDS = 30
        return shop, storage

    @staticmethod
    def age(seconds):
        DownloadToken.objects.update(
            created_at=timezone.now() - timedelta(seconds=seconds)
        )

    def test_the_client_cannot_choose_the_ad_type_or_prove_anything(
        self, make_firmware
    ):
        firmware = make_firmware()
        browser = Client()
        page = browser.get(urls(firmware)["page"])
        self.age(40)

        done = browser.post(
            urls(firmware)["ad"],
            {
                "token": page.context["download_token"],
                "sig": page.context["download_signature"],
                "ad_type": "interstitial",
                "ad_network": "forged",
                "watched_seconds": "9999",
            },
            HTTP_ACCEPT="application/json",
        )

        log = AdGateLog.objects.get()
        assert done.json() == {"ok": True}
        assert log.ad_type == "banner"
        assert log.proof == AdGateLog.Proof.DWELL
        assert log.ad_network == ""
        assert log.watched_seconds < 9999

    def test_another_browser_cannot_complete_the_gate_for_a_token(self, make_firmware):
        firmware = make_firmware()
        page = Client().get(urls(firmware)["page"])
        self.age(40)

        done = Client().post(
            urls(firmware)["ad"],
            {
                "token": page.context["download_token"],
                "sig": page.context["download_signature"],
            },
            HTTP_ACCEPT="application/json",
        )

        assert done.status_code == 400
        assert not DownloadToken.objects.get().ad_gate_completed
        assert not AdGateLog.objects.exists()

    def test_a_forged_page_signature_is_refused(self, make_firmware):
        firmware = make_firmware()
        browser = Client()
        page = browser.get(urls(firmware)["page"])
        self.age(40)

        done = browser.post(
            urls(firmware)["ad"],
            {"token": page.context["download_token"], "sig": sign_token("another")},
            HTTP_ACCEPT="application/json",
        )

        assert done.status_code == 400

    def test_a_live_rewarded_ad_unlock_replaces_the_sponsor_step(
        self, make_firmware, make_user
    ):
        firmware = make_firmware()
        browser = member_client(make_user("unlocked"))

        with patch("apps.firmwares.download_service.has_ad_unlock", return_value=True):
            page = browser.get(urls(firmware)["page"])

        token = DownloadToken.objects.get()
        assert not page.context["ad_required"]
        assert not token.ad_gate_required
        assert token.ad_gate_completed

    def test_the_ad_gate_is_still_required_without_an_unlock(
        self, make_firmware, make_user
    ):
        firmware = make_firmware()
        browser = member_client(make_user("watcher"))

        page = browser.get(urls(firmware)["page"])

        assert page.context["ad_required"]


# ---------------------------------------------------- leases: the cap measures transfers


@pytest.mark.django_db
class TestConcurrentTransferCap:
    @pytest.fixture(autouse=True)
    def _one_slot(self, shop):
        shop.max_concurrent = 1

    def test_a_second_page_is_refused_while_a_transfer_holds_the_slot(
        self, make_firmware, make_user
    ):
        user = make_user("one-slot")
        first, second = make_firmware("a.zip"), make_firmware("b.zip")
        browser = member_client(user)
        link_of(browser, first)

        refused, _page = admit(browser, second)

        assert held_transfers(user.pk) == 1
        assert refused.status_code == 302
        assert "/firmwares/serve/" not in refused["Location"]
        assert DownloadSession.objects.count() == 1

    def test_pages_opened_before_the_cap_was_hit_cannot_all_start(
        self, make_firmware, make_user
    ):
        user = make_user("parallel")
        browser = member_client(user)
        first, second = make_firmware("a.zip"), make_firmware("b.zip")
        pages = {
            firmware.pk: browser.get(urls(firmware)["page"])
            for firmware in (first, second)
        }

        def start(firmware):
            page = pages[firmware.pk]
            return browser.post(
                urls(firmware)["start"],
                {
                    "token": page.context["download_token"],
                    "sig": page.context["download_signature"],
                },
            )

        one, two = start(first), start(second)

        assert "/firmwares/serve/" in one["Location"]
        assert "/firmwares/serve/" not in two["Location"]
        assert DownloadToken.objects.get(object_id=second.pk).status == (
            DownloadToken.Status.ACTIVE
        )
        assert held_transfers(user.pk) == 1

    def test_n_plus_one_parallel_admissions_against_an_n_slot_cap_refuse_the_last(
        self, make_firmware, make_user, shop
    ):
        shop.max_concurrent = 3
        user = make_user("three-slots")
        browser = member_client(user)
        files = [make_firmware(f"f{index}.zip") for index in range(4)]
        pages = [browser.get(urls(firmware)["page"]) for firmware in files]

        locations = [
            browser.post(
                urls(firmware)["start"],
                {
                    "token": page.context["download_token"],
                    "sig": page.context["download_signature"],
                },
            )["Location"]
            for firmware, page in zip(files, pages, strict=True)
        ]

        assert ["/firmwares/serve/" in location for location in locations] == [
            True,
            True,
            True,
            False,
        ]
        assert held_transfers(user.pk) == 3

    def test_the_admission_check_itself_refuses_when_no_slot_is_free(
        self, make_firmware, make_user, rf
    ):
        user = make_user("direct")
        first, second = make_firmware("a.zip"), make_firmware("b.zip")
        request = rf.get("/")
        request.user = user
        token_one = create_download_token(first, request)
        token_two = create_download_token(second, request)
        consume_download_token(token_one.token, first, request)

        with pytest.raises(TokenError) as raised:
            consume_download_token(token_two.token, second, request)

        assert raised.value.code == "concurrent"
        assert "1 download at once" in raised.value.message
        token_two.refresh_from_db()
        assert token_two.status == DownloadToken.Status.ACTIVE

    def test_a_finished_transfer_frees_the_slot(self, make_firmware, make_user):
        user = make_user("finisher")
        first, second = make_firmware("a.zip"), make_firmware("b.zip")
        browser = member_client(user)
        location = link_of(browser, first)
        assert held_transfers(user.pk) == 1

        assert open_link(browser, location).status_code == 200

        assert held_transfers(user.pk) == 0
        assert "/firmwares/serve/" in admit(browser, second)[0]["Location"]

    def test_an_abandoned_link_frees_its_slot_when_the_lease_expires(
        self, make_firmware, make_user
    ):
        user = make_user("abandoner")
        browser = member_client(user)
        link_of(browser, make_firmware("a.zip"))
        assert held_transfers(user.pk) == 1

        DownloadSession.objects.update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )

        assert held_transfers(user.pk) == 0
        assert concurrency_refusal(user) == ""

    def test_a_started_transfer_without_a_report_holds_until_its_window_ends(
        self, make_firmware, make_user, settings
    ):
        settings.DEBUG = False
        user = make_user("nginx-user")
        firmware = make_firmware("a.zip")
        browser = member_client(user)
        location = link_of(browser, firmware)

        served = browser.get(location)

        session = session_of(firmware)
        assert served["X-Accel-Redirect"].endswith("a.zip")
        assert session.bytes_delivered == 0
        assert session.released_at is None
        assert held_transfers(user.pk) == 1
        assert session.lease_expires_at - timezone.now() > timedelta(minutes=9)

    def test_external_hand_offs_never_hold_a_slot_or_meet_the_cap(
        self, make_firmware, make_external, make_user
    ):
        user = make_user("hand-off")
        browser = member_client(user)
        link_of(browser, make_firmware("a.zip"))
        external = make_external()

        started, _page = admit(browser, external)

        assert started["Location"] == EXTERNAL_URL
        hand_off = DownloadSession.objects.get(token__object_id=external.pk)
        assert hand_off.released_at is not None
        assert hand_off.lease_expires_at is None
        assert held_transfers(user.pk) == 1

    def test_an_external_session_does_not_stay_started(self, make_external, make_user):
        user = make_user("hand-off-two")

        admit(member_client(user), make_external())

        assert not DownloadSession.objects.filter(
            status=DownloadSession.Status.STARTED
        ).exists()

    def test_the_cap_ignores_other_users(self, make_firmware, make_user):
        busy, free = make_user("busy"), make_user("free")
        link_of(member_client(busy), make_firmware("a.zip"))

        started, _page = admit(member_client(free), make_firmware("b.zip"))

        assert "/firmwares/serve/" in started["Location"]


# --------------------------------------------------------------- the lease state machine


@pytest.mark.django_db
class TestLeaseLifecycle:
    def served(self, make_firmware, content=CONTENT):
        firmware = make_firmware(content=content)
        browser = Client()
        location = link_of(browser, firmware)
        return firmware, browser, location

    def test_the_window_is_size_aware_and_bounded(self):
        assert transfer_window(None) == download_leases.MIN_TRANSFER_WINDOW
        assert transfer_window(10) == download_leases.MIN_TRANSFER_WINDOW
        big = 4 * 1024**3
        assert download_leases.MIN_TRANSFER_WINDOW < transfer_window(big)
        assert transfer_window(big) <= download_leases.MAX_TRANSFER_WINDOW
        assert transfer_window(10**15) == download_leases.MAX_TRANSFER_WINDOW

    def test_a_client_that_stops_early_aborts_the_transfer(self, make_firmware):
        content = b"x" * 20000
        firmware, browser, location = self.served(make_firmware, content)

        response = browser.get(location)
        stream = iter(response.streaming_content)
        next(stream)
        response.close()

        session = session_of(firmware)
        assert session.status == DownloadSession.Status.ABORTED
        assert 0 < session.bytes_delivered < len(content)
        assert session.released_at is not None
        attempt = FirmwareDownloadAttempt.objects.get()
        assert attempt.status == "failed"

    def test_reports_are_idempotent_and_the_first_one_wins(
        self, make_firmware, settings
    ):
        settings.DEBUG = False
        firmware, browser, location = self.served(make_firmware)
        assert browser.get(location).status_code == 200
        session = session_of(firmware)

        first = report_transfer(session.pk, bytes_delivered=14, completed=True)
        duplicate = report_transfer(session.pk, bytes_delivered=14, completed=True)
        late_abort = report_transfer(session.pk, bytes_delivered=3, completed=False)

        session.refresh_from_db()
        assert (first, duplicate, late_abort) == (True, False, False)
        assert session.status == DownloadSession.Status.COMPLETED
        assert session.bytes_delivered == 14
        assert FirmwareDownloadAttempt.objects.get().status == "completed"

    def test_a_completion_after_expiry_still_records_the_bytes(
        self, make_firmware, settings
    ):
        settings.DEBUG = False
        firmware, browser, location = self.served(make_firmware)
        browser.get(location)
        session = session_of(firmware)
        DownloadSession.objects.filter(pk=session.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        release_expired()

        recorded = report_transfer(session.pk, bytes_delivered=14, completed=True)

        session.refresh_from_db()
        assert recorded
        assert session.bytes_delivered == 14

    def test_a_report_for_a_link_that_was_never_opened_changes_nothing(
        self, make_firmware
    ):
        firmware, _browser, _location = self.served(make_firmware)
        session = session_of(firmware)

        assert not report_transfer(session.pk, bytes_delivered=14, completed=True)
        session.refresh_from_db()
        assert session.bytes_delivered == 0
        assert session.released_at is None

    def test_the_sweep_aborts_unopened_links_and_releases_opened_ones(
        self, make_firmware, settings
    ):
        settings.DEBUG = False
        opened_fw, opened_browser, opened_link = self.served(make_firmware)
        opened_browser.get(opened_link)
        idle_fw = make_firmware("idle.zip")
        link_of(Client(), idle_fw)
        DownloadSession.objects.update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )

        result = expire_download_tokens()

        idle = session_of(idle_fw)
        opened = session_of(opened_fw)
        assert result["leases_released"] == 2
        assert idle.status == DownloadSession.Status.ABORTED
        assert idle.released_at is not None
        assert opened.status == DownloadSession.Status.COMPLETED
        assert opened.released_at is not None
        assert FirmwareDownloadAttempt.objects.get(session=idle).status == "cancelled"
        assert release_expired() == 0

    def test_legacy_sessions_without_a_lease_hold_a_slot_only_briefly(
        self, make_firmware, make_user
    ):
        user = make_user("legacy")
        firmware = make_firmware()
        token = DownloadToken.objects.create(
            firmware=firmware,
            user=user,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        fresh = DownloadSession.objects.create(token=token, user=user)
        old = DownloadSession.objects.create(token=token, user=user)
        DownloadSession.objects.filter(pk=old.pk).update(
            started_at=timezone.now() - timedelta(hours=2)
        )

        assert held_transfers(user.pk) == 1
        DownloadSession.objects.filter(pk=fresh.pk).update(released_at=timezone.now())
        assert held_transfers(user.pk) == 0

    def test_the_download_does_not_count_before_a_byte_is_delivered(
        self, make_firmware
    ):
        firmware, _browser, _location = self.served(make_firmware)

        session = session_of(firmware)

        assert session.bytes_delivered == 0
        assert session.redeem_count == 0
        assert FirmwareDownloadAttempt.objects.get().bytes_transferred == 0, (
            "an attempt must not claim bytes before any were sent"
        )
