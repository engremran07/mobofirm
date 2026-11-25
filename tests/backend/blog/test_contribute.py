"""Contribute a blog post: submit, validate, throttle, edit, withdraw and review."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.blog.admin import PostAdmin
from apps.blog.models import BlogSettings, Category, Post, PostSource, PostStatus
from apps.blog.services import contributions, workflow
from apps.blog.sitemaps import PublishedBlogPostsSitemap
from apps.tags.models import Tag
from apps.users.models import Notification

pytestmark = pytest.mark.django_db

BODY = "<p>A careful write-up of how to recover a bricked phone, step by step.</p>"


@pytest.fixture
def make_user(django_user_model: Any) -> Callable[..., Any]:
    def build(username: str, *, verified: bool = True, staff: bool = False) -> Any:
        user = django_user_model.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="Pass-12345!",
            email_verified_at=timezone.now() if verified else None,
            is_staff=staff,
        )
        user.profile_completed = True
        user.save(update_fields=["profile_completed"])
        return user

    return build


@pytest.fixture
def member(make_user: Callable[..., Any]) -> Any:
    return make_user("contributor")


@pytest.fixture
def member_client(member: Any) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture
def reviewer(make_user: Callable[..., Any]) -> Any:
    user = make_user("reviewer", staff=True)
    user.user_permissions.add(Permission.objects.get(codename="change_post"))
    return user


@pytest.fixture
def category() -> Category:
    return Category.objects.create(name="Guides", slug="guides")


@pytest.fixture(autouse=True)
def open_contributions() -> BlogSettings:
    config = BlogSettings.get_solo()
    config.enable_blog = True
    config.allow_user_blog_posts = True
    config.contributions_per_day = 3
    config.save()
    return config


def form(category: Category, **extra: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "title": "Recover a bricked phone",
        "summary": "A short recovery guide.",
        "body": BODY,
        "category": category.pk,
    }
    values.update(extra)
    return values


def submit(client: Client, category: Category, **extra: Any) -> Any:
    return client.post(reverse("blog:contribute"), form(category, **extra))


class TestAccess:
    def test_anonymous_visitors_are_sent_to_sign_in(self, client: Client) -> None:
        for response in (
            client.get(reverse("blog:contribute")),
            client.post(reverse("blog:contribute"), {"title": "x"}),
        ):
            assert response.status_code == 302
            assert "login" in response["Location"]
        assert Post.objects.count() == 0

    def test_unverified_members_are_sent_to_verify_their_email(
        self, make_user: Callable[..., Any], category: Category
    ) -> None:
        client = Client()
        client.force_login(make_user("unverified", verified=False))

        page = client.get(reverse("blog:contribute"))
        posted = submit(client, category)

        for response in (page, posted):
            assert response.status_code == 302
            assert response["Location"] == reverse("users:verify_email_required")
        assert Post.objects.count() == 0

    def test_the_page_is_closed_when_member_posting_is_off(
        self,
        member_client: Client,
        open_contributions: BlogSettings,
        category: Category,
    ) -> None:
        open_contributions.allow_user_blog_posts = False
        open_contributions.save()

        page = member_client.get(reverse("blog:contribute"))
        posted = submit(member_client, category)

        assert page.status_code == posted.status_code == 403
        assert "Blog contributions are closed" in page.content.decode()
        assert Post.objects.count() == 0

    def test_the_page_is_hidden_when_the_blog_is_disabled(
        self, member_client: Client, open_contributions: BlogSettings
    ) -> None:
        open_contributions.enable_blog = False
        open_contributions.save()

        assert member_client.get(reverse("blog:contribute")).status_code == 404

    def test_a_missing_csrf_token_is_refused(
        self, member: Any, category: Category
    ) -> None:
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(member)

        response = strict.post(reverse("blog:contribute"), form(category))

        assert response.status_code == 403
        assert Post.objects.count() == 0


class TestSubmit:
    def test_the_page_renders_the_form_with_a_csrf_token(
        self, member_client: Client
    ) -> None:
        page = member_client.get(reverse("blog:contribute"))

        body = page.content.decode()
        assert page.status_code == 200
        assert "Contribute a blog post" in body
        assert "csrfmiddlewaretoken" in body
        for name in ("title", "summary", "body", "category", "tags", "access"):
            assert f'name="{name}"' in body
        assert 'name="proposed_price"' not in body
        assert 'hx-get="/blog/contribute/title-matches/"' in body
        assert 'hx-target="#contribution-title-matches"' in body
        assert 'aria-live="polite"' in body

    def test_a_published_similar_title_does_not_block_member_review(
        self, member: Any, member_client: Client, category: Category
    ) -> None:
        title = "Recover a bricked phone"
        Post.objects.create(
            title=title,
            body=BODY,
            author=member,
            category=category,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now(),
        )

        response = submit(member_client, category, title=title)

        contribution = Post.objects.get(
            content_source=PostSource.COMMUNITY_CONTRIBUTION
        )
        assert response.status_code == 302
        assert contribution.title == title
        assert contribution.status == PostStatus.IN_REVIEW

    def test_a_valid_post_enters_review_and_redirects(
        self,
        member: Any,
        member_client: Client,
        category: Category,
        reviewer: Any,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        tag = Tag.objects.create(name="Recovery", slug="recovery", is_curated=True)

        with django_capture_on_commit_callbacks(execute=True):
            response = submit(member_client, category, tags=[tag.pk])

        post = Post.objects.get()
        assert response.status_code == 302
        assert response["Location"] == reverse("contributions:blog")
        assert (post.author, post.status) == (member, PostStatus.IN_REVIEW)
        assert post.is_community_contribution
        assert not post.is_published and post.published_at is None
        assert list(post.tags.all()) == [tag]
        assert post.revisions.count() == 1
        notice = Notification.objects.get(recipient=reviewer)
        assert "Recover a bricked phone" in notice.message
        assert not Notification.objects.filter(recipient=member).exists()

    def test_only_curated_tags_can_be_attached(
        self, member_client: Client, category: Category
    ) -> None:
        hidden = Tag.objects.create(name="Draft tag", slug="draft-tag")

        response = submit(member_client, category, tags=[hidden.pk])

        assert response.status_code == 200
        assert "not one of the available choices" in response.content.decode()
        assert Post.objects.count() == 0

    def test_the_member_cannot_publish_or_price_their_post(
        self, member_client: Client, category: Category
    ) -> None:
        submit(
            member_client,
            category,
            status="published",
            is_published="on",
            price="9.00",
            featured="on",
        )

        post = Post.objects.get()
        assert post.status == PostStatus.IN_REVIEW
        assert (post.access, post.price, post.featured) == ("free", None, False)
        assert post.published_at is None

        posted = member_client.post(
            reverse("blog:post_workflow", kwargs={"slug": post.slug}),
            {"action": "publish"},
        )

        post.refresh_from_db()
        assert posted.status_code == 302
        assert post.status == PostStatus.IN_REVIEW
        assert not Post.objects.live().exists()

    def test_validation_errors_keep_the_input_and_create_nothing(
        self, member_client: Client, category: Category
    ) -> None:
        response = member_client.post(
            reverse("blog:contribute"),
            {"title": "No", "body": "<p>short</p>", "summary": "kept summary"},
        )

        page = response.content.decode()
        assert response.status_code == 200
        assert "kept summary" in page
        assert "field-error" in page
        for expected in ("at least 3 characters", "at least 40 characters", "required"):
            assert expected in page
        assert Post.objects.count() == 0

    def test_hostile_markup_is_sanitised(
        self, member_client: Client, category: Category
    ) -> None:
        hostile = (
            "<p>Visible text that is long enough for the reviewers to read.</p>"
            "<script>alert('x')</script><img src=x onerror=alert(1)>"
            '<a href="javascript:alert(1)" onclick="steal()">click</a>'
            "<iframe src='https://evil.example'></iframe>"
            "<style>body{display:none}</style>"
        )

        submit(member_client, category, body=hostile, title="<b>Bold</b> title here")

        stored = Post.objects.get().body.lower()
        for fragment in ("<script", "onerror", "javascript:", "onclick", "<iframe"):
            assert fragment not in stored
        assert "<img" not in stored and "<style" not in stored
        assert "visible text" in stored

    def test_a_double_post_creates_one_contribution_and_one_notification(
        self,
        member_client: Client,
        category: Category,
        reviewer: Any,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        with django_capture_on_commit_callbacks(execute=True):
            first = submit(member_client, category)
            second = submit(member_client, category)

        assert first.status_code == second.status_code == 302
        assert first["Location"] == second["Location"]
        assert Post.objects.count() == 1
        assert Notification.objects.filter(recipient=reviewer).count() == 1

    def test_a_resubmission_after_a_decision_is_a_new_contribution(
        self,
        member: Any,
        member_client: Client,
        category: Category,
        reviewer: Any,
    ) -> None:
        submit(member_client, category)
        contributions.reject(reviewer, Post.objects.get(), "Needs more detail.")

        submit(member_client, category)

        assert Post.objects.count() == 2

    def test_the_daily_limit_blocks_extra_posts(
        self,
        member_client: Client,
        category: Category,
        open_contributions: BlogSettings,
    ) -> None:
        open_contributions.contributions_per_day = 2
        open_contributions.save()

        for number in range(2):
            submit(member_client, category, title=f"Contribution number {number}")
        blocked = submit(member_client, category, title="Contribution number 3")

        assert blocked.status_code == 200
        assert "2 posts per day" in blocked.content.decode()
        assert Post.objects.count() == 2

    def test_the_limit_is_per_member(
        self,
        make_user: Callable[..., Any],
        member_client: Client,
        category: Category,
        open_contributions: BlogSettings,
    ) -> None:
        open_contributions.contributions_per_day = 1
        open_contributions.save()
        other = Client()
        other.force_login(make_user("second-member"))

        submit(member_client, category)
        response = submit(other, category, title="Another member's post")

        assert response.status_code == 302
        assert Post.objects.count() == 2


class TestEditAndWithdraw:
    @pytest.fixture
    def pending(self, member_client: Client, category: Category) -> Post:
        submit(member_client, category)
        return Post.objects.get()

    def test_the_author_edits_while_the_post_waits(
        self, member_client: Client, pending: Post, category: Category
    ) -> None:
        url = reverse("blog:contribute_edit", kwargs={"slug": pending.slug})
        assert member_client.get(url).status_code == 200

        response = member_client.post(
            url, form(category, title="Recover a bricked phone safely")
        )

        pending.refresh_from_db()
        assert response.status_code == 302
        assert pending.title == "Recover a bricked phone safely"
        assert pending.status == PostStatus.IN_REVIEW
        assert pending.version == 2

    def test_the_author_withdraws_and_can_resubmit(
        self,
        member_client: Client,
        pending: Post,
        category: Category,
        reviewer: Any,
    ) -> None:
        withdrawn = member_client.post(
            reverse("blog:contribute_withdraw", kwargs={"slug": pending.slug})
        )

        pending.refresh_from_db()
        assert withdrawn.status_code == 302
        assert pending.status == PostStatus.DRAFT
        assert pending.review_note == ""
        with pytest.raises(ValidationError):
            workflow.reject(pending, user=reviewer, reason="Too late")

        member_client.post(
            reverse("blog:contribute_edit", kwargs={"slug": pending.slug}),
            form(category, summary="Now with a clearer summary."),
        )

        pending.refresh_from_db()
        assert pending.status == PostStatus.IN_REVIEW

    def test_other_members_cannot_see_edit_or_withdraw_it(
        self, make_user: Callable[..., Any], pending: Post, category: Category
    ) -> None:
        stranger = Client()
        stranger.force_login(make_user("stranger"))
        edit = reverse("blog:contribute_edit", kwargs={"slug": pending.slug})
        withdraw = reverse("blog:contribute_withdraw", kwargs={"slug": pending.slug})

        assert stranger.get(edit).status_code == 404
        assert stranger.post(edit, form(category)).status_code == 404
        assert stranger.post(withdraw).status_code == 404
        pending.refresh_from_db()
        assert pending.status == PostStatus.IN_REVIEW

    def test_a_published_contribution_can_no_longer_be_edited(
        self,
        member_client: Client,
        pending: Post,
        reviewer: Any,
        category: Category,
    ) -> None:
        workflow.publish(pending, user=reviewer)
        edit = reverse("blog:contribute_edit", kwargs={"slug": pending.slug})

        response = member_client.post(edit, form(category, title="Sneaky rewrite"))

        pending.refresh_from_db()
        assert response.status_code == 302
        assert pending.title == "Recover a bricked phone"


class TestReview:
    @pytest.fixture
    def pending(self, member_client: Client, category: Category) -> Post:
        submit(member_client, category)
        return Post.objects.get()

    def test_a_reviewer_rejects_with_a_reason_the_author_reads(
        self,
        member: Any,
        member_client: Client,
        pending: Post,
        reviewer: Any,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        with django_capture_on_commit_callbacks(execute=True):
            contributions.reject(reviewer, pending, "Please add the exact model codes.")

        pending.refresh_from_db()
        assert (pending.status, pending.is_published) == (PostStatus.DRAFT, False)
        assert pending.review_note == "Please add the exact model codes."
        notice = Notification.objects.get(recipient=member)
        assert "exact model codes" in notice.message
        listing = member_client.get(reverse("contributions:blog")).content.decode()
        assert "Sent back for changes" in listing
        assert "Please add the exact model codes." in listing

        assert pending.category is not None
        edited = member_client.post(
            reverse("blog:contribute_edit", kwargs={"slug": pending.slug}),
            form(pending.category, summary="Model codes added."),
        )

        pending.refresh_from_db()
        assert edited.status_code == 302
        assert (pending.status, pending.review_note) == (PostStatus.IN_REVIEW, "")

    def test_rejecting_needs_a_reason_and_a_moderator(
        self, pending: Post, reviewer: Any, member: Any
    ) -> None:
        with pytest.raises(ValidationError):
            workflow.reject(pending, user=reviewer, reason="   ")
        with pytest.raises(PermissionDenied):
            workflow.reject(pending, user=member, reason="I reject my own post")
        pending.refresh_from_db()
        assert pending.status == PostStatus.IN_REVIEW

    def test_approval_publishes_it_and_it_enters_the_sitemap(
        self, member: Any, pending: Post, reviewer: Any
    ) -> None:
        assert pending not in PublishedBlogPostsSitemap().items()

        workflow.publish(pending, user=reviewer)

        pending.refresh_from_db()
        assert pending.status == PostStatus.PUBLISHED and pending.is_live
        assert pending.published_by == reviewer
        assert pending in PublishedBlogPostsSitemap().items()

        contributions.notify_author_published(pending)
        assert Notification.objects.filter(
            recipient=member, title="Your blog contribution was published"
        ).exists()

    def test_pending_contributions_are_not_public(
        self, client: Client, pending: Post
    ) -> None:
        assert client.get(pending.get_absolute_url()).status_code == 404
        assert pending not in PublishedBlogPostsSitemap().items()

    def test_the_review_queue_lists_contributions_with_the_console_actions(
        self, client: Client, pending: Post, reviewer: Any
    ) -> None:
        from django.contrib import admin

        post_admin = admin.site.get_model_admin(Post)
        assert isinstance(post_admin, PostAdmin)
        assert "reject" in post_admin.actions_row
        assert "reject" in post_admin.actions_detail
        assert any(
            isinstance(item, tuple) and item[0] == "content_source"
            for item in post_admin.list_filter
        )
        client.force_login(reviewer)

        response = client.get(reverse("admin:blog_post_changelist"))

        assert response.status_code == 200
        assert pending.title in response.content.decode()
