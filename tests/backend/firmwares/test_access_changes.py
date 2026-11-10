"""Access changes after approval: staff changes, requests, audit trail, auto-feature."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from apps.firmwares import access_changes, access_requests, auto_feature
from apps.firmwares.access_change_models import (
    AccessActor,
    AccessChangeLog,
    AccessChangeRequest,
    AccessChangeSource,
    AutoFeatureSettings,
)
from apps.firmwares.access_changes import (
    AccessChange,
    AccessChangeError,
    apply_access_change,
)
from apps.firmwares.models import (
    Brand,
    DownloadToken,
    Model,
    OfficialFirmware,
    Variant,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(name="notices")
def notices_fixture(monkeypatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def capture(user_ids: list[int], **kwargs: Any) -> None:
        sent.append({"user_ids": user_ids, **kwargs})

    monkeypatch.setattr(access_changes, "notify_users", capture)
    monkeypatch.setattr(access_requests, "notify_users", capture)
    return sent


@pytest.fixture(name="seller_ids")
def seller_ids_fixture(monkeypatch) -> set[int]:
    sellers: set[int] = set()
    monkeypatch.setattr(
        access_changes, "is_seller", lambda user: getattr(user, "pk", 0) in sellers
    )
    monkeypatch.setattr(
        access_requests, "is_seller", lambda user: getattr(user, "pk", 0) in sellers
    )
    return sellers


@pytest.fixture(name="people")
def people_fixture(django_user_model) -> dict[str, Any]:
    make = django_user_model.objects.create_user
    return {
        "staff": make(username="boss", email="boss@e.test", is_staff=True),
        "other_staff": make(username="boss2", email="boss2@e.test", is_staff=True),
        "uploader": make(username="up", email="up@e.test"),
        "stranger": make(username="nobody", email="nobody@e.test"),
    }


@pytest.fixture(name="make_file")
def make_file_fixture(people) -> Any:
    brand = Brand.objects.create(name="Acme", slug="acme")
    model = Model.objects.create(brand=brand, name="Phone One", slug="phone-one")
    variant = Variant.objects.create(model=model, name="Global", slug="global")
    counter = iter(range(1, 1000))

    def build(**extra: Any) -> OfficialFirmware:
        number = next(counter)
        values: dict[str, Any] = {
            "original_file_name": f"fw-{number}.zip",
            "stored_file_path": f"/firmware/fw-{number}.zip",
            "brand": brand,
            "model": model,
            "variant": variant,
            "is_verified": True,
            "is_active": True,
            "uploader": people["uploader"],
        }
        values.update(extra)
        return OfficialFirmware.objects.create(**values)

    return build


def promote(firmware: OfficialFirmware, to_tier: str, price: Any = None, **kw: Any):
    return apply_access_change(
        firmware, AccessChange(to_tier=to_tier, price=price, **kw)
    )


class TestStaffChange:
    def test_free_to_featured_to_paid_and_back_is_recorded(
        self, make_file, people, notices, seller_ids
    ) -> None:
        seller_ids.add(people["uploader"].pk)
        firmware = make_file()
        staff = people["staff"]

        promote(firmware, "featured", actor=staff, reason="Great file")
        promote(firmware, "paid", "4.5", actor=staff)
        promote(firmware, "free", actor=staff)

        firmware.refresh_from_db()
        assert (firmware.access_tier, firmware.approved_price) == ("free", None)
        steps = [
            (log.from_tier, log.to_tier, log.to_price, log.actor_id, log.source)
            for log in AccessChangeLog.objects.order_by("id")
        ]
        assert steps == [
            ("free", "featured", None, staff.pk, "dialog"),
            ("featured", "paid", Decimal("4.50"), staff.pk, "dialog"),
            ("paid", "free", None, staff.pk, "dialog"),
        ]
        assert AccessChangeLog.objects.all()[0].reason == ""

    def test_the_uploader_is_notified_after_commit(
        self, make_file, people, notices, django_capture_on_commit_callbacks
    ) -> None:
        firmware = make_file()

        with django_capture_on_commit_callbacks(execute=True):
            promote(firmware, "featured", actor=people["staff"], reason="Popular")

        assert [item["user_ids"] for item in notices] == [[people["uploader"].pk]]
        assert "keep their access" in notices[0]["message"]

    def test_paid_needs_a_price_and_only_paid_has_one(
        self, make_file, people, seller_ids
    ) -> None:
        seller_ids.add(people["uploader"].pk)
        firmware = make_file()
        staff = people["staff"]

        with pytest.raises(AccessChangeError, match="price"):
            promote(firmware, "paid", actor=staff)
        with pytest.raises(AccessChangeError, match="price greater"):
            promote(firmware, "paid", "0", actor=staff)
        with pytest.raises(AccessChangeError, match="Only paid"):
            promote(firmware, "featured", "3", actor=staff)
        assert AccessChangeLog.objects.count() == 0

    def test_the_price_of_a_paid_file_can_be_changed(
        self, make_file, people, seller_ids
    ) -> None:
        seller_ids.add(people["uploader"].pk)
        firmware = make_file()
        promote(firmware, "paid", "4", actor=people["staff"])

        promote(firmware, "paid", "6", actor=people["staff"])

        firmware.refresh_from_db()
        assert firmware.approved_price == Decimal("6.00")
        with pytest.raises(AccessChangeError, match="already"):
            promote(firmware, "paid", "6", actor=people["staff"])

    def test_paid_files_need_a_seller_uploader(self, make_file, people) -> None:
        firmware = make_file()
        with pytest.raises(AccessChangeError, match="seller"):
            promote(firmware, "paid", "4", actor=people["staff"])

        staff_file = make_file(uploader=people["other_staff"])
        promote(staff_file, "paid", "4", actor=people["staff"])
        assert staff_file.access_tier == "paid"

    def test_unapproved_files_cannot_change(self, make_file, people) -> None:
        firmware = make_file(is_verified=False)
        with pytest.raises(AccessChangeError, match="approved"):
            promote(firmware, "featured", actor=people["staff"])

    def test_staff_cannot_promote_their_own_file(self, make_file, people) -> None:
        firmware = make_file(uploader=people["staff"])
        with pytest.raises(AccessChangeError, match="your own"):
            promote(firmware, "featured", actor=people["staff"])

    def test_the_history_is_append_only(self, make_file, people) -> None:
        log = promote(make_file(), "featured", actor=people["staff"])

        log.reason = "tampered"
        with pytest.raises(RuntimeError):
            log.save()
        with pytest.raises(RuntimeError):
            log.delete()

    def test_a_direct_change_closes_other_open_requests(
        self, make_file, people, seller_ids
    ) -> None:
        seller_ids.add(people["uploader"].pk)
        firmware = make_file()
        access_requests.submit_request(
            firmware,
            people["uploader"],
            to_tier="featured",
            reason="Please feature this build",
        )

        promote(firmware, "featured", actor=people["staff"])

        assert (
            AccessChangeRequest.objects.get().status
            == AccessChangeRequest.Status.WITHDRAWN
        )


class TestRequests:
    def test_only_the_uploader_can_ask(self, make_file, people, seller_ids) -> None:
        seller_ids.update({people["uploader"].pk, people["stranger"].pk})
        firmware = make_file()

        with pytest.raises(AccessChangeError, match="uploader"):
            access_requests.submit_request(
                firmware,
                people["stranger"],
                to_tier="featured",
                reason="I would like it featured",
            )

    def test_normal_uploaders_cannot_ask_for_paid_or_featured(
        self, make_file, people, seller_ids
    ) -> None:
        firmware = make_file()
        with pytest.raises(AccessChangeError, match="sellers"):
            access_requests.submit_request(
                firmware,
                people["uploader"],
                to_tier="featured",
                reason="I would like it featured",
            )
        assert seller_ids == set()

    def test_a_paid_request_needs_a_price_and_a_reason(
        self, make_file, people, seller_ids
    ) -> None:
        seller_ids.add(people["uploader"].pk)
        firmware = make_file()
        with pytest.raises(AccessChangeError, match="price"):
            access_requests.submit_request(
                firmware, people["uploader"], to_tier="paid", reason="A long reason"
            )
        with pytest.raises(AccessChangeError, match="why"):
            access_requests.submit_request(
                firmware,
                people["uploader"],
                to_tier="paid",
                proposed_price="3",
                reason="no",
            )

    def test_one_open_request_per_file(self, make_file, people, seller_ids) -> None:
        seller_ids.add(people["uploader"].pk)
        firmware = make_file()
        access_requests.submit_request(
            firmware, people["uploader"], to_tier="featured", reason="Feature it now"
        )

        with pytest.raises(AccessChangeError, match="pending"):
            access_requests.submit_request(
                firmware,
                people["uploader"],
                to_tier="paid",
                proposed_price="3",
                reason="Rather sell it",
            )

    def test_approval_applies_the_change_with_the_staff_price(
        self,
        make_file,
        people,
        seller_ids,
        notices,
        django_capture_on_commit_callbacks,
    ) -> None:
        seller_ids.add(people["uploader"].pk)
        firmware = make_file()
        request = access_requests.submit_request(
            firmware,
            people["uploader"],
            to_tier="paid",
            proposed_price="9",
            reason="Rare readback, worth money",
        )

        with django_capture_on_commit_callbacks(execute=True):
            access_requests.approve_request(
                request, people["staff"], approved_price="7.5", note="Fair price"
            )

        firmware.refresh_from_db()
        request.refresh_from_db()
        assert (firmware.access_tier, firmware.approved_price) == (
            "paid",
            Decimal("7.50"),
        )
        assert request.status == AccessChangeRequest.Status.APPROVED
        assert request.approved_price == Decimal("7.50")
        log = AccessChangeLog.objects.get()
        assert log.request_id == request.pk
        assert log.source == AccessChangeSource.REQUEST
        assert len(notices) == 1
        assert notices[0]["title"] == "Access change approved"

    def test_rejection_needs_a_note_and_notifies(
        self,
        make_file,
        people,
        seller_ids,
        notices,
        django_capture_on_commit_callbacks,
    ) -> None:
        seller_ids.add(people["uploader"].pk)
        request = access_requests.submit_request(
            make_file(),
            people["uploader"],
            to_tier="featured",
            reason="Please feature this",
        )
        with pytest.raises(AccessChangeError, match="reason"):
            access_requests.reject_request(request, people["staff"], note=" ")

        with django_capture_on_commit_callbacks(execute=True):
            access_requests.reject_request(
                request, people["staff"], note="Not popular enough yet"
            )

        request.refresh_from_db()
        assert request.status == AccessChangeRequest.Status.REJECTED
        assert AccessChangeLog.objects.count() == 0
        assert "Not popular enough yet" in notices[0]["message"]
        with pytest.raises(AccessChangeError, match="already"):
            access_requests.approve_request(request, people["other_staff"])

    def test_staff_cannot_decide_their_own_request(
        self, make_file, people, seller_ids
    ) -> None:
        staff = people["staff"]
        seller_ids.add(staff.pk)
        request = access_requests.submit_request(
            make_file(uploader=staff),
            staff,
            to_tier="featured",
            reason="Feature my own file",
        )

        with pytest.raises(AccessChangeError, match="own request"):
            access_requests.approve_request(request, staff)

    def test_new_requests_notify_the_reviewers(
        self,
        make_file,
        people,
        seller_ids,
        notices,
        django_capture_on_commit_callbacks,
    ) -> None:
        seller_ids.add(people["uploader"].pk)

        with django_capture_on_commit_callbacks(execute=True):
            access_requests.submit_request(
                make_file(),
                people["uploader"],
                to_tier="featured",
                reason="Please feature this build",
            )

        assert len(notices) == 1
        assert notices[0]["title"] == "New access change request"
        assert notices[0]["user_ids"] == []

    def test_withdrawing(self, make_file, people, seller_ids) -> None:
        seller_ids.add(people["uploader"].pk)
        request = access_requests.submit_request(
            make_file(),
            people["uploader"],
            to_tier="featured",
            reason="Feature this one",
        )

        access_requests.withdraw_request(request, people["uploader"])

        request.refresh_from_db()
        assert request.status == AccessChangeRequest.Status.WITHDRAWN
        with pytest.raises(AccessChangeError):
            access_requests.withdraw_request(request, people["stranger"])


def download(firmware: OfficialFirmware, count: int, *, days_ago: int = 1) -> None:
    content_type = ContentType.objects.get_for_model(firmware, for_concrete_model=False)
    when = timezone.now() - timedelta(days=days_ago)
    DownloadToken.objects.bulk_create(
        [
            DownloadToken(
                content_type=content_type,
                object_id=firmware.pk,
                ip=f"10.0.{index // 250}.{index % 250 + 1}",
                status=DownloadToken.Status.USED,
                expires_at=when + timedelta(hours=1),
                used_at=when,
            )
            for index in range(count)
        ]
    )


@pytest.fixture(name="auto_config")
def auto_config_fixture() -> AutoFeatureSettings:
    config = AutoFeatureSettings.get_solo()
    config.enabled = True
    config.dry_run = False
    config.window_days = 30
    config.min_downloads = 20
    config.min_unique_downloaders = 10
    config.velocity_days = 7
    config.min_velocity = Decimal("1.00")
    config.cooldown_days = 14
    config.max_promotions_per_run = 5
    config.save()
    return config


class TestAutoFeature:
    def test_a_popular_free_file_is_promoted_as_the_system(
        self, make_file, auto_config, notices, django_capture_on_commit_callbacks
    ) -> None:
        firmware = make_file()
        download(firmware, 30)

        with django_capture_on_commit_callbacks(execute=True):
            result = auto_feature.run_auto_feature()

        firmware.refresh_from_db()
        assert firmware.access_tier == "featured"
        assert [item.firmware.pk for item in result.promoted] == [firmware.pk]
        log = AccessChangeLog.objects.get()
        assert (log.actor_kind, log.source, log.actor_id) == (
            AccessActor.SYSTEM,
            AccessChangeSource.AUTO,
            None,
        )
        assert "30 downloads" in log.reason
        assert len(notices) == 1
        auto_config.refresh_from_db()
        assert auto_config.last_run_report["promoted"]

    def test_dry_run_changes_nothing(self, make_file, auto_config) -> None:
        firmware = make_file()
        download(firmware, 30)
        auto_config.dry_run = True
        auto_config.save()

        result = auto_feature.run_auto_feature()

        firmware.refresh_from_db()
        assert firmware.access_tier == "free"
        assert len(result.would_promote) == 1
        assert AccessChangeLog.objects.count() == 0

    def test_disabled_does_nothing_unless_previewing(
        self, make_file, auto_config
    ) -> None:
        firmware = make_file()
        download(firmware, 30)
        auto_config.enabled = False
        auto_config.save()

        assert auto_feature.run_auto_feature().skipped == {"disabled": 1}
        preview = auto_feature.run_auto_feature(force_dry_run=True, ignore_enabled=True)
        assert len(preview.would_promote) == 1
        firmware.refresh_from_db()
        assert firmware.access_tier == "free"

    def test_every_threshold_must_be_met(self, make_file, auto_config) -> None:
        few = make_file()
        download(few, 5)
        stale = make_file()
        download(stale, 30, days_ago=20)
        single = make_file()
        content_type = ContentType.objects.get_for_model(
            single, for_concrete_model=False
        )
        DownloadToken.objects.bulk_create(
            [
                DownloadToken(
                    content_type=content_type,
                    object_id=single.pk,
                    ip="10.9.9.9",
                    status=DownloadToken.Status.USED,
                    expires_at=timezone.now() + timedelta(hours=1),
                    used_at=timezone.now() - timedelta(days=1),
                )
                for _ in range(30)
            ]
        )

        result = auto_feature.run_auto_feature()

        assert result.promoted == []
        assert result.skipped.get("too_slow") == 1
        assert result.skipped.get("too_few_people") == 1

    def test_paid_files_are_never_touched(
        self, make_file, auto_config, people, seller_ids
    ) -> None:
        seller_ids.add(people["uploader"].pk)
        firmware = make_file(access_tier="paid", approved_price=Decimal("5.00"))
        download(firmware, 50)

        result = auto_feature.run_auto_feature()

        firmware.refresh_from_db()
        assert firmware.access_tier == "paid"
        assert result.promoted == []

    def test_a_manually_demoted_file_is_never_promoted_again(
        self, make_file, auto_config, people
    ) -> None:
        firmware = make_file()
        download(firmware, 30)
        auto_feature.run_auto_feature()
        promote(firmware, "free", actor=people["staff"], reason="Not for members")
        AccessChangeLog.objects.update(created_at=timezone.now() - timedelta(days=90))

        result = auto_feature.run_auto_feature()

        firmware.refresh_from_db()
        assert firmware.access_tier == "free"
        assert result.skipped == {"demoted_by_staff": 1}

    def test_revert_returns_the_file_and_blocks_a_second_promotion(
        self, make_file, auto_config, people
    ) -> None:
        firmware = make_file()
        download(firmware, 30)
        auto_feature.run_auto_feature()
        log = AccessChangeLog.objects.get()

        access_changes.revert_change(log, actor=people["staff"], reason="Too early")

        firmware.refresh_from_db()
        assert firmware.access_tier == "free"
        latest = AccessChangeLog.objects.all()[0]
        assert latest.reverts_id == log.pk
        assert latest.actor_kind == AccessActor.STAFF
        AccessChangeLog.objects.update(created_at=timezone.now() - timedelta(days=90))
        assert auto_feature.run_auto_feature().promoted == []
        with pytest.raises(AccessChangeError, match="latest"):
            access_changes.revert_change(log, actor=people["staff"], reason="again")

    def test_the_cooldown_skips_recently_changed_files(
        self, make_file, auto_config, people
    ) -> None:
        firmware = make_file()
        download(firmware, 30)
        AccessChangeLog.objects.create(
            content_type=ContentType.objects.get_for_model(
                firmware, for_concrete_model=False
            ),
            object_id=firmware.pk,
            firmware_label="x",
            actor=people["staff"],
            actor_kind=AccessActor.STAFF,
            source=AccessChangeSource.ADMIN,
            from_tier="paid",
            to_tier="free",
            from_price=Decimal("3.00"),
        )

        result = auto_feature.run_auto_feature()

        assert result.promoted == []
        assert result.skipped == {"cooldown": 1}

    def test_the_run_cap(self, make_file, auto_config) -> None:
        popular = [make_file() for _ in range(3)]
        for firmware in popular:
            download(firmware, 30)
        auto_config.max_promotions_per_run = 2
        auto_config.save()

        result = auto_feature.run_auto_feature()

        assert len(result.promoted) == 2
        assert result.skipped["over_run_limit"] == 1
