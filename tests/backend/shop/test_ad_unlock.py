"""Unlock a file by watching rewarded ads: offers, tickets, caps, replay and package changes."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.ads.models import AdPlacement, AdsSettings, RewardedAdConfig, RewardedAdView
from apps.consent import services as consent
from apps.firmwares.access import check_download_access, finalize_served_download
from apps.firmwares.shortener import should_shorten
from apps.shop import ad_unlock, entitlements, services
from apps.shop.models import PackageTier, QuotaConsumption
from apps.shop.unlock_models import AdUnlockProgress, AdUnlockTicket

pytestmark = pytest.mark.django_db

MIN_WATCH = 5


class Clock:
    def __init__(self) -> None:
        self.now = timezone.now()

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture(name="clock")
def clock_fixture() -> Iterator[Clock]:
    clock = Clock()
    with patch("django.utils.timezone.now", side_effect=lambda: clock.now):
        yield clock


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def free_tier(default_tier: PackageTier) -> PackageTier:
    """The default package: 2 new files a day, unlock by 3 ads (free) or 2 (featured)."""
    PackageTier.objects.filter(pk=default_tier.pk).update(
        unlock_by_ads_free=True,
        unlock_ads_free=3,
        unlock_by_ads_featured=True,
        unlock_ads_featured=2,
        unlock_cooldown_seconds=30,
        unlock_daily_cap=5,
    )
    default_tier.refresh_from_db()
    return default_tier


@pytest.fixture
def unlock_ad(db) -> RewardedAdConfig:
    row = AdsSettings.get_solo()
    row.ads_enabled = True
    row.rewarded_ads_enabled = True
    row.save()
    placement = AdPlacement.objects.create(name="Unlock", slug="unlock", code="unlock")
    return RewardedAdConfig.objects.create(
        name="Download unlock",
        placement=placement,
        is_enabled=True,
        reward_type=RewardedAdConfig.RewardType.DOWNLOAD_UNLOCK,
        min_watch_seconds=MIN_WATCH,
        cooldown_minutes=0,
        daily_limit_per_user=0,
        monthly_limit_per_user=0,
    )


@pytest.fixture
def client_for() -> Callable[..., Client]:
    def build(user: Any, *, csrf: bool = False) -> Client:
        client = Client(enforce_csrf_checks=csrf)
        client.force_login(user)
        client.cookies[consent.cookie_name()] = consent.encode_cookie(
            consent.active_policy().version, ["advertising"], uuid.uuid4()
        )
        return client

    return build


@pytest.fixture
def over_quota(free_tier, user, make_firmware) -> Any:
    """``user`` has used the whole daily allowance; returns a further free file."""
    for _ in range(2):
        entitlements.consume_quota(user, make_firmware(), reason="free")
    return make_firmware()


def watch(
    client: Client, config: RewardedAdConfig, ticket: AdUnlockTicket, clock: Clock
) -> dict[str, Any]:
    """One honest rewarded watch bound to ``ticket``; returns the completion JSON."""
    client.get(reverse("ads:rewarded", args=[config.pk]), {"ref": ticket.reference})
    opened = client.post(reverse("ads:rewarded_start", args=[config.pk])).json()
    if not opened.get("ok"):
        return {"started": False, **opened}
    for _ in range(MIN_WATCH // 5 + 1):
        clock.advance(5)
        client.post(reverse("ads:rewarded_heartbeat"), {"token": opened["token"]})
    done = client.post(reverse("ads:rewarded_complete"), {"token": opened["token"]})
    return {"started": True, **done.json()}


def run_unlock(client, config, ticket, clock, ads: int) -> list[dict[str, Any]]:
    results = []
    for index in range(ads):
        if index:
            clock.advance(31)
        results.append(watch(client, config, ticket, clock))
    return results


class TestOffer:
    def test_no_offer_while_the_allowance_lasts(
        self, free_tier, unlock_ad, user, make_firmware
    ):
        assert ad_unlock.offer(user, make_firmware()).state == ad_unlock.QUOTA_OK

    def test_an_over_quota_free_file_can_be_unlocked(self, unlock_ad, user, over_quota):
        offer = ad_unlock.offer(user, over_quota)

        assert (offer.state, offer.required, offer.kind) == (
            ad_unlock.AVAILABLE,
            3,
            "free",
        )
        assert offer.visible and not offer.can_watch

    def test_the_offer_needs_a_configured_unlock_ad(self, user, over_quota):
        offer = ad_unlock.offer(user, over_quota)

        assert offer.state == ad_unlock.UNAVAILABLE
        with pytest.raises(ad_unlock.UnlockRefused):
            ad_unlock.start_ticket(user, over_quota)

    def test_a_package_without_unlocks_offers_nothing(
        self, free_tier, unlock_ad, user, over_quota
    ):
        PackageTier.objects.filter(pk=free_tier.pk).update(unlock_by_ads_free=False)

        assert ad_unlock.offer(user, over_quota).state == ad_unlock.NONE

    def test_ad_free_packages_never_see_an_offer(
        self, free_tier, unlock_ad, user, over_quota, pro_tier
    ):
        PackageTier.objects.filter(pk=pro_tier.pk).update(
            ad_free=True,
            applies_to_featured=False,
            bandwidth_enabled=True,
            limit_files=1,
            unlock_by_ads_free=True,
        )
        pro_tier.refresh_from_db()
        services.activate_package(user, pro_tier)

        assert ad_unlock.offer(user, over_quota).state in {
            ad_unlock.NONE,
            ad_unlock.QUOTA_OK,
        }
        assert ad_unlock.rule_for(user, "free").enabled is False

    def test_unverified_accounts_are_asked_to_verify(
        self, free_tier, unlock_ad, make_user, make_firmware
    ):
        member = make_user("unverified-unlock", verified=False)
        for _ in range(2):
            entitlements.consume_quota(member, make_firmware(), reason="free")

        assert ad_unlock.offer(member, make_firmware()).state == ad_unlock.VERIFY

    def test_paid_files_cannot_be_unlocked_by_ads(
        self, free_tier, unlock_ad, user, make_firmware
    ):
        paid = make_firmware("paid", "5.00")

        assert ad_unlock.offer(user, paid).state == ad_unlock.NONE
        with pytest.raises(ad_unlock.UnlockRefused):
            ad_unlock.start_ticket(user, paid)

    def test_anonymous_visitors_get_no_offer(self, free_tier, unlock_ad, make_firmware):
        from django.contrib.auth.models import AnonymousUser

        assert ad_unlock.offer(AnonymousUser(), make_firmware()).state == ad_unlock.NONE

    def test_a_featured_file_is_offered_with_its_own_count(
        self, free_tier, unlock_ad, user, make_firmware
    ):
        offer = ad_unlock.offer(user, make_firmware("featured"))

        assert (offer.state, offer.required, offer.kind) == (
            ad_unlock.AVAILABLE,
            2,
            "featured",
        )

    def test_a_package_that_includes_featured_needs_no_unlock(
        self, free_tier, unlock_ad, user, make_firmware, pro_tier
    ):
        services.activate_package(user, pro_tier)

        assert (
            ad_unlock.offer(user, make_firmware("featured")).state == ad_unlock.QUOTA_OK
        )


class TestTicket:
    def test_one_ticket_per_user_and_file(self, unlock_ad, user, over_quota):
        first = ad_unlock.start_ticket(user, over_quota)
        again = ad_unlock.start_ticket(user, over_quota)

        assert first.pk == again.pk
        assert AdUnlockTicket.objects.count() == 1
        assert (first.required, first.progress, first.status) == (3, 0, "open")
        assert first.reference == f"unlock:{first.pk}"

    def test_the_offer_then_tracks_progress(self, unlock_ad, user, over_quota):
        ad_unlock.start_ticket(user, over_quota)

        offer = ad_unlock.offer(user, over_quota)

        assert offer.state == ad_unlock.IN_PROGRESS and offer.can_watch
        assert "unlock%3A" in offer.watch_url and "ref=" in offer.watch_url

    def test_the_database_allows_only_one_ticket_per_file(
        self, unlock_ad, user, over_quota
    ):
        from django.db import IntegrityError, transaction

        ticket = ad_unlock.start_ticket(user, over_quota)

        with pytest.raises(IntegrityError), transaction.atomic():
            AdUnlockTicket.objects.create(
                user=user,
                content_type=ticket.content_type,
                object_id=ticket.object_id,
                kind="free",
                required=3,
            )


class TestFullUnlock:
    def test_three_ads_unlock_one_file_without_using_the_allowance(
        self, unlock_ad, user, over_quota, client_for, clock
    ):
        client = client_for(user)
        ticket = ad_unlock.start_ticket(user, over_quota)
        assert not check_download_access(user, over_quota).allowed
        used_before = QuotaConsumption.objects.filter(user=user).count()

        first, second, third = run_unlock(client, unlock_ad, ticket, clock, 3)

        assert [first["reward_granted"], second["reward_granted"]] == [True, True]
        assert "Ad 1 of 3 counted" in first["message"]
        assert "File unlocked" in third["message"]
        ticket.refresh_from_db()
        assert (ticket.status, ticket.progress) == ("ready", 3)
        assert ticket.expires_at > clock.now
        assert check_download_access(user, over_quota).allowed
        assert not should_shorten(user, over_quota)
        assert ad_unlock.offer(user, over_quota).state == ad_unlock.READY

        finalize_served_download(user, over_quota)

        ticket.refresh_from_db()
        assert ticket.status == "redeemed" and ticket.redeemed_at
        assert QuotaConsumption.objects.filter(user=user).count() == used_before

    def test_the_unlock_is_spent_by_the_download_and_cannot_be_reused(
        self, unlock_ad, user, over_quota, client_for, clock, make_firmware
    ):
        client = client_for(user)
        ticket = ad_unlock.start_ticket(user, over_quota)
        run_unlock(client, unlock_ad, ticket, clock, 3)
        other_file = make_firmware()

        assert not check_download_access(user, other_file).allowed
        assert ad_unlock.redeem(user, over_quota) is True
        assert ad_unlock.redeem(user, over_quota) is False
        assert not ad_unlock.has_live_unlock(user, over_quota)

    def test_a_featured_file_unlocks_with_its_own_number_of_ads(
        self, free_tier, unlock_ad, user, make_firmware, client_for, clock
    ):
        featured = make_firmware("featured")
        assert not check_download_access(user, featured).allowed
        ticket = ad_unlock.start_ticket(user, featured)

        run_unlock(client_for(user), unlock_ad, ticket, clock, 2)

        assert check_download_access(user, featured).allowed
        finalize_served_download(user, featured)
        assert not check_download_access(user, make_firmware("featured")).allowed
        ticket.refresh_from_db()
        assert ticket.status == "redeemed"

    def test_the_unlock_only_covers_its_own_file(
        self, unlock_ad, user, over_quota, client_for, clock, make_firmware
    ):
        ticket = ad_unlock.start_ticket(user, over_quota)
        run_unlock(client_for(user), unlock_ad, ticket, clock, 3)

        assert not check_download_access(user, make_firmware()).allowed
        assert check_download_access(user, over_quota).allowed

    def test_another_account_cannot_use_the_unlock(
        self, unlock_ad, user, over_quota, client_for, clock, other
    ):
        ticket = ad_unlock.start_ticket(user, over_quota)
        run_unlock(client_for(user), unlock_ad, ticket, clock, 3)

        assert not ad_unlock.has_live_unlock(other, over_quota)
        assert ad_unlock.offer(other, over_quota).state != ad_unlock.READY


class TestCooldownAndCaps:
    def test_the_next_ad_waits_for_the_cooldown(
        self, unlock_ad, user, over_quota, client_for, clock
    ):
        client = client_for(user)
        ticket = ad_unlock.start_ticket(user, over_quota)
        assert watch(client, unlock_ad, ticket, clock)["reward_granted"]

        early = watch(client, unlock_ad, ticket, clock)

        assert early["started"] is False
        assert early["error"] == "cooldown" and early["retry_after"] > 0
        offer = ad_unlock.offer(user, over_quota)
        assert offer.state == ad_unlock.COOLDOWN and offer.cooldown_seconds > 0
        clock.advance(31)
        assert watch(client, unlock_ad, ticket, clock)["reward_granted"]
        ticket.refresh_from_db()
        assert ticket.progress == 2

    def test_the_daily_cap_limits_unlocked_files(
        self, free_tier, unlock_ad, user, over_quota, client_for, clock, make_firmware
    ):
        PackageTier.objects.filter(pk=free_tier.pk).update(unlock_daily_cap=1)
        ticket = ad_unlock.start_ticket(user, over_quota)
        run_unlock(client_for(user), unlock_ad, ticket, clock, 3)
        second_file = make_firmware()

        offer = ad_unlock.offer(user, second_file)

        assert offer.state == ad_unlock.DAILY_CAP and offer.daily_left == 0
        with pytest.raises(ad_unlock.UnlockRefused) as refused:
            ad_unlock.start_ticket(user, second_file)
        assert refused.value.code == ad_unlock.DAILY_CAP
        clock.advance(25 * 3600)
        assert ad_unlock.offer(user, second_file).state != ad_unlock.DAILY_CAP

    def test_the_final_ad_is_refused_when_the_cap_was_reached_meanwhile(
        self, free_tier, unlock_ad, user, over_quota, make_firmware, client_for, clock
    ):
        PackageTier.objects.filter(pk=free_tier.pk).update(unlock_daily_cap=1)
        ticket = ad_unlock.start_ticket(user, over_quota)
        client = client_for(user)
        run_unlock(client, unlock_ad, ticket, clock, 2)
        AdUnlockTicket.objects.create(
            user=user,
            content_type=ticket.content_type,
            object_id=uuid.uuid4(),
            kind="free",
            required=1,
            progress=1,
            status="redeemed",
            unlocked_at=clock.now,
        )
        clock.advance(31)

        last = watch(client, unlock_ad, ticket, clock)

        assert last["started"] is False and last["error"] == "daily_cap"
        ticket.refresh_from_db()
        assert ticket.status == "open" and ticket.progress == 2


class TestForgeryAndReplay:
    def test_a_watch_without_a_ticket_reference_is_refused(
        self, unlock_ad, user, client_for
    ):
        client = client_for(user)

        result = client.post(reverse("ads:rewarded_start", args=[unlock_ad.pk])).json()

        assert result["ok"] is False and result["error"] == "no_ticket"
        assert not RewardedAdView.objects.filter(user=user).exists()

    @pytest.mark.parametrize(
        "ref", ["unlock:999999", "unlock:abc", "other:1", "x" * 80]
    )
    def test_forged_references_are_refused(self, unlock_ad, user, client_for, ref):
        client = client_for(user)
        client.get(reverse("ads:rewarded", args=[unlock_ad.pk]), {"ref": ref})

        result = client.post(reverse("ads:rewarded_start", args=[unlock_ad.pk])).json()

        assert result["ok"] is False and result["error"] == "no_ticket"

    def test_another_users_ticket_cannot_be_fed(
        self, unlock_ad, user, other, over_quota, client_for, clock
    ):
        ticket = ad_unlock.start_ticket(user, over_quota)

        result = watch(client_for(other), unlock_ad, ticket, clock)

        assert result["started"] is False and result["error"] == "no_ticket"
        ticket.refresh_from_db()
        assert ticket.progress == 0

    def test_completing_twice_counts_the_ad_once(
        self, unlock_ad, user, over_quota, client_for, clock
    ):
        client = client_for(user)
        ticket = ad_unlock.start_ticket(user, over_quota)
        client.get(
            reverse("ads:rewarded", args=[unlock_ad.pk]), {"ref": ticket.reference}
        )
        opened = client.post(reverse("ads:rewarded_start", args=[unlock_ad.pk])).json()
        for _ in range(2):
            clock.advance(5)
            client.post(reverse("ads:rewarded_heartbeat"), {"token": opened["token"]})
        first = client.post(
            reverse("ads:rewarded_complete"), {"token": opened["token"]}
        ).json()
        replay = client.post(
            reverse("ads:rewarded_complete"), {"token": opened["token"]}
        ).json()

        assert first["reward_granted"] is True
        assert replay["already_completed"] is True
        ticket.refresh_from_db()
        assert ticket.progress == 1
        assert AdUnlockProgress.objects.filter(ticket=ticket).count() == 1

    def test_record_watch_is_idempotent_per_view(self, unlock_ad, user, over_quota):
        ticket = ad_unlock.start_ticket(user, over_quota)

        first = ad_unlock.record_watch(user, ticket.reference, 1001)
        again = ad_unlock.record_watch(user, ticket.reference, 1001)

        assert first.counted and again.counted
        ticket.refresh_from_db()
        assert ticket.progress == 1
        assert AdUnlockProgress.objects.filter(view_id=1001).count() == 1

    def test_a_view_id_cannot_be_counted_by_two_tickets(
        self, unlock_ad, user, over_quota, make_firmware
    ):
        first = ad_unlock.start_ticket(user, over_quota)
        second = ad_unlock.start_ticket(user, make_firmware("featured"))
        ad_unlock.record_watch(user, first.reference, 2002)

        outcome = ad_unlock.record_watch(user, second.reference, 2002)

        second.refresh_from_db()
        assert second.progress == 0 and not outcome.counted

    def test_a_concurrent_insert_of_the_same_view_counts_it_once(
        self, unlock_ad, user, over_quota
    ):
        from django.db import IntegrityError

        ticket = ad_unlock.start_ticket(user, over_quota)
        with patch.object(
            AdUnlockProgress.objects, "create", side_effect=IntegrityError("race")
        ):
            outcome = ad_unlock.record_watch(user, ticket.reference, 5005)

        ticket.refresh_from_db()
        assert outcome.counted is True and "already counted" in outcome.message
        assert ticket.progress == 0

    def test_an_unlinked_ad_is_audited_but_never_counted(self, user):
        outcome = ad_unlock.record_watch(user, "", 3003)

        assert outcome.counted is False
        assert not AdUnlockProgress.objects.filter(view_id=3003).exists()

    def test_every_counted_watch_leaves_its_audit_row(
        self, unlock_ad, user, over_quota, client_for, clock
    ):
        ticket = ad_unlock.start_ticket(user, over_quota)
        run_unlock(client_for(user), unlock_ad, ticket, clock, 2)

        views = RewardedAdView.objects.filter(user=user, reference=ticket.reference)
        steps = AdUnlockProgress.objects.filter(ticket=ticket)

        assert views.filter(status="completed", reward_granted=True).count() == 2
        assert set(steps.values_list("view_id", flat=True)) == set(
            views.values_list("pk", flat=True)
        )
        assert list(steps.values_list("number", flat=True)) == [1, 2]


class TestExpiry:
    def test_an_unused_unlock_expires(
        self, unlock_ad, user, over_quota, client_for, clock
    ):
        ticket = ad_unlock.start_ticket(user, over_quota)
        run_unlock(client_for(user), unlock_ad, ticket, clock, 3)
        assert check_download_access(user, over_quota).allowed

        clock.advance(ad_unlock.READY_TTL.total_seconds() + 1)

        assert not check_download_access(user, over_quota).allowed
        assert ad_unlock.redeem(user, over_quota) is False
        ticket.refresh_from_db()
        assert ticket.status == "expired"

    def test_a_stale_counting_ticket_expires_and_can_start_over(
        self, unlock_ad, user, over_quota, client_for, clock
    ):
        ticket = ad_unlock.start_ticket(user, over_quota)
        watch(client_for(user), unlock_ad, ticket, clock)
        AdUnlockTicket.objects.filter(pk=ticket.pk).update(
            expires_at=clock.now - timedelta(seconds=1)
        )
        clock.advance(31)

        closed = watch(client_for(user), unlock_ad, ticket, clock)
        assert closed["started"] is False and closed["error"] == "ticket_closed"
        fresh = ad_unlock.start_ticket(user, over_quota)

        assert fresh.pk == ticket.pk
        assert (fresh.status, fresh.progress, fresh.cycle) == ("open", 0, 2)

    def test_housekeeping_expires_old_tickets(self, unlock_ad, user, over_quota, clock):
        ticket = ad_unlock.start_ticket(user, over_quota)
        clock.advance(ad_unlock.OPEN_TTL.total_seconds() + 1)

        assert ad_unlock.cancel_stale() == 1
        ticket.refresh_from_db()
        assert ticket.status == "expired"


class TestPackageChanges:
    def test_upgrading_to_an_ad_free_package_stops_the_unlock(
        self, free_tier, unlock_ad, user, over_quota, client_for, clock
    ):
        client = client_for(user)
        ticket = ad_unlock.start_ticket(user, over_quota)
        watch(client, unlock_ad, ticket, clock)
        platinum = PackageTier.objects.create(
            name="Platinum",
            slug="platinum",
            price=100,
            duration_days=30,
            ad_free=True,
            bandwidth_enabled=False,
            limit_devices=False,
            applies_to_featured=True,
            applies_to_paid=True,
        )
        services.activate_package(user, platinum)
        clock.advance(31)

        refused = watch(client, unlock_ad, ticket, clock)

        assert refused["started"] is False and refused["error"] == "not_offered"
        assert ad_unlock.offer(user, over_quota).state in {
            ad_unlock.NONE,
            ad_unlock.QUOTA_OK,
        }
        assert check_download_access(user, over_quota).allowed

    def test_an_in_flight_watch_is_not_counted_after_an_upgrade(
        self, free_tier, unlock_ad, user, over_quota
    ):
        ticket = ad_unlock.start_ticket(user, over_quota)
        platinum = PackageTier.objects.create(
            name="Platinum",
            slug="platinum",
            price=100,
            duration_days=30,
            ad_free=True,
            bandwidth_enabled=False,
            limit_devices=False,
            applies_to_featured=True,
            applies_to_paid=True,
        )
        services.activate_package(user, platinum)

        outcome = ad_unlock.record_watch(user, ticket.reference, 4004)

        ticket.refresh_from_db()
        assert outcome.counted is False and ticket.status == "cancelled"

    def test_lowering_the_required_ads_mid_ticket_applies_at_once(
        self, free_tier, unlock_ad, user, over_quota, client_for, clock
    ):
        client = client_for(user)
        ticket = ad_unlock.start_ticket(user, over_quota)
        watch(client, unlock_ad, ticket, clock)
        PackageTier.objects.filter(pk=free_tier.pk).update(unlock_ads_free=2)
        clock.advance(31)

        done = watch(client, unlock_ad, ticket, clock)

        assert "File unlocked" in done["message"]
        ticket.refresh_from_db()
        assert (ticket.status, ticket.progress, ticket.required) == ("ready", 2, 2)

    def test_raising_the_required_ads_mid_ticket_keeps_the_progress(
        self, free_tier, unlock_ad, user, over_quota, client_for, clock
    ):
        client = client_for(user)
        ticket = ad_unlock.start_ticket(user, over_quota)
        run_unlock(client, unlock_ad, ticket, clock, 2)
        PackageTier.objects.filter(pk=free_tier.pk).update(unlock_ads_free=4)
        clock.advance(31)

        watch(client, unlock_ad, ticket, clock)

        ticket.refresh_from_db()
        assert (ticket.status, ticket.progress, ticket.required) == ("open", 3, 4)

    def test_downgrading_to_a_package_without_unlocks_cancels_the_watch(
        self, free_tier, unlock_ad, user, over_quota, client_for, clock
    ):
        ticket = ad_unlock.start_ticket(user, over_quota)
        PackageTier.objects.filter(pk=free_tier.pk).update(unlock_by_ads_free=False)

        refused = watch(client_for(user), unlock_ad, ticket, clock)

        assert refused["error"] == "not_offered"


class TestAnalytics:
    def test_the_funnel_counts_started_unlocked_and_ads(
        self, unlock_ad, user, over_quota, client_for, clock, make_firmware
    ):
        ticket = ad_unlock.start_ticket(user, over_quota)
        run_unlock(client_for(user), unlock_ad, ticket, clock, 3)
        ad_unlock.redeem(user, over_quota)
        ad_unlock.start_ticket(user, make_firmware("featured"))

        stats = ad_unlock.analytics()

        assert (stats.started, stats.unlocked, stats.redeemed, stats.ads_counted) == (
            2,
            1,
            1,
            3,
        )
        assert stats.unlock_rate == 50 and stats.ads_per_unlock == 3.0
        assert "2 started, 1 unlocked (50%)" in stats.summary()
        assert ad_unlock.analytics(since=clock.now + timedelta(days=1)).started == 0

    def test_an_empty_funnel_does_not_divide_by_zero(self, db):
        stats = ad_unlock.analytics()

        assert (stats.unlock_rate, stats.ads_per_unlock) == (0, 0.0)


class TestPanel:
    def start_url(self, firmware: Any) -> str:
        return reverse(
            "shop:unlock_start",
            kwargs={"firmware_type": "official", "firmware_id": firmware.pk},
        )

    def panel_url(self, firmware: Any) -> str:
        return reverse(
            "shop:unlock_panel",
            kwargs={"firmware_type": "official", "firmware_id": firmware.pk},
        )

    def test_the_file_page_shows_the_offer_and_the_upgrade_link(
        self, unlock_ad, user, over_quota, client_for
    ):
        page = (
            client_for(user)
            .get(reverse("firmwares:firmware_detail", args=["official", over_quota.pk]))
            .content.decode()
        )

        assert 'id="ad-unlock-panel"' in page
        assert "Watch <strong>3</strong> short ads to unlock this one file" in page
        assert "csrfmiddlewaretoken" in page
        assert reverse("shop:pricing") in page

    def test_starting_over_htmx_returns_the_progress_panel(
        self, unlock_ad, user, over_quota, client_for
    ):
        response = client_for(user).post(
            self.start_url(over_quota), HTTP_HX_REQUEST="true"
        )

        body = response.content.decode()
        assert response.status_code == 200
        assert "0 of 3 ads watched" in body and "Watch ad 1 of 3" in body
        assert AdUnlockTicket.objects.filter(user=user).count() == 1

    def test_a_plain_post_redirects_back_to_the_file(
        self, unlock_ad, user, over_quota, client_for
    ):
        response = client_for(user).post(self.start_url(over_quota))

        assert response.status_code == 302
        assert response["Location"] == reverse(
            "firmwares:firmware_detail", args=["official", over_quota.pk]
        )

    def test_the_cooldown_panel_refreshes_itself(
        self, unlock_ad, user, over_quota, client_for, clock
    ):
        client = client_for(user)
        ticket = ad_unlock.start_ticket(user, over_quota)
        watch(client, unlock_ad, ticket, clock)

        body = client.get(self.panel_url(over_quota)).content.decode()

        assert 'data-unlock-state="cooldown"' in body
        assert 'hx-trigger="load delay:' in body
        assert "1 of 3 ads watched" in body

    def test_the_ready_panel_links_to_the_download(
        self, unlock_ad, user, over_quota, client_for, clock
    ):
        client = client_for(user)
        ticket = ad_unlock.start_ticket(user, over_quota)
        run_unlock(client, unlock_ad, ticket, clock, 3)

        body = client.get(self.panel_url(over_quota)).content.decode()

        assert 'data-unlock-state="ready"' in body
        assert (
            reverse("firmwares:firmware_download", args=["official", over_quota.pk])
            in body
        )

    def test_a_missing_csrf_token_is_refused(
        self, unlock_ad, user, over_quota, client_for
    ):
        response = client_for(user, csrf=True).post(self.start_url(over_quota))

        assert response.status_code == 403
        assert not AdUnlockTicket.objects.exists()

    def test_anonymous_visitors_are_sent_to_sign_in(self, client, over_quota):
        assert client.get(self.panel_url(over_quota)).status_code == 302
        assert client.post(self.start_url(over_quota)).status_code == 302

    def test_a_package_without_unlocks_gets_an_empty_panel(
        self, free_tier, user, over_quota, client_for
    ):
        PackageTier.objects.filter(pk=free_tier.pk).update(unlock_by_ads_free=False)

        response = client_for(user).get(self.panel_url(over_quota))

        assert response.status_code == 200
        assert response.content.strip() == b""
