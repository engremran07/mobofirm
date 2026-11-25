"""Blog contribution access types: Free/Featured for all, Paid for approved sellers."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import BlogSettings, Category, Post, PostAccess, PostStatus
from apps.blog.services import contributions, workflow
from apps.core.admin_base import ModelAdmin
from apps.core.app_service import AppService
from apps.users.models import Notification

pytestmark = pytest.mark.django_db

BODY = "<p>A careful write-up of how to recover a bricked phone, step by step.</p>"


@pytest.fixture
def make_user(django_user_model: Any) -> Callable[..., Any]:
    def build(username: str, *, staff: bool = False) -> Any:
        user = django_user_model.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="Pass-12345!",
            email_verified_at=timezone.now(),
            is_staff=staff,
        )
        user.profile_completed = True
        user.save(update_fields=["profile_completed"])
        return user

    return build


@pytest.fixture
def sellers(monkeypatch: pytest.MonkeyPatch) -> set[int]:
    """Approved marketplace sellers: ``sellers.add(user.pk)``."""
    approved: set[int] = set()
    real = AppService.export

    def export(cls: Any, label: str, name: str) -> Any:
        if (label, name) == ("marketplace", "is_active_seller"):
            return lambda user: user.pk in approved
        return real(label, name)

    monkeypatch.setattr(AppService, "export", classmethod(export))
    return approved


@pytest.fixture
def category() -> Category:
    return Category.objects.create(name="Guides", slug="guides")


@pytest.fixture(autouse=True)
def open_contributions() -> None:
    config = BlogSettings.get_solo()
    config.enable_blog = True
    config.allow_user_blog_posts = True
    config.contributions_per_day = 10
    config.save()


@pytest.fixture
def member(make_user: Callable[..., Any]) -> Any:
    return make_user("tier_member")


@pytest.fixture
def member_client(member: Any) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture
def reviewer(make_user: Callable[..., Any]) -> Any:
    user = make_user("tier_reviewer", staff=True)
    user.user_permissions.add(Permission.objects.get(codename="change_post"))
    return user


def form(category: Category, **extra: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "title": "Recover a bricked phone",
        "body": BODY,
        "category": category.pk,
    }
    values.update(extra)
    return values


def post_to(client: Client, category: Category, **extra: Any) -> Any:
    return client.post(reverse("blog:contribute"), form(category, **extra))


class TestForm:
    def test_non_sellers_are_offered_free_and_featured_only(
        self, member_client: Client
    ) -> None:
        page = member_client.get(reverse("blog:contribute")).content.decode()

        assert '<option value="free"' in page and '<option value="featured"' in page
        assert '<option value="paid"' not in page
        assert 'name="proposed_price"' not in page
        assert "Only approved sellers can offer paid posts" in page

    def test_the_become_a_seller_link_is_kept(self, member_client: Client) -> None:
        page = member_client.get(reverse("blog:contribute")).content.decode()

        assert (
            f'<a href="{reverse("marketplace:become_seller")}">Become a seller</a>'
            in page
        )

    def test_sellers_also_see_paid_and_the_price_field(
        self, member: Any, member_client: Client, sellers: set[int]
    ) -> None:
        sellers.add(member.pk)

        page = member_client.get(reverse("blog:contribute")).content.decode()

        assert '<option value="paid"' in page
        assert 'name="proposed_price"' in page
        assert "Become a seller" not in page


class TestNonSellers:
    @pytest.mark.parametrize("access", ["free", "featured"])
    def test_free_and_featured_are_accepted(
        self, member_client: Client, category: Category, access: str
    ) -> None:
        response = post_to(member_client, category, access=access)

        post = Post.objects.get()
        assert response.status_code == 302
        assert (post.access, post.proposed_price, post.price) == (access, None, None)
        assert post.status == PostStatus.IN_REVIEW

    def test_a_blank_access_defaults_to_free(
        self, member_client: Client, category: Category
    ) -> None:
        post_to(member_client, category)

        assert Post.objects.get().access == PostAccess.FREE

    def test_a_forged_paid_value_is_rejected(
        self, member_client: Client, category: Category
    ) -> None:
        response = post_to(
            member_client, category, access="paid", proposed_price="9.00"
        )

        assert response.status_code == 200
        assert "not one of the available choices" in response.content.decode()
        assert Post.objects.count() == 0

    def test_the_service_refuses_paid_even_if_the_form_is_bypassed(
        self, member: Any, category: Category
    ) -> None:
        data = contributions.ContributionData(
            title="Bypass attempt",
            body=BODY,
            category=category,
            access=PostAccess.PAID,
            proposed_price=Decimal("9.00"),
        )

        with pytest.raises(contributions.ContributionError):
            contributions.submit(member, data)
        assert Post.objects.count() == 0

    def test_a_featured_post_carries_no_price(
        self, member_client: Client, category: Category
    ) -> None:
        post_to(member_client, category, access="featured", proposed_price="4.00")

        post = Post.objects.get()
        assert (post.access, post.proposed_price) == ("featured", None)


class TestSellers:
    @pytest.fixture(autouse=True)
    def seller(self, member: Any, sellers: set[int]) -> None:
        sellers.add(member.pk)

    def test_paid_needs_a_price(
        self, member_client: Client, category: Category
    ) -> None:
        response = post_to(member_client, category, access="paid")

        assert response.status_code == 200
        assert "proposed price above zero" in response.content.decode()
        assert Post.objects.count() == 0

    @pytest.mark.parametrize("amount", ["0", "-2"])
    def test_paid_needs_a_price_above_zero(
        self, member_client: Client, category: Category, amount: str
    ) -> None:
        response = post_to(
            member_client, category, access="paid", proposed_price=amount
        )

        assert response.status_code == 200
        assert Post.objects.count() == 0

    def test_a_paid_post_keeps_the_proposed_price_but_has_no_approved_price(
        self, member_client: Client, category: Category
    ) -> None:
        post_to(member_client, category, access="paid", proposed_price="12.50")

        post = Post.objects.get()
        assert post.access == PostAccess.PAID
        assert post.proposed_price == Decimal("12.50")
        assert post.price is None

    def test_a_free_post_drops_a_stray_price(
        self, member_client: Client, category: Category
    ) -> None:
        post_to(member_client, category, access="free", proposed_price="3.00")

        assert Post.objects.get().proposed_price is None

    def test_publishing_approves_the_proposed_price(
        self, member_client: Client, category: Category, reviewer: Any
    ) -> None:
        post_to(member_client, category, access="paid", proposed_price="12.50")
        post = Post.objects.get()

        workflow.publish(post, user=reviewer)

        post.refresh_from_db()
        assert (post.price, post.price_approved_by) == (Decimal("12.50"), reviewer)


class TestReviewerDowngrade:
    @pytest.fixture
    def paid_post(self, member: Any, sellers: set[int], category: Category) -> Post:
        sellers.add(member.pk)
        result = contributions.submit(
            member,
            contributions.ContributionData(
                title="Paid walkthrough",
                body=BODY,
                category=category,
                access=PostAccess.PAID,
                proposed_price=Decimal("8.00"),
            ),
        )
        return result.post

    def test_a_reviewer_lowers_paid_to_featured_with_a_reason(
        self,
        member: Any,
        paid_post: Post,
        reviewer: Any,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        with django_capture_on_commit_callbacks(execute=True):
            contributions.lower_access(
                reviewer, paid_post, PostAccess.FEATURED, "Not worth a fee."
            )

        paid_post.refresh_from_db()
        assert paid_post.access == PostAccess.FEATURED
        assert (paid_post.price, paid_post.proposed_price) == (None, None)
        assert paid_post.status == PostStatus.IN_REVIEW
        notice = Notification.objects.get(
            recipient=member, title__startswith="The access type"
        )
        assert "Paid to Featured" in notice.message
        assert "Not worth a fee." in notice.message

    def test_the_author_sees_the_change_in_my_contributions(
        self, member_client: Client, paid_post: Post, reviewer: Any
    ) -> None:
        contributions.lower_access(reviewer, paid_post, PostAccess.FREE, "Too basic.")

        page = member_client.get(reverse("contributions:blog")).content.decode()

        assert "Access lowered from Paid to Free: Too basic." in page
        assert ">Free<" in page

    def test_a_reason_is_required(self, paid_post: Post, reviewer: Any) -> None:
        with pytest.raises(ValidationError):
            workflow.lower_access(
                paid_post, user=reviewer, access=PostAccess.FREE, reason="  "
            )

    def test_raising_or_keeping_the_access_is_refused(
        self, paid_post: Post, reviewer: Any
    ) -> None:
        for access in (PostAccess.PAID, "gold"):
            with pytest.raises(ValidationError):
                workflow.lower_access(
                    paid_post, user=reviewer, access=access, reason="No."
                )

    def test_only_moderators_may_lower_access(
        self, member: Any, paid_post: Post
    ) -> None:
        from django.core.exceptions import PermissionDenied

        with pytest.raises(PermissionDenied):
            workflow.lower_access(
                paid_post, user=member, access=PostAccess.FREE, reason="Mine."
            )

    def test_a_live_post_cannot_change_access(
        self, paid_post: Post, reviewer: Any
    ) -> None:
        workflow.publish(paid_post, user=reviewer)

        with pytest.raises(ValidationError):
            workflow.lower_access(
                paid_post, user=reviewer, access=PostAccess.FREE, reason="Late."
            )

    def test_the_console_offers_the_action(self) -> None:
        from django.contrib import admin

        post_admin = admin.site.get_model_admin(Post)
        assert isinstance(post_admin, ModelAdmin)
        assert "lower_access" in post_admin.actions_detail


class TestMyContributionsChips:
    def test_each_item_shows_its_access_and_price(
        self,
        member: Any,
        member_client: Client,
        sellers: set[int],
        category: Category,
    ) -> None:
        sellers.add(member.pk)
        for title, access, price in (
            ("A free post", PostAccess.FREE, None),
            ("A featured post", PostAccess.FEATURED, None),
            ("A paid post", PostAccess.PAID, Decimal("6.00")),
        ):
            contributions.submit(
                member,
                contributions.ContributionData(
                    title=title,
                    body=BODY,
                    category=category,
                    access=access,
                    proposed_price=price,
                ),
            )

        page = member_client.get(reverse("contributions:blog")).content.decode()

        for label in ("Free", "Featured", "Paid"):
            assert f">{label}</span>" in page, label
        assert "Price awaiting review (you proposed USD 6.00)" in page
