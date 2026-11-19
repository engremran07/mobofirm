"""Contract between "My contributions" (users) and the blog and wallet public APIs."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.blog import api as blog_api
from apps.blog.models import BlogSettings, Category, Post, PostStatus
from apps.blog.reward_events import POST_PUBLISHED
from apps.core.models import AppRegistry
from apps.wallet import api as wallet_api

pytestmark = pytest.mark.django_db


@pytest.fixture
def member(django_user_model: Any) -> Any:
    user = django_user_model.objects.create_user(
        username="contract_member",
        email="contract_member@example.com",
        password="Pass-12345!",
        email_verified_at=timezone.now(),
    )
    user.profile_completed = True
    user.save(update_fields=["profile_completed"])
    return user


@pytest.fixture
def other(django_user_model: Any) -> Any:
    return django_user_model.objects.create_user(
        username="contract_other", email="contract_other@example.com"
    )


def post_for(author: Any, title: str, **extra: Any) -> Post:
    values: dict[str, Any] = {
        "title": title,
        "body": "<p>Body long enough for the contract test.</p>",
        "author": author,
    }
    values.update(extra)
    return Post.objects.create(**values)


class TestBlogContract:
    def test_contributions_are_the_members_own_non_automated_posts(
        self, member: Any, other: Any
    ) -> None:
        mine = post_for(member, "Mine in review", status=PostStatus.IN_REVIEW)
        post_for(member, "Automated", is_ai_generated=True)
        post_for(other, "Someone else")

        rows = blog_api.contributions_of(member)

        assert [row.title for row in rows] == ["Mine in review"]
        assert rows[0].status == "in_review" and mine.slug
        assert blog_api.contributions_of(None) == []

    def test_member_facing_states(self, member: Any) -> None:
        post_for(member, "Withdrawn draft")
        post_for(member, "Sent back", review_note="Add detail.")
        post_for(member, "Live", status=PostStatus.PUBLISHED)

        states = {row.title: row.status for row in blog_api.contributions_of(member)}

        assert states == {
            "Withdrawn draft": "withdrawn",
            "Sent back": "returned",
            "Live": "published",
        }

    def test_edit_links_only_exist_for_open_community_contributions(
        self, member: Any
    ) -> None:
        post_for(member, "Editorial draft")
        post_for(
            member,
            "Pending contribution",
            status=PostStatus.IN_REVIEW,
            is_community_contribution=True,
        )

        rows = {row.title: row for row in blog_api.contributions_of(member)}

        assert rows["Editorial draft"].edit_url == ""
        assert rows["Pending contribution"].edit_url.endswith("/edit/")
        assert rows["Pending contribution"].withdraw_url.endswith("/withdraw/")

    def test_contributions_open_follow_the_blog_settings(self, member: Any) -> None:
        config = BlogSettings.get_solo()
        config.enable_blog = True
        config.allow_user_blog_posts = False
        config.save()
        assert blog_api.contributions_open_for(member) is False

        config.allow_user_blog_posts = True
        config.save()
        assert blog_api.contributions_open_for(member) is True


class TestWalletContract:
    def test_rewards_earned_sums_only_the_requested_event_prefixes(
        self, member: Any
    ) -> None:
        entry = wallet_api.grant_reward(
            member, POST_PUBLISHED, key="contract:blog:1", reference="blog.post:1"
        )
        assert entry is not None

        assert wallet_api.rewards_earned(member, ("blog.",)) == entry.amount
        assert wallet_api.rewards_earned(member, ("firmware.",)) == 0
        assert wallet_api.rewards_earned(member, ()) == 0

    def test_the_hub_shows_the_credits_earned(self, member: Any) -> None:
        entry = wallet_api.grant_reward(
            member, POST_PUBLISHED, key="contract:blog:2", reference="blog.post:2"
        )
        assert entry is not None
        client = Client()
        client.force_login(member)

        page = client.get(reverse("contributions:hub")).content.decode()

        assert f"<strong data-reward-credits>{entry.amount}</strong>" in page

    def test_the_hub_works_without_the_wallet(self, member: Any) -> None:
        registry = AppRegistry.get_solo()
        registry.wallet_enabled = False
        registry.save()
        client = Client()
        client.force_login(member)

        response = client.get(reverse("contributions:hub"))

        assert response.status_code == 200
        assert "Credit rewards are not enabled" in response.content.decode()

    def test_the_hub_works_without_the_blog(self, member: Any) -> None:
        registry = AppRegistry.get_solo()
        registry.blog_enabled = False
        registry.save()
        Category.objects.create(name="Unused", slug="unused")
        client = Client()
        client.force_login(member)

        response = client.get(reverse("contributions:hub"))

        assert response.status_code == 200
        assert "You have not contributed a blog post yet" in response.content.decode()
