"""Gamification: idempotent XP, badges, event wiring, leaderboard, pages, admin."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.events.bus import event_bus
from apps.gamification import api, services
from apps.gamification.constants import (
    BOUNTY_FULFILLED,
    COMMENT_APPROVED,
    FIRMWARE_APPROVED,
)
from apps.gamification.models import (
    Badge,
    Level,
    UserBadge,
    XPRule,
    XPTransaction,
)

pytestmark = pytest.mark.django_db
PASSWORD = "Pass-12345!"


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def make_user(django_user_model):
    def build(name: str, **extra):
        return django_user_model.objects.create_user(
            username=name,
            email=f"{name}@example.com",
            password=PASSWORD,
            email_verified_at=timezone.now(),
            **extra,
        )

    return build


@pytest.fixture
def alice(make_user):
    return make_user("alice")


def approved_event(user, firmware_id="7", requests=()):
    return {
        "uploader": user,
        "firmware_type": "official",
        "firmware_id": firmware_id,
        "fulfilled_request_ids": list(requests),
    }


def bonus_xp(user) -> int:
    return sum(
        XPTransaction.objects.filter(user=user, event_key="badge.earned").values_list(
            "xp", flat=True
        )
    )


def test_defaults_are_seeded_without_overwriting_admin_edits():
    assert XPRule.objects.filter(event_key=FIRMWARE_APPROVED).exists()
    assert Level.objects.count() == 5
    assert Badge.objects.filter(slug="first-upload").exists()
    XPRule.objects.filter(event_key=FIRMWARE_APPROVED).update(xp=99)
    services.sync_defaults()
    assert XPRule.objects.get(event_key=FIRMWARE_APPROVED).xp == 99


def test_award_is_idempotent_and_mirrors_reputation(alice):
    first = services.award(alice, FIRMWARE_APPROVED, key="k1")
    again = services.award(alice, FIRMWARE_APPROVED, key="k1")
    assert first is not None
    assert again is None
    assert XPTransaction.objects.filter(user=alice, key="k1").count() == 1
    assert services.total_xp(alice.pk) == first.xp + bonus_xp(alice)
    alice.refresh_from_db()
    assert alice.reputation.total_score == services.total_xp(alice.pk)
    assert alice.reputation.fw_contributions == 1


def test_disabled_unknown_and_inactive_are_refused_quietly(alice, make_user):
    XPRule.objects.filter(event_key=FIRMWARE_APPROVED).update(is_enabled=False)
    assert services.award(alice, FIRMWARE_APPROVED, key="a") is None
    assert services.award(alice, "nope.event", key="b") is None
    XPRule.objects.filter(event_key=FIRMWARE_APPROVED).update(is_enabled=True)
    ghost = make_user("ghost", is_active=False)
    assert services.award(ghost, FIRMWARE_APPROVED, key="c") is None
    assert not XPTransaction.objects.filter(key__in=["a", "b", "c"]).exists()


def test_daily_cap_limits_awards_per_day(alice):
    XPRule.objects.filter(event_key=COMMENT_APPROVED).update(daily_cap=2)
    results = [services.award(alice, COMMENT_APPROVED, key=f"c{i}") for i in range(4)]
    assert [r is not None for r in results] == [True, True, False, False]
    old = XPTransaction.objects.get(key="c0")
    XPTransaction.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(days=2)
    )
    assert services.award(alice, COMMENT_APPROVED, key="c9") is not None


def test_badges_are_earned_once_with_bonus_xp_and_a_notification(alice):
    from apps.users.models import Notification

    services.award(alice, FIRMWARE_APPROVED, key="k1")
    held = UserBadge.objects.filter(user=alice).values_list("badge__slug", flat=True)
    assert "first-upload" in held
    bonus = XPTransaction.objects.get(
        key__startswith="badge:", reference="first-upload"
    )
    assert bonus.xp == Badge.objects.get(slug="first-upload").xp_bonus
    assert (
        Notification.objects.filter(
            recipient=alice, title__contains="First upload"
        ).count()
        == 1
    )
    services.award(alice, FIRMWARE_APPROVED, key="k2")
    assert UserBadge.objects.filter(user=alice, badge__slug="first-upload").count() == 1


def test_badge_bonus_xp_can_unlock_xp_badges_in_the_same_pass(alice):
    Badge.objects.filter(slug="first-upload").update(xp_bonus=600)
    services.award(alice, FIRMWARE_APPROVED, key="k1")
    held = set(
        UserBadge.objects.filter(user=alice).values_list("badge__slug", flat=True)
    )
    assert {"first-upload", "rising-star"} <= held


def test_streak_counts_consecutive_days(alice):
    services.award(alice, FIRMWARE_APPROVED, key="d1")
    streak = alice.streak
    assert streak.count == 1
    streak.last_active = timezone.localdate() - timedelta(days=1)
    streak.save()
    services.award(alice, FIRMWARE_APPROVED, key="d2")
    streak.refresh_from_db()
    assert (streak.count, streak.longest) == (2, 2)
    services.award(alice, FIRMWARE_APPROVED, key="d3")
    streak.refresh_from_db()
    assert streak.count == 2


def test_firmware_approved_event_rewards_the_uploader_once(alice):
    event_bus.publish(FIRMWARE_APPROVED, approved_event(alice, requests=[11, 12]))
    event_bus.publish(FIRMWARE_APPROVED, approved_event(alice, requests=[11, 12]))
    events = list(
        XPTransaction.objects.filter(user=alice).values_list("event_key", flat=True)
    )
    assert events.count(FIRMWARE_APPROVED) == 1
    assert events.count("firmware.request_fulfilled") == 2
    assert {"first-upload", "request-hunter"} <= set(
        UserBadge.objects.filter(user=alice).values_list("badge__slug", flat=True)
    )


def test_firmware_approved_without_an_uploader_is_ignored():
    event_bus.publish(FIRMWARE_APPROVED, approved_event(None))
    assert not XPTransaction.objects.exists()


def test_published_blog_posts_earn_xp_once_and_only_when_they_earn_rewards(alice):
    payload = {"post_id": 5, "author": alice, "earns_rewards": True}
    event_bus.publish("blog.post_published", payload)
    event_bus.publish("blog.post_published", payload)
    event_bus.publish(
        "blog.post_published", {**payload, "post_id": 6, "earns_rewards": False}
    )
    event_bus.publish("blog.post_published", {**payload, "post_id": 7, "author": None})
    keys = list(
        XPTransaction.objects.filter(user=alice).values_list("event_key", flat=True)
    )
    assert keys == ["blog.post_published"]


def test_wallet_credit_events_for_referrals_and_bounties_earn_xp(alice):
    for event_key, key in (
        ("referral.verified", "ref-1"),
        ("referral.verified", "ref-1"),
        (BOUNTY_FULFILLED, "b-1"),
        ("shop.something", "x"),
    ):
        event_bus.publish(
            "wallet.credited",
            {"user_id": alice.pk, "event_key": event_key, "key": key},
        )
    keys = (
        XPTransaction.objects.filter(user=alice)
        .exclude(event_key="badge.earned")
        .values_list("event_key", flat=True)
    )
    assert sorted(keys) == ["bounty.fulfilled", "referral.rewarded"]


def test_generic_award_event_validates_its_payload(alice):
    event_bus.publish(
        "gamification.award_xp",
        {"user_id": alice.pk, "event_key": COMMENT_APPROVED, "key": "g1"},
    )
    event_bus.publish("gamification.award_xp", {"user_id": alice.pk, "key": "g2"})
    event_bus.publish(
        "gamification.award_xp",
        {"user_id": alice.pk, "event_key": "made.up", "key": "g3"},
    )
    assert list(
        XPTransaction.objects.filter(user=alice, key__startswith="g").values_list(
            "key", flat=True
        )
    ) == ["g1"]


def test_approved_comment_earns_xp_once_and_pending_does_not(alice):
    from apps.blog.models import Post, PostStatus
    from apps.comments.models import Comment

    post = Post.objects.create(
        title="Post",
        body="<p>x</p>",
        author=alice,
        status=PostStatus.PUBLISHED,
        publish_at=timezone.now() - timedelta(minutes=1),
    )
    pending = Comment.objects.create(
        post=post, user=alice, body="Hello", status=Comment.Status.PENDING
    )
    assert not XPTransaction.objects.filter(user=alice).exists()
    pending.status = Comment.Status.APPROVED
    pending.save()
    pending.save()
    assert (
        XPTransaction.objects.filter(user=alice, event_key=COMMENT_APPROVED).count()
        == 1
    )


def test_comment_handler_ignores_anonymous_and_deleted():
    from apps.gamification.handlers import on_comment_saved

    on_comment_saved(
        instance=SimpleNamespace(status="approved", is_deleted=False, user_id=None),
        sender=object,
        created=True,
    )
    on_comment_saved(
        instance=SimpleNamespace(status="approved", is_deleted=True, user_id=1),
        sender=object,
        created=True,
    )
    assert not XPTransaction.objects.exists()


def test_badge_credits_are_paid_through_the_wallet_once(
    alice, django_capture_on_commit_callbacks
):
    from apps.wallet.api import balance

    Badge.objects.filter(slug="first-upload").update(credits=7)
    with django_capture_on_commit_callbacks(execute=True):
        services.award(alice, FIRMWARE_APPROVED, key="k1")
    assert balance(alice) == 7
    with django_capture_on_commit_callbacks(execute=True):
        services.award(alice, FIRMWARE_APPROVED, key="k2")
    assert balance(alice) == 7


def test_leaderboard_ranks_hides_private_and_inactive_users(make_user):
    from apps.users.profile_models import Profile

    top, mid, hidden, ghost = (make_user(n) for n in ("top", "mid", "hidden", "ghost"))
    for user, count in ((top, 3), (mid, 1), (hidden, 5), (ghost, 4)):
        for i in range(count):
            services.award(user, FIRMWARE_APPROVED, key=f"{user.pk}-{i}")
    Profile.objects.create(user=hidden, is_public=False)
    get_user_model().objects.filter(pk=ghost.pk).update(is_active=False)
    cache.clear()
    rows = services.leaderboard("all")
    assert [r.username for r in rows] == ["top", "mid"]
    assert [r.rank for r in rows] == [1, 2]
    assert rows[0].level
    XPTransaction.objects.filter(user=mid).update(
        created_at=timezone.now() - timedelta(days=20)
    )
    cache.clear()
    assert [r.username for r in services.leaderboard("week")] == ["top"]
    assert {r.username for r in services.leaderboard("month")} == {"top", "mid"}


def test_public_pages_render_and_progress_needs_login(client, alice):
    assert client.get(reverse("gamification:badges")).status_code == 200
    page = client.get("/leaderboard/").content.decode()
    assert "Nobody is ranked yet" in page
    assert client.get(reverse("gamification:progress")).status_code == 302
    services.award(alice, FIRMWARE_APPROVED, key="p1")
    cache.clear()
    client.force_login(alice)
    badges = client.get("/community/badges/").content.decode()
    assert "First upload" in badges
    assert "Earned" in badges
    progress = client.get("/community/progress/").content.decode()
    assert "Level 1" in progress
    board = client.get("/leaderboard/?period=week").content.decode()
    assert "alice" in board
    assert "(you)" in board
    assert client.get("/leaderboard/?period=bogus").status_code == 200


def test_user_badges_tag_renders_badges_and_hides_for_empty_users(alice, make_user):
    from django.template import Context, Template

    template = Template("{% load gamification_tags %}{% user_badges person %}")
    empty = template.render(Context({"person": make_user("blank")}))
    assert "badge-chip" not in empty
    assert "XP" not in empty
    services.award(alice, FIRMWARE_APPROVED, key="t1")
    html = template.render(Context({"person": alice}))
    assert "First upload" in html
    assert "XP" in html
    assert template.render(Context({"person": None})).strip() == ""


def test_api_contract(alice):
    entry = api.award(alice, FIRMWARE_APPROVED, key="api-1")
    assert entry is not None
    assert api.total_xp(alice) >= entry.xp
    assert api.level_of(alice) is not None
    assert "First upload" in [b.name for b in api.badges_of(alice)]


@pytest.fixture
def staff_client(db) -> Client:
    admin = get_user_model().objects.create_superuser(
        username="gam-admin", email="gam-admin@example.com", password=PASSWORD
    )
    client = Client()
    client.force_login(admin)
    return client


def test_staff_can_award_a_badge_by_hand_through_the_admin(staff_client, alice):
    badge = Badge.objects.get(slug="week-streak")
    response = staff_client.post(
        reverse("admin:gamification_userbadge_add"),
        {"user": alice.pk, "badge": badge.pk},
    )
    assert response.status_code == 302
    assert UserBadge.objects.get(user=alice, badge=badge).awarded_manually is True
    assert XPTransaction.objects.filter(
        user=alice, event_key="badge.earned", reference="week-streak"
    ).exists()
    again = staff_client.post(
        reverse("admin:gamification_userbadge_add"),
        {"user": alice.pk, "badge": badge.pk},
    )
    assert again.status_code == 200
    assert UserBadge.objects.filter(user=alice, badge=badge).count() == 1


def test_xp_rules_are_editable_but_not_creatable(staff_client):
    assert staff_client.get(reverse("admin:gamification_xprule_add")).status_code == 403
    rule = XPRule.objects.get(event_key=FIRMWARE_APPROVED)
    change = staff_client.get(
        reverse("admin:gamification_xprule_change", args=[rule.pk])
    )
    assert change.status_code == 200
    delete = staff_client.post(
        reverse("admin:gamification_xprule_delete", args=[rule.pk]), {"post": "yes"}
    )
    assert delete.status_code == 403
