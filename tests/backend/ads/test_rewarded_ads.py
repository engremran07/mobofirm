"""Rewarded ads: server-timed start / heartbeat / complete, caps, abuse flags and rewards."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.ads import rewarded
from apps.ads.api import get_rewarded_ad_config, rewarded_offer
from apps.ads.models import (
    AdPlacement,
    AdsSettings,
    RewardedAdConfig,
    RewardedAdGrant,
    RewardedAdView,
)
from apps.consent import services as consent
from apps.devices.models import DeviceResetAllowance
from apps.shop import entitlements
from apps.wallet import api as wallet
from apps.wallet.models import LedgerEntry, RewardRule

pytestmark = pytest.mark.django_db

PASSWORD = "Pass-12345!"
MIN_WATCH = 20


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
def fresh_state() -> Iterator[None]:
    from apps.wallet.services import sync_registry

    cache.clear()
    sync_registry()
    yield
    cache.clear()


@pytest.fixture(name="make_user")
def make_user_fixture(django_user_model) -> Callable[..., Any]:
    def build(username: str, *, verified: bool = True) -> Any:
        return django_user_model.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=PASSWORD,
            email_verified_at=timezone.now() if verified else None,
        )

    return build


@pytest.fixture(name="user")
def user_fixture(make_user) -> Any:
    return make_user("viewer")


@pytest.fixture(name="other")
def other_fixture(make_user) -> Any:
    return make_user("bystander")


@pytest.fixture(name="switched_on")
def switched_on_fixture() -> AdsSettings:
    row = AdsSettings.get_solo()
    row.ads_enabled = True
    row.rewarded_ads_enabled = True
    row.save()
    return row


@pytest.fixture(name="placement")
def placement_fixture() -> AdPlacement:
    return AdPlacement.objects.create(
        name="Reward slot", slug="reward-slot", code="reward-slot"
    )


@pytest.fixture(name="make_config")
def make_config_fixture(request, placement) -> Callable[..., RewardedAdConfig]:
    request.getfixturevalue("switched_on")

    def build(**overrides: Any) -> RewardedAdConfig:
        fields: dict[str, Any] = {
            "name": f"Reward {uuid.uuid4().hex[:6]}",
            "placement": placement,
            "is_enabled": True,
            "reward_type": RewardedAdConfig.RewardType.CREDITS,
            "reward_amount": 1,
            "min_watch_seconds": MIN_WATCH,
            "cooldown_minutes": 0,
            "daily_limit_per_user": 0,
            "monthly_limit_per_user": 0,
        }
        fields.update(overrides)
        return RewardedAdConfig.objects.create(**fields)

    return build


@pytest.fixture(name="config")
def config_fixture(make_config) -> RewardedAdConfig:
    return make_config()


@pytest.fixture(name="client_for")
def client_for_fixture() -> Callable[..., Client]:
    def build(user: Any, *, consent_given: bool = True, csrf: bool = False) -> Client:
        client = Client(enforce_csrf_checks=csrf)
        client.force_login(user)
        if consent_given:
            client.cookies[consent.cookie_name()] = consent.encode_cookie(
                consent.active_policy().version, ["advertising"], uuid.uuid4()
            )
        return client

    return build


def start(client: Client, config: RewardedAdConfig) -> dict[str, Any]:
    response = client.post(reverse("ads:rewarded_start", args=[config.pk]))
    return {"status": response.status_code, **response.json()}


def beat(client: Client, token: str, **extra: str) -> dict[str, Any]:
    response = client.post(reverse("ads:rewarded_heartbeat"), {"token": token, **extra})
    return {"status": response.status_code, **response.json()}


def finish(client: Client, token: str) -> dict[str, Any]:
    response = client.post(reverse("ads:rewarded_complete"), {"token": token})
    return {"status": response.status_code, **response.json()}


def watch(client: Client, config: RewardedAdConfig, clock: Clock) -> dict[str, Any]:
    """A full honest watch: start, heartbeats every five seconds, complete."""
    opened = start(client, config)
    assert opened["ok"], opened
    for _ in range(MIN_WATCH // 5 + 1):
        clock.advance(5)
        assert beat(client, opened["token"])["ok"]
    return finish(client, opened["token"])


def credits_of(user: Any) -> int:
    return wallet.balance(user)


class TestAvailability:
    def test_nothing_is_enabled_by_default(self, client_for, user):
        client = client_for(user)
        config = RewardedAdConfig.objects.create(name="Draft")

        page = client.get(reverse("ads:rewarded", args=[config.pk]))

        assert page.status_code == 200
        assert b"Rewarded ads are not enabled right now." in page.content
        assert b"Draft" not in page.content
        assert start(client, config)["error"] == "not_available"
        assert rewarded_offer("credits") == {"enabled": False}
        assert get_rewarded_ad_config() == {"enabled": False}

    @pytest.mark.usefixtures("switched_on")
    def test_a_config_without_a_placement_is_not_configured(self, client_for, user):
        config = RewardedAdConfig.objects.create(name="No slot", is_enabled=True)
        client = client_for(user)

        page = client.get(reverse("ads:rewarded", args=[config.pk]))

        assert b"not configured yet" in page.content
        assert rewarded.unavailable_reason(config) == (
            "This rewarded ad is not configured yet."
        )

    def test_the_master_switch_wins_over_an_enabled_config(
        self, client_for, user, config
    ):
        AdsSettings.objects.update(rewarded_ads_enabled=False)

        assert start(client_for(user), config)["error"] == "not_available"

    def test_a_disabled_placement_is_not_configured(self, client_for, user, config):
        AdPlacement.objects.filter(pk=config.placement_id).update(is_enabled=False)

        assert start(client_for(user), config)["error"] == "not_available"

    def test_pages_need_a_signed_in_user(self, client, config):
        page = client.get(reverse("ads:rewarded", args=[config.pk]))

        assert page.status_code == 302
        assert "login" in page["Location"]

    def test_json_endpoints_answer_401_instead_of_redirecting(self, client, config):
        response = client.post(reverse("ads:rewarded_start", args=[config.pk]))

        assert response.status_code == 401
        assert response.json()["error"] == "login_required"

    def test_the_ready_page_shows_the_timer_and_the_slot(
        self, client_for, user, config
    ):
        page = client_for(user).get(reverse("ads:rewarded", args=[config.pk]))

        body = page.content.decode()
        assert 'x-data="rewardedAd"' in body
        assert f'data-min-watch="{MIN_WATCH}"' in body
        assert "Start watching" in body
        assert "<script" not in body.split('id="rewarded-ad"')[1].split("</section>")[0]

    def test_unknown_config_is_a_404(self, client_for, user):
        assert (
            client_for(user).get(reverse("ads:rewarded", args=[999999])).status_code
            == 404
        )

    def test_offer_and_api_only_report_live_configs(self, config):
        offer = rewarded_offer("credits")

        assert offer["enabled"] is True
        assert offer["url"] == reverse("ads:rewarded", args=[config.pk])
        assert get_rewarded_ad_config(config.name)["url"] == offer["url"]
        assert rewarded_offer("device_reset") == {"enabled": False}


class TestEligibility:
    def test_consent_is_required_and_nothing_is_recorded(
        self, client_for, user, config
    ):
        client = client_for(user, consent_given=False)

        page = client.get(reverse("ads:rewarded", args=[config.pk]))
        result = start(client, config)

        assert b"advertising consent" in page.content
        assert b"Open the privacy centre" in page.content
        assert (result["status"], result["error"]) == (403, "consent_required")
        assert not RewardedAdView.objects.exists()

    def test_an_unverified_email_cannot_start(self, client_for, make_user, config):
        client = client_for(make_user("unverified", verified=False))

        result = start(client, config)

        assert (result["status"], result["error"]) == (403, "email_unverified")
        assert not RewardedAdView.objects.exists()

    def test_post_requires_csrf(self, client_for, user, config):
        client = client_for(user, csrf=True)

        response = client.post(reverse("ads:rewarded_start", args=[config.pk]))

        assert response.status_code == 403
        assert not RewardedAdView.objects.exists()

    def test_get_is_not_allowed_on_the_json_endpoints(self, client_for, user, config):
        client = client_for(user)

        assert (
            client.get(reverse("ads:rewarded_start", args=[config.pk])).status_code
            == 405
        )
        assert client.get(reverse("ads:rewarded_complete")).status_code == 405


class TestHonestWatch:
    def test_a_full_watch_pays_credits_once(self, client_for, user, config, clock):
        client = client_for(user)

        result = watch(client, config, clock)

        assert result["ok"] and result["reward_granted"]
        assert result["reward_type"] == "credits"
        assert credits_of(user) == 5
        view = RewardedAdView.objects.get()
        assert view.status == "completed"
        assert (view.reward_granted, view.reward_amount) == (True, 5)
        assert view.watch_duration_seconds >= MIN_WATCH
        assert view.token_digest and not view.is_flagged
        entry = LedgerEntry.objects.get(key=f"ads:rewarded:{view.pk}")
        assert entry.event_key == rewarded.EVENT_KEY

    def test_the_token_is_stored_only_as_a_digest(self, client_for, user, config):
        opened = start(client_for(user), config)

        view = RewardedAdView.objects.get()
        assert opened["token"] not in view.token_digest
        assert view.token_digest == rewarded.digest_token(opened["token"])

    def test_completing_twice_is_idempotent_and_flags_the_replay(
        self, client_for, user, config, clock
    ):
        client = client_for(user)
        opened = start(client, config)
        for _ in range(MIN_WATCH // 5 + 1):
            clock.advance(5)
            beat(client, opened["token"])
        first = finish(client, opened["token"])
        second = finish(client, opened["token"])

        assert first["already_completed"] is False
        assert second["already_completed"] is True
        assert credits_of(user) == 5
        assert LedgerEntry.objects.filter(event_key=rewarded.EVENT_KEY).count() == 1
        view = RewardedAdView.objects.get()
        assert view.is_flagged and rewarded.FLAG_REPLAY in view.flags

    def test_heartbeats_after_completion_are_refused_and_flagged(
        self, client_for, user, config, clock
    ):
        client = client_for(user)
        opened = start(client, config)
        for _ in range(MIN_WATCH // 5 + 1):
            clock.advance(5)
            beat(client, opened["token"])
        finish(client, opened["token"])

        late = beat(client, opened["token"])

        assert (late["status"], late["error"]) == (409, "not_active")
        assert rewarded.FLAG_REPLAY in RewardedAdView.objects.get().flags

    @pytest.mark.usefixtures("clock")
    def test_completing_at_once_is_refused_and_flagged(self, client_for, user, config):
        client = client_for(user)
        opened = start(client, config)

        early = finish(client, opened["token"])

        assert (early["status"], early["error"]) == (409, "not_watched_long_enough")
        assert early["remaining_seconds"] >= MIN_WATCH - 1
        assert credits_of(user) == 0
        view = RewardedAdView.objects.get()
        assert view.status == "started"
        assert view.is_flagged and rewarded.FLAG_TOO_EARLY in view.flags

    @pytest.mark.usefixtures("clock")
    def test_repeated_early_completion_rejects_the_view(self, client_for, user, config):
        client = client_for(user)
        opened = start(client, config)
        for _ in range(rewarded.MAX_EARLY_COMPLETIONS):
            finish(client, opened["token"])

        view = RewardedAdView.objects.get()
        assert view.status == "rejected"
        assert finish(client, opened["token"])["error"] == "not_active"
        assert credits_of(user) == 0

    def test_waiting_without_heartbeats_cannot_beat_the_cap_per_beat(
        self, client_for, user, make_config, clock
    ):
        config = make_config(min_watch_seconds=60)
        client = client_for(user)
        opened = start(client, config)
        clock.advance(120)

        result = finish(client, opened["token"])

        assert result["error"] == "not_watched_long_enough"
        assert credits_of(user) == 0
        assert RewardedAdView.objects.get().accrued_ms == (
            rewarded.HEARTBEAT_MAX_GAP_SECONDS * 1000
        )

    def test_heartbeats_keep_a_long_watch_valid(
        self, client_for, user, make_config, clock
    ):
        config = make_config(min_watch_seconds=40)
        client = client_for(user)
        opened = start(client, config)
        for _ in range(9):
            clock.advance(5)
            progress = beat(client, opened["token"])

        assert progress["watched_seconds"] == 45
        assert progress["remaining_seconds"] == 0
        assert finish(client, opened["token"])["reward_granted"] is True

    def test_a_client_claiming_more_than_elapsed_is_flagged(
        self, client_for, user, config, clock
    ):
        client = client_for(user)
        opened = start(client, config)
        clock.advance(5)

        beat(client, opened["token"], claimed="600")

        view = RewardedAdView.objects.get()
        assert rewarded.FLAG_IMPOSSIBLE in view.flags
        assert view.accrued_ms == 5000

    def test_a_session_that_ran_too_long_expires(self, client_for, user, config, clock):
        client = client_for(user)
        opened = start(client, config)
        clock.advance(MIN_WATCH + rewarded.SESSION_GRACE_SECONDS + 1)

        late = beat(client, opened["token"])

        assert (late["status"], late["error"]) == (410, "expired")
        assert RewardedAdView.objects.get().status == "expired"
        assert finish(client, opened["token"])["error"] == "not_active"

    @pytest.mark.usefixtures("clock")
    def test_the_floor_overrides_a_tiny_configured_time(self, make_config):
        config = make_config(min_watch_seconds=1)

        assert rewarded.effective_min_watch(config) == 5

    @override_settings(REWARDED_AD_MIN_WATCH_FLOOR_SECONDS=0)
    def test_a_settings_override_allows_short_watches_for_tests(self, make_config):
        assert rewarded.effective_min_watch(make_config(min_watch_seconds=1)) == 1

    def test_an_unset_time_uses_the_site_default(self, make_config):
        assert rewarded.effective_min_watch(make_config(min_watch_seconds=0)) == (
            rewarded.DEFAULT_MIN_WATCH_SECONDS
        )


class TestTokensAreBoundToTheirOwner:
    def test_another_users_token_is_invalid(
        self, client_for, user, other, config, clock
    ):
        mine = client_for(user)
        theirs = client_for(other)
        opened = start(mine, config)
        clock.advance(MIN_WATCH + 1)

        stolen = finish(theirs, opened["token"])
        stolen_beat = beat(theirs, opened["token"])

        assert (stolen["status"], stolen["error"]) == (400, "invalid_token")
        assert stolen_beat["error"] == "invalid_token"
        assert credits_of(other) == 0
        assert RewardedAdView.objects.get().status == "started"

    def test_garbage_and_missing_tokens_are_rejected(self, client_for, user, config):
        client = client_for(user)
        start(client, config)

        assert finish(client, "nope")["error"] == "invalid_token"
        assert finish(client, "")["error"] == "invalid_token"
        assert beat(client, "x" * 500)["error"] == "invalid_token"

    def test_a_bad_claim_is_a_400(self, client_for, user, config):
        client = client_for(user)
        opened = start(client, config)

        response = beat(client, opened["token"], claimed="abc")

        assert (response["status"], response["error"]) == (400, "invalid_input")


class TestSingleActiveView:
    @pytest.mark.usefixtures("clock")
    def test_restarting_expires_the_previous_view_and_its_token(
        self, client_for, user, config
    ):
        client = client_for(user)
        first = start(client, config)
        second = start(client, config)

        assert first["token"] != second["token"]
        statuses = list(
            RewardedAdView.objects.order_by("pk").values_list("status", flat=True)
        )
        assert statuses == ["expired", "started"]
        old = RewardedAdView.objects.order_by("pk").first()
        assert old is not None and rewarded.FLAG_ABANDONED in old.flags
        assert beat(client, first["token"])["error"] == "invalid_token"

    def test_start_is_rate_limited(self, client_for, user, config):
        client = client_for(user)
        for _ in range(rewarded.STARTS_PER_HOUR):
            assert start(client, config)["ok"]

        blocked = start(client, config)

        assert (blocked["status"], blocked["error"]) == (429, "rate_limited")


class TestCaps:
    def test_cooldown_blocks_an_immediate_second_view_and_records_it(
        self, client_for, user, make_config, clock
    ):
        config = make_config(cooldown_minutes=30)
        client = client_for(user)
        assert watch(client, config, clock)["ok"]

        again = start(client, config)

        assert (again["status"], again["error"]) == (429, "cooldown")
        assert again["retry_after"] > 0
        refused = RewardedAdView.objects.filter(status="rejected").get()
        assert rewarded.FLAG_TOO_FREQUENT in refused.flags and refused.is_flagged

    def test_the_cooldown_ends(self, client_for, user, make_config, clock):
        config = make_config(cooldown_minutes=30)
        client = client_for(user)
        watch(client, config, clock)
        clock.advance(31 * 60)

        assert start(client, config)["ok"]

    def test_the_daily_cap(self, client_for, user, make_config, clock):
        config = make_config(daily_limit_per_user=2)
        client = client_for(user)
        watch(client, config, clock)
        watch(client, config, clock)

        third = start(client, config)

        assert (third["status"], third["error"]) == (429, "daily_cap")
        assert credits_of(user) == 10

    def test_the_daily_cap_resets_tomorrow(self, client_for, user, make_config, clock):
        config = make_config(daily_limit_per_user=1)
        client = client_for(user)
        watch(client, config, clock)
        clock.advance(26 * 3600)

        assert start(client, config)["ok"]

    def test_the_monthly_cap(self, client_for, user, make_config, clock):
        config = make_config(monthly_limit_per_user=1)
        client = client_for(user)
        watch(client, config, clock)
        clock.advance(2 * 3600)

        blocked = start(client, config)

        assert (blocked["status"], blocked["error"]) == (429, "monthly_cap")

    def test_caps_are_per_user(self, client_for, user, other, make_config, clock):
        config = make_config(daily_limit_per_user=1)
        watch(client_for(user), config, clock)

        assert start(client_for(other), config)["ok"]

    def test_the_wallet_rule_can_block_the_credits_without_failing_the_view(
        self, client_for, user, config, clock
    ):
        RewardRule.objects.filter(event_key=rewarded.EVENT_KEY).update(enabled=False)

        result = watch(client_for(user), config, clock)

        assert result["ok"] is True
        assert result["reward_granted"] is False
        assert "No credits" in result["message"]
        assert credits_of(user) == 0
        assert RewardedAdView.objects.get().status == "completed"

    def test_the_wallet_daily_credit_cap_stops_further_credits(
        self, client_for, user, config, clock
    ):
        RewardRule.objects.filter(event_key=rewarded.EVENT_KEY).update(
            daily_cap=5, cooldown_seconds=0
        )
        client = client_for(user)

        first = watch(client, config, clock)
        second = watch(client, config, clock)

        assert first["reward_granted"] is True
        assert second["reward_granted"] is False
        assert credits_of(user) == 5


class TestRewardTypes:
    def test_device_reset_grants_idempotent_allowances(
        self, client_for, user, make_config, clock
    ):
        config = make_config(
            reward_type=RewardedAdConfig.RewardType.DEVICE_RESET, reward_amount=2
        )
        client = client_for(user)

        result = watch(client, config, clock)

        assert result["reward_granted"] and result["reward_amount"] == 2
        assert DeviceResetAllowance.objects.filter(user=user).count() == 2
        assert credits_of(user) == 0

    def test_replaying_a_device_reset_completion_adds_nothing(
        self, client_for, user, make_config, clock
    ):
        config = make_config(reward_type=RewardedAdConfig.RewardType.DEVICE_RESET)
        client = client_for(user)
        opened = start(client, config)
        for _ in range(MIN_WATCH // 5 + 1):
            clock.advance(5)
            beat(client, opened["token"])
        finish(client, opened["token"])
        finish(client, opened["token"])

        assert DeviceResetAllowance.objects.filter(user=user).count() == 1

    def test_the_device_reset_offer_points_to_the_ad(self, make_config):
        config = make_config(reward_type=RewardedAdConfig.RewardType.DEVICE_RESET)

        offer = rewarded_offer("device_reset")

        assert offer["url"] == reverse("ads:rewarded", args=[config.pk])

    def test_download_bonus_adds_to_the_allowance_until_it_expires(
        self, client_for, user, make_config, clock
    ):
        config = make_config(
            reward_type=RewardedAdConfig.RewardType.DOWNLOAD_BONUS,
            reward_amount=7,
            valid_hours=2,
        )
        before = entitlements.effective_entitlements(user)

        result = watch(client_for(user), config, clock)

        after = entitlements.effective_entitlements(user)
        assert result["reward_granted"] is True
        assert after.limits.files == before.limits.files + 7
        assert any("Rewarded ad: +7 files" in source for source in after.sources)
        grant = RewardedAdGrant.objects.get()
        assert (grant.kind, grant.amount) == ("download_bonus", 7)

        clock.advance(3 * 3600)
        expired = entitlements.effective_entitlements(user)
        assert expired.limits.files == before.limits.files

    def test_ad_free_turns_ads_off_for_the_period(
        self, client_for, user, make_config, clock, switched_on
    ):
        config = make_config(
            reward_type=RewardedAdConfig.RewardType.AD_FREE, reward_amount=2
        )
        assert switched_on.should_show_ads(user=user, page_url="/x/") is True

        result = watch(client_for(user), config, clock)

        assert result["reward_granted"] is True
        assert switched_on.should_show_ads(user=user, page_url="/x/") is False
        assert entitlements.effective_entitlements(user).perks.ad_free is True
        assert rewarded.user_is_ad_free(user) is True

        clock.advance(3 * 3600)
        assert switched_on.should_show_ads(user=user, page_url="/x/") is True

    def test_other_users_keep_seeing_ads(
        self, client_for, user, other, make_config, clock, switched_on
    ):
        config = make_config(reward_type=RewardedAdConfig.RewardType.AD_FREE)
        watch(client_for(user), config, clock)

        assert switched_on.should_show_ads(user=other, page_url="/x/") is True

    def test_anonymous_users_never_get_the_modifier_query(self, switched_on):
        assert switched_on.should_show_ads(user=AnonymousUser(), page_url="/") is True

    def test_validation_keeps_rewards_small_and_requires_a_placement_to_enable(
        self, placement
    ):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            RewardedAdConfig(
                name="a",
                is_enabled=True,
                reward_type="credits",
                placement=None,
            ).full_clean()
        with pytest.raises(ValidationError):
            RewardedAdConfig(
                name="b",
                placement=placement,
                reward_type="device_reset",
                reward_amount=9,
            ).full_clean()


class TestAnalyticsAndAdmin:
    def test_analytics_summarise_the_funnel(self, client_for, user, config, clock):
        client = client_for(user)
        watch(client, config, clock)
        start(client, config)
        RewardedAdView.objects.filter(status="started").update(is_flagged=True)

        stats = rewarded.analytics()

        assert (stats.starts, stats.completions, stats.flagged) == (2, 1, 1)
        assert stats.completion_rate == 50
        assert stats.by_type == {"credits": 5}

    def test_admin_pages_load_and_the_dashboard_says_not_configured(
        self, django_user_model
    ):
        admin_user = django_user_model.objects.create_superuser(
            username="ads_admin", email="ads_admin@example.com", password=PASSWORD
        )
        client = Client()
        client.force_login(admin_user)

        for name in (
            "ads_rewardedadconfig_changelist",
            "ads_rewardedadconfig_add",
            "ads_rewardedadview_changelist",
            "ads_rewardedadgrant_changelist",
            "ads_adssettings_changelist",
        ):
            assert client.get(reverse(f"admin:{name}")).status_code == 200, name
        dashboard = client.get(reverse("ads:dashboard"))

        assert dashboard.status_code == 200
        assert b"Not configured." in dashboard.content
        assert b"Preview ad formats" not in dashboard.content

    def test_admin_detail_pages_load(self, django_user_model, config, user):
        admin_user = django_user_model.objects.create_superuser(
            username="ads_admin2", email="ads_admin2@example.com", password=PASSWORD
        )
        view = RewardedAdView.objects.create(user=user, config=config, status="expired")
        client = Client()
        client.force_login(admin_user)

        assert (
            client.get(
                reverse("admin:ads_rewardedadconfig_change", args=[config.pk])
            ).status_code
            == 200
        )
        assert (
            client.get(
                reverse("admin:ads_rewardedadview_change", args=[view.pk])
            ).status_code
            == 200
        )

    def test_the_dashboard_lists_a_live_config(self, django_user_model, config):
        admin_user = django_user_model.objects.create_superuser(
            username="ads_admin3", email="ads_admin3@example.com", password=PASSWORD
        )
        client = Client()
        client.force_login(admin_user)

        dashboard = client.get(reverse("ads:dashboard"))

        assert b"Live" in dashboard.content
        assert config.name.encode() in dashboard.content
