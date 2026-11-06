from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.http import StreamingHttpResponse
from django.http.response import HttpResponseBase
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.firmwares.download_service import sign_token
from apps.firmwares.models import (
    AdGateLog,
    Brand,
    DownloadSession,
    DownloadToken,
    EngineeringFirmware,
    FirmwareSourceType,
    Model,
    ModifiedFirmware,
    OfficialFirmware,
    OtherFirmware,
    ReadbackFirmware,
    UnclassifiedFirmware,
)
from apps.firmwares.tracking_models import FirmwareDownloadAttempt

CONTENT = b"signed-firmware-bytes"
TYPE_MODELS = {
    "official": OfficialFirmware,
    "engineering": EngineeringFirmware,
    "readback": ReadbackFirmware,
    "modified": ModifiedFirmware,
    "other": OtherFirmware,
    "unclassified": UnclassifiedFirmware,
}


def streamed_body(response: HttpResponseBase) -> bytes:
    if not isinstance(response, StreamingHttpResponse):
        raise TypeError("The response was expected to stream a file.")
    return response.getvalue()


@override_settings(
    FIRMWARE_DOWNLOAD_GATE_ENABLED=True,
    FIRMWARE_DOWNLOAD_COUNTDOWN_SECONDS=10,
    FIRMWARE_DOWNLOAD_AD_GATE_ENABLED=False,
    FIRMWARE_DOWNLOAD_AD_GATE_SECONDS=30,
    FIRMWARE_DOWNLOAD_TOKEN_EXPIRY_MINUTES=30,
    FIRMWARE_DOWNLOAD_REQUIRE_LOGIN=False,
    FIRMWARE_DOWNLOAD_MAX_PER_DAY=0,
    FIRMWARE_DOWNLOAD_HOTLINK_PROTECTION=False,
    FIRMWARE_DOWNLOAD_LINK_TTL_SECONDS=600,
    DEBUG=True,
)
class DownloadFlowTestCase(TestCase):
    def setUp(self):
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.root = Path(storage.name)
        override = override_settings(FIRMWARE_STORAGE_ROOT=self.root)
        override.enable()
        self.addCleanup(override.disable)

        self.brand = Brand.objects.create(name="Flow Brand", slug="flow-brand")
        self.model = Model.objects.create(
            brand=self.brand, name="Flow Phone", slug="flow-phone"
        )
        self.client = Client()

    def make_firmware(self, kind="official", name="flow.zip", **extra):
        path = self.root / self.brand.slug / kind / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(CONTENT)
        values = {
            "original_file_name": name,
            "stored_file_path": str(path),
            "brand": self.brand,
            "model": self.model,
            "is_verified": True,
            "file_size": len(CONTENT),
        }
        values.update(extra)
        return TYPE_MODELS[kind].objects.create(**values)

    def urls(self, firmware, kind="official"):
        params = {"firmware_type": kind, "firmware_id": str(firmware.pk)}
        return {
            "page": reverse("firmwares:firmware_download", kwargs=params),
            "start": reverse("firmwares:firmware_download_start", kwargs=params),
            "ad": reverse("firmwares:firmware_ad_gate_complete", kwargs=params),
            "detail": reverse("firmwares:firmware_detail", kwargs=params),
        }

    def open_page(self, firmware, kind="official", client=None):
        client = client or self.client
        response = client.get(self.urls(firmware, kind)["page"])
        self.assertEqual(response.status_code, 200)
        return response

    def post_start(self, firmware, response, kind="official", client=None, **extra):
        data = {
            "token": response.context["download_token"],
            "sig": response.context["download_signature"],
        }
        data.update(extra)
        return (client or self.client).post(self.urls(firmware, kind)["start"], data)

    def age_token(self, token, seconds):
        DownloadToken.objects.filter(token=token).update(
            created_at=timezone.now() - timedelta(seconds=seconds)
        )


class TypedTokenTests(DownloadFlowTestCase):
    def test_download_page_works_for_every_firmware_category(self):
        for kind in TYPE_MODELS:
            with self.subTest(kind=kind):
                firmware = self.make_firmware(kind, f"{kind}.zip")

                response = self.open_page(firmware, kind)

                token = DownloadToken.objects.get(
                    token=response.context["download_token"]
                )
                self.assertEqual(token.object_id, firmware.pk)
                self.assertEqual(token.firmware, firmware)
                self.assertEqual(token.content_type.model, f"{kind}firmware")
                self.assertEqual(list(firmware.download_tokens), [token])

    def test_repeated_page_loads_reuse_the_live_token(self):
        firmware = self.make_firmware()

        first = self.open_page(firmware)
        second = self.open_page(firmware)

        self.assertEqual(
            first.context["download_token"], second.context["download_token"]
        )
        self.assertEqual(DownloadToken.objects.count(), 1)

    def test_a_token_cannot_be_used_for_a_different_firmware(self):
        first = self.make_firmware(name="first.zip")
        second = self.make_firmware(name="second.zip")
        response = self.open_page(first)
        self.age_token(response.context["download_token"], 60)

        result = self.post_start(second, response)

        self.assertRedirects(
            result, self.urls(second)["page"], fetch_redirect_response=False
        )
        token = DownloadToken.objects.get()
        self.assertEqual(token.status, DownloadToken.Status.ACTIVE)

    def test_unreviewed_or_inactive_firmware_is_never_downloadable(self):
        hidden = self.make_firmware(name="hidden.zip", is_verified=False)
        inactive = self.make_firmware(name="inactive.zip", is_active=False)

        for firmware in (hidden, inactive):
            urls = self.urls(firmware)
            self.assertEqual(self.client.get(urls["page"]).status_code, 404)
            self.assertEqual(
                self.client.post(urls["start"], {"token": "x", "sig": "y"}).status_code,
                404,
            )
            self.assertEqual(self.client.get(urls["detail"]).status_code, 404)
        self.assertFalse(DownloadToken.objects.exists())


class CountdownAndReplayTests(DownloadFlowTestCase):
    def test_start_before_the_countdown_is_refused_and_keeps_the_token(self):
        firmware = self.make_firmware()
        response = self.open_page(firmware)
        self.assertGreater(response.context["remaining_seconds"], 0)
        self.assertFalse(response.context["can_start"])

        early = self.post_start(firmware, response)

        self.assertRedirects(
            early, self.urls(firmware)["page"], fetch_redirect_response=False
        )
        token = DownloadToken.objects.get()
        self.assertEqual(token.status, DownloadToken.Status.ACTIVE)
        self.assertFalse(DownloadSession.objects.exists())
        firmware.refresh_from_db()
        self.assertEqual(firmware.download_count, 0)

    def test_start_after_the_countdown_redirects_to_a_signed_link_once(self):
        firmware = self.make_firmware()
        response = self.open_page(firmware)
        self.age_token(response.context["download_token"], 11)

        started = self.post_start(firmware, response)

        self.assertEqual(started.status_code, 302)
        self.assertIn("/firmwares/serve/", started["Location"])
        token = DownloadToken.objects.get()
        self.assertEqual(token.status, DownloadToken.Status.USED)
        self.assertIsNotNone(token.used_at)
        self.assertEqual(DownloadSession.objects.filter(token=token).count(), 1)
        firmware.refresh_from_db()
        self.assertEqual(firmware.download_count, 1)
        self.assertEqual(
            FirmwareDownloadAttempt.objects.filter(object_id=firmware.pk).count(), 1
        )

        replay = self.post_start(firmware, response)

        self.assertRedirects(
            replay, self.urls(firmware)["page"], fetch_redirect_response=False
        )
        firmware.refresh_from_db()
        self.assertEqual(firmware.download_count, 1)
        self.assertEqual(DownloadSession.objects.count(), 1)

    @override_settings(FIRMWARE_DOWNLOAD_GATE_ENABLED=False)
    def test_countdown_is_skipped_when_the_gate_is_disabled(self):
        firmware = self.make_firmware()
        response = self.open_page(firmware)

        started = self.post_start(firmware, response)

        self.assertEqual(started.status_code, 302)

    def test_bad_signature_does_not_consume_the_token(self):
        firmware = self.make_firmware()
        response = self.open_page(firmware)
        self.age_token(response.context["download_token"], 60)

        result = self.post_start(firmware, response, sig="0" * 64)

        self.assertEqual(result.status_code, 302)
        self.assertEqual(
            DownloadToken.objects.get().status, DownloadToken.Status.ACTIVE
        )

    def test_expired_tokens_are_refused_and_marked_expired(self):
        firmware = self.make_firmware()
        response = self.open_page(firmware)
        DownloadToken.objects.update(expires_at=timezone.now() - timedelta(minutes=1))

        result = self.post_start(firmware, response)

        self.assertEqual(result.status_code, 302)
        self.assertEqual(
            DownloadToken.objects.get().status, DownloadToken.Status.EXPIRED
        )


class ServeLocalFileTests(DownloadFlowTestCase):
    def consume(self, firmware, kind="official", client=None):
        response = self.open_page(firmware, kind, client)
        self.age_token(response.context["download_token"], 60)
        started = self.post_start(firmware, response, kind, client)
        self.assertEqual(started.status_code, 302)
        return started["Location"], response.context["download_token"]

    def test_signed_link_streams_the_file_and_records_delivery(self):
        firmware = self.make_firmware()
        location, token_value = self.consume(firmware)

        served = self.client.get(location)

        self.assertEqual(served.status_code, 200)
        self.assertEqual(streamed_body(served), CONTENT)
        self.assertIn("attachment", served["Content-Disposition"])
        session = DownloadSession.objects.get(token__token=token_value)
        self.assertEqual(session.status, DownloadSession.Status.COMPLETED)
        self.assertEqual(session.bytes_delivered, len(CONTENT))
        self.assertEqual(
            FirmwareDownloadAttempt.objects.get(object_id=firmware.pk).status,
            "downloading",
        )

    def test_every_category_is_downloadable(self):
        for kind in TYPE_MODELS:
            with self.subTest(kind=kind):
                firmware = self.make_firmware(kind, f"served-{kind}.zip")
                location, _token = self.consume(firmware, kind, Client())
                served = Client().get(location)
                self.assertEqual(streamed_body(served), CONTENT)

    def test_link_for_an_unconsumed_token_is_refused(self):
        firmware = self.make_firmware()
        response = self.open_page(firmware)
        token = response.context["download_token"]
        # A hand-signed link must not bypass the countdown for an ACTIVE token.
        from apps.firmwares.download_service import generate_signed_url

        link = generate_signed_url(firmware, DownloadToken.objects.get(token=token))
        if link is None:
            self.fail("A stored firmware should produce a signed link.")

        self.assertEqual(self.client.get(link).status_code, 404)

    def test_tampered_or_expired_links_are_refused(self):
        firmware = self.make_firmware()
        other = self.make_firmware(name="other.zip")
        location, _token = self.consume(firmware)
        parts = urlsplit(location)
        query = parse_qs(parts.query)

        wrong_signature = f"{parts.path}?token={query['token'][0]}&exp={query['exp'][0]}&sig={'0' * 64}"
        extended = f"{parts.path}?token={query['token'][0]}&exp={int(query['exp'][0]) + 9999}&sig={query['sig'][0]}"
        other_path = location.replace(str(firmware.pk), str(other.pk))

        for url in (wrong_signature, extended, other_path, parts.path):
            with self.subTest(url=url[:60]):
                self.assertEqual(self.client.get(url).status_code, 404)

    @override_settings(FIRMWARE_DOWNLOAD_LINK_TTL_SECONDS=-5)
    def test_expired_ttl_link_is_refused(self):
        firmware = self.make_firmware()
        location, _token = self.consume(firmware)

        self.assertEqual(self.client.get(location).status_code, 404)

    def test_files_outside_the_storage_root_are_never_served(self):
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "secret.zip"
            secret.write_bytes(b"not for download")
            firmware = self.make_firmware(stored_file_path=str(secret))
            response = self.open_page(firmware)
            self.age_token(response.context["download_token"], 60)

            started = self.post_start(firmware, response)

        self.assertRedirects(
            started, self.urls(firmware)["detail"], fetch_redirect_response=False
        )
        self.assertEqual(
            DownloadToken.objects.get().status, DownloadToken.Status.ACTIVE
        )

    def test_relative_traversal_paths_are_never_served(self):
        firmware = self.make_firmware(stored_file_path="../../etc/passwd")
        response = self.open_page(firmware)
        self.age_token(response.context["download_token"], 60)

        started = self.post_start(firmware, response)

        self.assertRedirects(
            started, self.urls(firmware)["detail"], fetch_redirect_response=False
        )

    def test_relative_paths_inside_the_root_are_resolved_against_it(self):
        firmware = self.make_firmware(name="relative.zip")
        relative = Path(firmware.stored_file_path).relative_to(self.root)
        OfficialFirmware.objects.filter(pk=firmware.pk).update(
            stored_file_path=str(relative)
        )
        location, _token = self.consume(firmware)

        served = self.client.get(location)

        self.assertEqual(streamed_body(served), CONTENT)

    @override_settings(DEBUG=False)
    def test_production_uses_x_accel_redirect_under_the_storage_root(self):
        firmware = self.make_firmware(name="accel.zip")
        location, _token = self.consume(firmware)

        served = self.client.get(location)

        self.assertEqual(served.status_code, 200)
        self.assertTrue(served["X-Accel-Redirect"].startswith("/protected-firmware/"))
        self.assertTrue(served["X-Accel-Redirect"].endswith("accel.zip"))
        self.assertEqual(served.content, b"")


class ExternalDownloadTests(DownloadFlowTestCase):
    EXTERNAL_URL = "https://downloads.example.invalid/external.zip"

    def make_external(self):
        return OfficialFirmware.objects.create(
            original_file_name="external.zip",
            source_type=FirmwareSourceType.EXTERNAL,
            stored_file_path="",
            external_url=self.EXTERNAL_URL,
            brand=self.brand,
            model=self.model,
            is_verified=True,
        )

    def test_gate_redirects_to_the_external_url_after_token_consumption(self):
        firmware = self.make_external()
        response = self.open_page(firmware)
        self.assertContains(response, "External download")
        self.age_token(response.context["download_token"], 60)

        started = self.post_start(firmware, response)

        self.assertRedirects(started, self.EXTERNAL_URL, fetch_redirect_response=False)
        self.assertEqual(DownloadToken.objects.get().status, DownloadToken.Status.USED)
        firmware.refresh_from_db()
        self.assertEqual(firmware.download_count, 1)

    def test_external_url_is_not_reachable_before_the_countdown(self):
        firmware = self.make_external()
        response = self.open_page(firmware)

        early = self.post_start(firmware, response)

        self.assertNotEqual(early["Location"], self.EXTERNAL_URL)
        self.assertEqual(
            DownloadToken.objects.get().status, DownloadToken.Status.ACTIVE
        )

    @override_settings(FIRMWARE_EXTERNAL_URL_ALLOWED_HOSTS=["other.example.test"])
    def test_a_host_removed_from_the_allowlist_is_no_longer_redirected_to(self):
        firmware = self.make_external()
        response = self.open_page(firmware)
        self.age_token(response.context["download_token"], 60)

        started = self.post_start(firmware, response)

        self.assertRedirects(
            started, self.urls(firmware)["detail"], fetch_redirect_response=False
        )
        self.assertEqual(
            DownloadToken.objects.get().status, DownloadToken.Status.ACTIVE
        )

    def test_detail_page_labels_external_downloads(self):
        firmware = self.make_external()

        response = self.client.get(self.urls(firmware)["detail"])

        self.assertContains(response, "External download")
        self.assertContains(response, "downloads.example.invalid")


@override_settings(FIRMWARE_DOWNLOAD_AD_GATE_ENABLED=True)
class AdGateTests(DownloadFlowTestCase):
    def test_start_requires_the_ad_gate_even_after_the_countdown(self):
        firmware = self.make_firmware()
        response = self.open_page(firmware)
        self.assertTrue(response.context["ad_required"])
        self.age_token(response.context["download_token"], 11)

        blocked = self.post_start(firmware, response)

        self.assertRedirects(
            blocked, self.urls(firmware)["page"], fetch_redirect_response=False
        )
        self.assertEqual(
            DownloadToken.objects.get().status, DownloadToken.Status.ACTIVE
        )

    def test_ad_gate_cannot_be_completed_early(self):
        firmware = self.make_firmware()
        response = self.open_page(firmware)
        data = {
            "token": response.context["download_token"],
            "sig": response.context["download_signature"],
        }

        early = self.client.post(
            self.urls(firmware)["ad"], data, HTTP_ACCEPT="application/json"
        )

        self.assertEqual(early.status_code, 400)
        self.assertFalse(DownloadToken.objects.get().ad_gate_completed)
        self.assertFalse(AdGateLog.objects.exists())

    def test_completed_ad_gate_unlocks_the_download_and_is_logged_once(self):
        firmware = self.make_firmware()
        response = self.open_page(firmware)
        token = response.context["download_token"]
        self.age_token(token, 40)
        data = {"token": token, "sig": response.context["download_signature"]}

        done = self.client.post(
            self.urls(firmware)["ad"], data, HTTP_ACCEPT="application/json"
        )
        again = self.client.post(
            self.urls(firmware)["ad"], data, HTTP_ACCEPT="application/json"
        )
        started = self.post_start(firmware, response)

        self.assertEqual(done.json(), {"ok": True})
        self.assertEqual(again.json(), {"ok": True})
        self.assertEqual(started.status_code, 302)
        self.assertEqual(AdGateLog.objects.count(), 1)
        log = AdGateLog.objects.get()
        self.assertEqual(log.token.token, token)
        self.assertEqual(log.session, DownloadSession.objects.get())

    def test_form_post_to_the_ad_gate_redirects_back_to_the_page(self):
        firmware = self.make_firmware()
        response = self.open_page(firmware)
        token = response.context["download_token"]
        self.age_token(token, 40)

        result = self.client.post(
            self.urls(firmware)["ad"],
            {"token": token, "sig": response.context["download_signature"]},
        )

        self.assertRedirects(
            result, self.urls(firmware)["page"], fetch_redirect_response=False
        )
        self.assertTrue(DownloadToken.objects.get().ad_gate_completed)

    def test_ad_gate_rejects_forged_signatures(self):
        firmware = self.make_firmware()
        response = self.open_page(firmware)

        result = self.client.post(
            self.urls(firmware)["ad"],
            {"token": response.context["download_token"], "sig": sign_token("other")},
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(result.status_code, 400)


class QuotaTests(DownloadFlowTestCase):
    @override_settings(FIRMWARE_DOWNLOAD_MAX_PER_DAY=1)
    def test_daily_quota_counts_actual_downloads_for_anonymous_visitors(self):
        first = self.make_firmware(name="quota-one.zip")
        second = self.make_firmware(name="quota-two.zip")
        for _ in range(3):
            self.open_page(first)
        self.assertEqual(DownloadToken.objects.count(), 1)

        response = self.open_page(first)
        self.age_token(response.context["download_token"], 60)
        self.assertEqual(self.post_start(first, response).status_code, 302)

        blocked = self.client.get(self.urls(second)["page"])

        self.assertRedirects(
            blocked, self.urls(second)["detail"], fetch_redirect_response=False
        )

    @override_settings(FIRMWARE_DOWNLOAD_REQUIRE_LOGIN=True)
    def test_login_requirement_is_enforced_server_side(self):
        firmware = self.make_firmware()

        anonymous = self.client.get(self.urls(firmware)["page"])

        self.assertRedirects(
            anonymous, self.urls(firmware)["detail"], fetch_redirect_response=False
        )
        self.assertFalse(DownloadToken.objects.exists())


class TokenApiTests(DownloadFlowTestCase):
    def setUp(self):
        super().setUp()
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="token-owner", email="token-owner@example.test"
        )
        self.stranger = user_model.objects.create_user(
            username="token-stranger", email="token-stranger@example.test"
        )
        self.firmware = self.make_firmware()
        self.token = DownloadToken.objects.create(
            firmware=self.firmware,
            user=self.owner,
            token="owner-secret-token-value",
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        self.api = APIClient()

    def test_tokens_are_read_only_and_never_expose_the_secret(self):
        self.api.force_authenticate(self.owner)
        url = reverse("firmwares:download-tokens-list")

        listing = self.api.get(url)
        created = self.api.post(
            url,
            {"firmware": str(self.firmware.pk), "ad_gate_required": False},
            format="json",
        )
        detail_url = reverse("firmwares:download-tokens-detail", args=[self.token.pk])
        patched = self.api.patch(detail_url, {"status": "active"}, format="json")
        deleted = self.api.delete(detail_url)

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json()), 1)
        self.assertNotIn("owner-secret-token-value", listing.content.decode())
        self.assertEqual(listing.json()[0]["firmware_type"], "officialfirmware")
        self.assertEqual(created.status_code, 405)
        self.assertEqual(patched.status_code, 405)
        self.assertEqual(deleted.status_code, 405)
        self.assertEqual(DownloadToken.objects.count(), 1)

    def test_users_only_see_their_own_tokens_and_anonymous_see_none(self):
        url = reverse("firmwares:download-tokens-list")

        self.api.force_authenticate(self.stranger)
        stranger = self.api.get(url)
        self.api.force_authenticate(None)
        anonymous = self.api.get(url)

        self.assertEqual(stranger.json(), [])
        self.assertIn(anonymous.status_code, (401, 403))
